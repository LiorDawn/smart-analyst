"""节点：planner（计划生成 · 按需漏斗版）。

功能：TaskSchema → QueryItem 查询计划（规则拼 SQL，收敛版《查数链路设计方案》§二/§四）。

按需漏斗（L1→L4，能规则的绝不 LLM）：
  L1 结构化命中：metric ∈ METRIC_DICT      → 规则拼 SQL（90% 场景，0 token，不碰 RAG）
  L2 别名预检：  metric ∈ SYNONYM_DICT     → 归一化 canonical → 规则拼 SQL（补 5%）
  L3 向量 RAG：  前两层都失败 → 口径库检索 + LLM 提取定义 → 回写字典（下次 L1 命中）
  L4 口径不明：  仍失败 → errors 记录（不猜，clarify/跳过该指标）

规则生成（build_query_items）：
  原子指标   → 一条 SQL（聚合字段 + 时间窗 + filter + GROUP BY dims）；task.compare 存在 → 追加对比段
  派生指标   → 拆 base 原子查询（target_metric=父指标，analysis 据此计算）
  跨期指标   → 分析期（current）+ 对比期（compare）两段（依赖 task.compare 区间）

安全：字段名来自受控定义表；时间窗做日期格式校验（防注入），非法 → errors 不拼 SQL。
输出：plan（list[QueryItem] dict，replace）、errors（add）、status
所属 Step：M5 查数段 · 收敛版
"""

from __future__ import annotations

import logging
import re
import threading

from app.conf.metric_dict import METRIC_DICT, SYNONYM_DICT
from app.entities.planner_schema import MetricDef, QueryItem
from app.graph.state import AnalystState, TaskStatus, task_field

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ────────────────────────── L1/L2：规则解析 ──────────────────────────

def _resolve_metric(metric: str) -> tuple[dict, str] | None:
    """L1 结构化命中 / L2 别名预检 → (指标定义, canonical 名)；均未命中 → None。"""
    m = METRIC_DICT.get(metric)
    if m:
        return m, metric
    canonical = SYNONYM_DICT.get(metric)
    if canonical:
        m = METRIC_DICT.get(canonical)
        if m:
            return m, canonical
    return None


# ── 并发安全：METRIC_DICT 跨请求共享，"检查-回写"复合操作必须互斥 ──
_dict_lock = threading.Lock()  # 保护 L1/L2 读 + L3 回写（防 TOCTOU：同时 miss → 重复 L3 RAG + 互相覆盖）


def _resolve_metric_locked(metric: str) -> tuple[dict, str] | None:
    """加锁读 L1/L2：与回写互斥，杜绝『检查时不存在 → 回写时已被并发写入』竞态。"""
    with _dict_lock:
        return _resolve_metric(metric)


def _writeback_locked(metric: str, m: dict) -> tuple[dict, bool]:
    """加锁回写 L3 结果：double-check + first-write-wins。

    返回 (最终生效的定义, 本次是否真正写入)。并发请求同时解析同一新指标时，
    先到者写入，后到者复用先到结果——避免重复 L3 RAG/LLM 调用与结果互相覆盖。
    """
    with _dict_lock:
        existing = _resolve_metric(metric)  # double-check：并发请求可能已回写成功
        if existing is not None:
            return existing[0], False
        METRIC_DICT[metric] = m
        return m, True


# ────────────────────────── L3：RAG 口径兜底 ──────────────────────────

async def _resolve_metric_via_rag(metric: str) -> dict | None:
    """L3：口径库检索 → LLM 提取指标定义 → 返回 METRIC_DICT 条目。

    全程异常安全：RAG 挂 / LLM 失败 / 无命中 → 返回 None（走 L4），绝不阻断主链。
    LLM 只做「文档 → 结构化口径」提取，不决定是否触发检索（那是规则的事）。
    """
    try:
        from app.clients.llm_client import llm_client_manager
        from app.rag.retriever import retriever

        if llm_client_manager.client is None:
            llm_client_manager.init()

        hits = await retriever.retrieve(f"指标口径定义：{metric}", top_k=5)
        if hits.is_empty:
            return None

        structured_llm = llm_client_manager.client.with_structured_output(MetricDef)
        resp = await structured_llm.ainvoke([
            {
                "role": "system",
                "content": (
                    "你是口径解析器：从口径文档中提取指标定义，输出 METRIC_DICT 同构条目"
                    "（type/table/field/agg/filter/formula/base）。"
                    "规则：文档中没有该指标 → found=False，绝不编造字段或公式。"
                ),
            },
            {
                "role": "user",
                "content": f"指标：{metric}\n\n口径文档：\n{hits.context}",
            },
        ])
        return resp.defn if resp.found and resp.defn else None
    except Exception as e:  # noqa: BLE001 —— L3 是可选兜底，任何失败都降级 L4
        logger.warning("L3 口径兜底不可用（%s）: %s", metric, e)
        return None


# ────────────────────────── 规则生成：SQL ──────────────────────────

