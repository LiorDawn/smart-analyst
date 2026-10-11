"""字段能力字典（物理层 · 只存指标层看不到的信息）。

收敛原则（回应企业设计：元数据自动采集字段、口径绑定指标、字段层只留能力信息）：
  - table / field / type / agg / filter 的唯一权威 = METRIC_DICT（指标定义表），这里不重复；
  - 字段层只存 METRIC_DICT 没有的信息：
      dims：该字段支持的下钻维度（GROUP BY 白名单，planner validate_dims 用）
      desc：字段视角说明（quick 答疑 / 文档生成用）
  - 血缘（字段 → 指标）由 build_field_index() 从 METRIC_DICT 反推，不手写第二份。

用途：
  - planner validate_dims：校验请求的 GROUP BY 是否合法（非法维度记 errors，不静默忽略）
  - quick 答疑：字段是什么
  - analysis 归因：哪个表的哪个字段影响哪些指标（FIELD_INDEX）
"""

from __future__ import annotations

# ========== 字段能力字典（键 = "表.字段"） ==========
# dims = 支持的下钻维度白名单；desc = 字段视角说明（口径的唯一权威仍在 METRIC_DICT）

FIELD_DICT: dict[str, dict] = {
    # ── fact_sales 订单事实表 ──
    "fact_sales.amount": {"dims": ["category", "channel"], "desc": "订单实付金额"},
    "fact_sales.order_id": {"dims": [], "desc": "订单ID（去重计数）"},
    "fact_sales.customer_id": {"dims": [], "desc": "客户ID（去重计数）"},
    "fact_sales.is_new_customer": {"dims": [], "desc": "是否新客（1=新客）"},
    "fact_sales.category": {"dims": [], "desc": "商品品类（维度字段）"},
    "fact_sales.channel": {"dims": [], "desc": "渠道（直播/搜索/推荐/自然）"},
    "fact_sales.date": {"dims": [], "desc": "业务日期（时间窗过滤）"},
    "fact_sales.status": {"dims": [], "desc": "订单状态（cancelled 剔除）"},

    # ── fact_traffic 流量事实表 ──
    "fact_traffic.visitors": {"dims": ["channel"], "desc": "访客数 UV（去重）"},
    "fact_traffic.clicks": {"dims": ["channel"], "desc": "点击量"},
    "fact_traffic.impressions": {"dims": ["channel"], "desc": "曝光量"},

    # ── fact_refund 退款事实表 ──
    "fact_refund.amount": {"dims": [], "desc": "退款额"},

    # ── fact_marketing 投放事实表 ──
    "fact_marketing.spend": {"dims": ["channel"], "desc": "广告花费"},

    # ── dim_product 商品维度表 ──
    "dim_product.stock": {"dims": ["category"], "desc": "库存量"},
}


def build_field_index(metric_dict: dict | None = None) -> dict[str, list[str]]:
    """反向血缘：(表.字段) → 使用它的指标（从 METRIC_DICT 反推，不手写第二份）。"""
    if metric_dict is None:
        from app.conf.metric_dict import METRIC_DICT

        metric_dict = METRIC_DICT

    index: dict[str, list[str]] = {}

    def _add(table: str, field: str, metric_name: str) -> None:
        key = f"{table}.{field}"
        if metric_name not in index.setdefault(key, []):
            index[key].append(metric_name)

    for name, m in metric_dict.items():
        if m["type"] == "atomic":
            _add(m["table"], m["field"], name)
        elif m["type"] in ("derived", "cross_period"):
            bases = m.get("base", [])
            if isinstance(bases, str):
                bases = [bases]
            for base in bases:
                bm = metric_dict.get(base)
                if bm and bm["type"] == "atomic":
                    _add(bm["table"], bm["field"], name)
    return index


FIELD_INDEX: dict[str, list[str]] = build_field_index()


def validate_dims(metric: str, dims: list[str]) -> list[str]:
    """planner 用：校验请求维度是否被字段支持（如 stock 仅支持 category）。

    表/字段信息从 METRIC_DICT 取（唯一权威），维度白名单从 FIELD_DICT 取。
    返回不支持的维度列表（空 = 全部合法）。
    """
    from app.conf.metric_dict import METRIC_DICT

    m = METRIC_DICT.get(metric)
    if m is None or m["type"] != "atomic":
        return []  # 派生/跨期维度校验由 analysis 统一处理
    field = FIELD_DICT.get(f"{m['table']}.{m['field']}")
    if field is None:
        return []
    supported = set(field.get("dims", []))
    return [d for d in dims if d not in supported]
