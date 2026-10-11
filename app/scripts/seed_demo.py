"""演示数据脚本：建事实表 + 造数（对齐 metric_dict 的表/字段）。

目的：在没有真实业务库时，把 MySQL 填上可查的数据，让 planner L1 规则拼 SQL →
query_executor 真实查询 → compute 计算 → writer 出报告的整条链路可跑。

表（对齐 app/conf/metric_dict.py）：
  fact_sales     成交事实（amount/order_id/customer_id/is_new_customer/status + 维度 category/channel）
  fact_traffic   流量事实（visitors/clicks/impressions + 维度 channel）
  fact_refund    退款事实（amount）
  fact_marketing 投放事实（spend + 维度 channel）
  dim_product    商品维度（category + stock）

造数要点（可复现：固定 random seed）：
  - 近 DAYS=35 天，每天每品类×渠道生成订单
  - 制造"分析点"：最近一周数码类目 GMV 明显下滑（环比 -25%），家居平稳，直播渠道高增长
  - 退款少量、流量随机、投放与 gmv 弱相关

用法（项目根下）：
  python -m app.scripts.seed_demo          # 幂等：先 DROP 再建（演示数据重跑无妨）
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import date, timedelta

from sqlalchemy import text

from app.clients.mysql_client import mysql_client_manager

DAYS = 35
CATEGORIES = ["数码", "家居", "服饰", "美妆"]
CHANNELS = ["直营", "分销", "直播"]
STATUSES = ["paid", "paid", "paid", "cancelled"]  # 25% 取消，口径过滤用

DDL = """
-- 成交事实表（对齐 metric_dict.fact_sales）
CREATE TABLE IF NOT EXISTS fact_sales (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  date DATE NOT NULL,
  category VARCHAR(32) NOT NULL,
  channel VARCHAR(32) NOT NULL,
  order_id VARCHAR(64) NOT NULL,
  customer_id VARCHAR(64) NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  is_new_customer TINYINT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT 'paid',
  PRIMARY KEY (id),
  KEY idx_dt (date), KEY idx_category (category), KEY idx_channel (channel)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 流量事实表
CREATE TABLE IF NOT EXISTS fact_traffic (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  date DATE NOT NULL,
  channel VARCHAR(32) NOT NULL,
  visitors INT NOT NULL, clicks INT NOT NULL, impressions INT NOT NULL,
  PRIMARY KEY (id), KEY idx_dt (date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 退款事实表
CREATE TABLE IF NOT EXISTS fact_refund (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  date DATE NOT NULL, order_id VARCHAR(64) NOT NULL, amount DECIMAL(12,2) NOT NULL,
  PRIMARY KEY (id), KEY idx_dt (date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 投放事实表
CREATE TABLE IF NOT EXISTS fact_marketing (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  date DATE NOT NULL, channel VARCHAR(32) NOT NULL, spend DECIMAL(12,2) NOT NULL,
  PRIMARY KEY (id), KEY idx_dt (date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 商品维度表
CREATE TABLE IF NOT EXISTS dim_product (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  category VARCHAR(32) NOT NULL, stock INT NOT NULL,
  PRIMARY KEY (id), UNIQUE KEY uk_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _category_gmv_factor(category: str, day_offset: int) -> float:
    """品类日成交额基准 × 时间衰减因子（制造数码最近一周下滑的分析点）。"""
    base = {"数码": 62000.0, "家居": 41000.0, "服饰": 33000.0, "美妆": 27000.0}[category]
    if category == "数码" and day_offset >= DAYS - 7:  # 最近一周数码 -25%
        base *= 0.75
    if category == "家居":
        base *= 1.0  # 平稳
    if category == "直播":
        pass  # 渠道因子在下方处理
    return base


def _gen_rows(days: int):
    """生成全部行的 SQL VALUES 片段（一次性插入，幂等前提是 DROP）。"""
    rows_sales, rows_traffic, rows_refund, rows_marketing = [], [], [], []
    random.seed(42)
    today = date.today()
    order_pool = []
    for offset in range(days):
        d = today - timedelta(days=days - 1 - offset)
        for cat in CATEGORIES:
            base = _category_gmv_factor(cat, offset)
            for ch in CHANNELS:
                ch_factor = {"直营": 1.0, "分销": 0.8, "直播": 0.5}.get(ch, 1.0)
                if ch == "直播" and d >= today - timedelta(days=7):  # 直播近一周高增长
                    ch_factor = 0.8
                orders = max(2, int(base * ch_factor / 450))
                for _ in range(orders):
                    oid = f"O{d.strftime('%Y%m%d')}{random.randint(10000, 99999)}"
                    cid = f"C{random.randint(1000, 9999)}"
                    amount = round(random.uniform(80, 900), 2)
                    status = random.choice(STATUSES)
                    rows_sales.append(
                        f"('{d}', '{cat}', '{ch}', '{oid}', '{cid}', {amount}, "
                        f"{random.randint(0, 1)}, '{status}')"
                    )
                    order_pool.append((d, oid, amount, status))
                    if status == "paid" and random.random() < 0.06:  # 6% 已支付订单退款
                        rows_refund.append(f"('{d}', '{oid}', {round(amount * 0.4, 2)})")
        for ch in CHANNELS:
            visitors = int(1800 + random.randint(0, 1200) + (600 if ch == "直播" and d >= today - timedelta(days=7) else 0))
            rows_traffic.append(
                f"('{d}', '{ch}', {visitors}, {int(visitors * random.uniform(0.05, 0.12))}, {int(visitors * random.uniform(3, 6))})"
            )
            rows_marketing.append(
                f"('{d}', '{ch}', {round(random.uniform(3000, 9000) * ({'直营': 1.0, '分销': 0.7, '直播': 1.2}[ch]), 2)})"
            )
    return rows_sales, rows_traffic, rows_refund, rows_marketing


async def main() -> None:
    # 0. 确保 meta 库存在（sql.sql 未执行过时兜底建库）
    import pymysql
    from app.conf.app_config import app_config

    db = app_config.db
    conn = pymysql.connect(host=db.host, port=db.port, user=db.user, password=db.password, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS meta "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()

    mysql_client_manager.init()
    engine = mysql_client_manager.engine
    async with engine.begin() as conn:
        for t in ("fact_sales", "fact_traffic", "fact_refund", "fact_marketing", "dim_product"):
            await conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        for stmt in DDL.split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))

        # 维度表（库存）
        stocks = [("数码", 1200), ("家居", 3400), ("服饰", 2100), ("美妆", 2600)]
        await conn.execute(
            text("INSERT INTO dim_product (category, stock) VALUES " + ", ".join(f"('{c}', {s})" for c, s in stocks))
        )

        # 事实表批量插入
        sales, traffic, refund, marketing = _gen_rows(DAYS)
        await conn.execute(text("INSERT INTO fact_sales (date, category, channel, order_id, customer_id, amount, is_new_customer, status) VALUES " + ", ".join(sales)))
        await conn.execute(text("INSERT INTO fact_traffic (date, channel, visitors, clicks, impressions) VALUES " + ", ".join(traffic)))
        await conn.execute(text("INSERT INTO fact_refund (date, order_id, amount) VALUES " + ", ".join(refund)))
        await conn.execute(text("INSERT INTO fact_marketing (date, channel, spend) VALUES " + ", ".join(marketing)))

    # 汇总核对（幂等抽验：重跑可复现）
    async with engine.connect() as conn:
        for t in ("fact_sales", "fact_traffic", "fact_refund", "fact_marketing", "dim_product"):
            r = await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))
            print(f"  {t}: {r.scalar()} 行")
        r = await conn.execute(text(
            "SELECT category, SUM(amount) gmv FROM fact_sales "
            "WHERE status != 'cancelled' AND date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY category"
        ))
        print("\n近 7 天品类 GMV（数码应明显低于其他）：")
        for row in r.fetchall():
            print(f"  {row[0]}: {row[1]:,.0f}")
    await engine.dispose()
    print("\n[seed_demo 完成] 事实表已建 + 演示数据已造")


if __name__ == "__main__":
    asyncio.run(main())