def _build_sql(m: dict, dims: list[str], time_range: dict) -> str:
    """原子指标 → 一条 SQL（字段名来自受控定义表，时间窗校验后拼接）。"""
    start, end = time_range["start"], time_range["end"]
    if not (_DATE_RE.fullmatch(start) and _DATE_RE.fullmatch(end)):  # 时间窗校验（YYYY-MM-DD），防非法值进 SQL
        raise ValueError(f"非法日期区间: {time_range}")

    agg_expr = f"COUNT(DISTINCT {m['field']})" if m["agg"] == "COUNT_DISTINCT" else f"{m['agg']}({m['field']})"
    select = f"SELECT {agg_expr} AS {m['field']}"
    if dims:
        select += ", " + ", ".join(dims)  # GROUP BY 的维度列必须 SELECT 出来，compute 才能读到分组值
    where = f" WHERE date BETWEEN '{start}' AND '{end}'"
    if m.get("filter"):
        where += f" AND {m['filter']}"
    group = f" GROUP BY {', '.join(dims)}" if dims else ""
    return f"{select} FROM {m['table']}{where}{group}"


# ────────────────────────── 规则生成：QueryItem ──────────────────────────

def _atomic_items(
    m: dict,
    canonical: str,
    dims: list[str],
    time_range: dict | None,
    compare: dict | None,
    target: str | None = None,
) -> list[QueryItem]:
    """原子指标：分析期一条；task.compare 存在 → 追加对比段（同一聚合，换时间窗）。"""
    if time_range is None:
        return []
    items = [
        QueryItem(
            type="sql",
            query=_build_sql(m, dims, time_range),
            metric=canonical,
            target_metric=target,
            period="current",
        )
    ]
    if compare:
        items.append(
            QueryItem(
                type="sql",
                query=_build_sql(m, dims, compare),
                metric=canonical,
                target_metric=target,
                period="compare",
            )
        )
    return items


def _derived_items(m: dict, canonical: str, dims: list[str], time_range: dict | None, compare: dict | None) -> list[QueryItem]:
    """派生指标：拆 base 原子查询（target_metric=父指标；base 非原子 → 跳过）。"""
    items: list[QueryItem] = []
    for base in m.get("base", []):
        bm = METRIC_DICT.get(base)
        if bm is None or bm["type"] != "atomic":
            continue  # base 未定义/非原子 → 该 base 不拆（口径问题由 analysis 兜底标注）
        items += _atomic_items(bm, base, dims, time_range, compare, target=canonical)
    return items


def _cross_period_items(m: dict, canonical: str, dims: list[str], time_range: dict | None, compare: dict | None) -> list[QueryItem]:
    """跨期指标：base 指标的分析期 + 对比期两段（compare 区间来自 task.compare）。"""
    bm = METRIC_DICT.get(m["base"])
    if bm is None or bm["type"] != "atomic":
        return []
    return _atomic_items(bm, m["base"], dims, time_range, compare, target=canonical)


# ────────────────────────── 主流程 ──────────────────────────

async def planner(state: AnalystState) -> dict:
    task = state.get("task") or {}  # 意图契约（回写 metrics 归一化用）
    metrics = task_field(state, "metrics", [])
    time_range = task_field(state, "time_range")
    dims = task_field(state, "dims", [])
    compare = task_field(state, "compare")
    errors: list[dict] = []

    if not metrics:
        return {
            "plan": [],
            "errors": [{"node": "planner", "msg": "metrics 为空，无法生成计划", "step": "plan"}],
            "status": TaskStatus.RUNNING.value,
        }

    items: list[QueryItem] = []
    for i, metric in enumerate(metrics):
        resolved = _resolve_metric_locked(metric)  # L1 / L2（同步、零成本、加锁读）
        if resolved is None:
            m = await _resolve_metric_via_rag(metric)  # L3（RAG 兜底，异常安全）
            if m is None:
                errors.append({"node": "planner", "msg": f"指标 {metric} 口径未定义", "step": "plan"})
                continue
            canonical = metric
            m, _ = _writeback_locked(metric, m)  # 并发安全回写：下次 L1 直接命中（first-write-wins）
        else:
            m, canonical = resolved
            task["metrics"][i] = canonical  # L2 别名归一化回写（下游用 canonical 名）

        try:
            if m["type"] == "atomic":
                items += _atomic_items(m, canonical, dims, time_range, compare)
            elif m["type"] == "derived":
                items += _derived_items(m, canonical, dims, time_range, compare)
            elif m["type"] == "cross_period":
                if compare is None:
                    errors.append(
                        {"node": "planner", "msg": f"跨期指标 {canonical} 缺少对比期（task.compare 为空）", "step": "plan"}
                    )
                    continue
                items += _cross_period_items(m, canonical, dims, time_range, compare)
            else:
                errors.append({"node": "planner", "msg": f"指标 {canonical} 类型未知: {m['type']}", "step": "plan"})
        except ValueError as e:
            errors.append({"node": "planner", "msg": str(e), "step": "plan"})
            continue

    return {"plan": [i.model_dump() for i in items], "task": task, "errors": errors, "status": TaskStatus.RUNNING.value}
