"""指标定义表 + 同义词表（查数段核心资产 · 规则拼 SQL 的数据源）。

对齐《SmartAnalyst-M5 查数链路设计方案》§三：
  - 指标是受控资产：口径 / 表字段 / 公式固化在此，新增指标 = 注册一条，规则即可查；
  - 原子指标直接拼 SQL；派生指标拆 base 原子查询 → analysis 计算；
  - 跨期指标生成两段（current + compare）配对计算；
  - precompute=True 表示高频指标，后续 C5 走预计算/缓存。

类型约定：
  atomic      直接查事实表字段（table/field/agg/filter）
  derived     原子 + 公式（base: list[str]，analysis 代码计算）
  cross_period 跨期涨跌（base: str，生成两段时间窗）
"""

from __future__ import annotations

# ========== 指标定义表（首批 16 个，按电商经营分析实际建模） ==========

METRIC_DICT: dict[str, dict] = {
    # ── 原子指标（直接查事实表字段，规则拼 SQL） ──────────────────────────
    "gmv": {
        "type": "atomic",
        "table": "fact_sales",
        "field": "amount",
        "agg": "SUM",
        "filter": "status != 'cancelled'",  # 口径：剔除取消订单
        "precompute": True,
        "desc": "成交额（剔除取消订单）",
    },
    "orders": {
        "type": "atomic",
        "table": "fact_sales",
        "field": "order_id",
        "agg": "COUNT_DISTINCT",
        "filter": "status != 'cancelled'",
        "precompute": True,
        "desc": "成交订单数",
    },
    "visitors": {
        "type": "atomic",
        "table": "fact_traffic",
        "field": "visitors",
        "agg": "SUM",
        "filter": None,
        "precompute": True,
        "desc": "访客数",
    },
    "customers": {
        "type": "atomic",
        "table": "fact_sales",
        "field": "customer_id",
        "agg": "COUNT_DISTINCT",
        "filter": "status != 'cancelled'",
        "precompute": False,
        "desc": "成交客户数",
    },
    "new_customers": {
        "type": "atomic",
        "table": "fact_sales",
        "field": "customer_id",
        "agg": "COUNT_DISTINCT",
        "filter": "is_new_customer = 1",
        "precompute": False,
        "desc": "新客数",
    },
    "refund_amount": {
        "type": "atomic",
        "table": "fact_refund",
        "field": "amount",
        "agg": "SUM",
        "filter": None,
        "precompute": False,
        "desc": "退款额",
    },
    "ad_spend": {
        "type": "atomic",
        "table": "fact_marketing",
        "field": "spend",
        "agg": "SUM",
        "filter": None,
        "precompute": False,
        "desc": "广告花费",
    },
    "clicks": {
        "type": "atomic",
        "table": "fact_traffic",
        "field": "clicks",
        "agg": "SUM",
        "filter": None,
        "precompute": False,
        "desc": "点击量",
    },
    "impressions": {
        "type": "atomic",
        "table": "fact_traffic",
        "field": "impressions",
        "agg": "SUM",
        "filter": None,
        "precompute": False,
        "desc": "曝光量",
    },
    "stock": {
        "type": "atomic",
        "table": "dim_product",
        "field": "stock",
        "agg": "SUM",
        "filter": None,
        "precompute": False,
        "dims_supported": ["category"],
        "desc": "库存量（仅支持品类维度）",
    },

    # ── 派生指标（base + formula，analysis 代码计算） ─────────────────────
    "conversion_rate": {
        "type": "derived",
        "base": ["orders", "visitors"],
        "formula": "orders / visitors",
        "precompute": True,
        "desc": "转化率 = 成交订单数 / 访客数",
    },
    "avg_order_value": {
        "type": "derived",
        "base": ["gmv", "orders"],
        "formula": "gmv / orders",
        "precompute": False,
        "desc": "客单价 = 成交额 / 订单数",
    },
    "refund_rate": {
        "type": "derived",
        "base": ["refund_amount", "gmv"],
        "formula": "refund_amount / gmv",
        "precompute": False,
        "desc": "退款率 = 退款额 / 成交额",
    },
    "roi": {
        "type": "derived",
        "base": ["gmv", "ad_spend"],
        "formula": "gmv / ad_spend",
        "precompute": False,
        "desc": "投放回报 = 成交额 / 广告花费（多表：fact_sales × fact_marketing）",
    },
    "ctr": {
        "type": "derived",
        "base": ["clicks", "impressions"],
        "formula": "clicks / impressions",
        "precompute": False,
        "desc": "点击率 = 点击量 / 曝光量",
    },

    # ── 跨期指标（两段时间窗配对计算） ───────────────────────────────────
    "gmv_wow": {
        "type": "cross_period",
        "base": "gmv",
        "period": "week",
        "formula": "(current - compare) / compare",
        "precompute": False,
        "desc": "GMV 周环比涨跌幅",
    },
    "orders_wow": {
        "type": "cross_period",
        "base": "orders",
        "period": "week",
        "formula": "(current - compare) / compare",
        "precompute": False,
        "desc": "订单量周环比涨跌幅",
    },
    "gmv_mom": {
        "type": "cross_period",
        "base": "gmv",
        "period": "month",
        "formula": "(current - compare) / compare",
        "precompute": False,
        "desc": "GMV 月环比涨跌幅",
    },
}

# ========== 同义词表（L2 别名预检：字符串匹配，零成本） ==========
# 用户口语/别名 → canonical 指标名；命中即归一化，不触发 RAG。

SYNONYM_DICT: dict[str, str] = {
    "成交额": "gmv",
    "销售额": "gmv",
    "gross sales": "gmv",
    "成交金额": "gmv",
    "订单数": "orders",
    "单量": "orders",
    "订单量": "orders",
    "访客数": "visitors",
    "uv": "visitors",
    "客户数": "customers",
    "成交客户": "customers",
    "新客": "new_customers",
    "新客户数": "new_customers",
    "退款额": "refund_amount",
    "广告花费": "ad_spend",
    "投放花费": "ad_spend",
    "点击量": "clicks",
    "曝光量": "impressions",
    "转化率": "conversion_rate",
    "转化": "conversion_rate",
    "客单价": "avg_order_value",
    "客单": "avg_order_value",
    "退款率": "refund_rate",
    "roi": "roi",
    "投放回报": "roi",
    "点击率": "ctr",
    "ctr": "ctr",
    "周环比": "gmv_wow",
    "环比上周": "gmv_wow",
    "月环比": "gmv_mom",
    "环比上月": "gmv_mom",
}
