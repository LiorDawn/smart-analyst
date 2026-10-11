"""节点：compute（指标计算 · 纯确定性）。

职责（用户定稿：先算确切数据 → 分析交给子 agent）：
  本节点只做确定性计算（无 LLM）：
     - 原子指标    → 直接取数（无分组 → 单值；有分组 → 按维度行）
     - 派生指标    → base 指标值代入公式（安全四则求值；分组场景按维度键配对）
     - 跨期指标    → base 指标 current/compare 两段配对 → (cur-cmp)/cmp
  归因解释 / 数据缺口分析 → analysis_agent 子 agent（supervisor LLM 自主触发，
  简单问答不调，省 token）——本节点不内嵌 LLM。

输出：computed_metrics（list[dict]，replace）——每指标一条，带 type/formula/source_refs 可溯源。
输入：research_results（join 整理后）/ task（指标清单/维度）
安全：公式是受控资产（metric_dict 注册，写死）；safe_calc 用 ast 白名单求值，
      仅允许 base 指标名 + 四则运算 + 括号 + 数字，杜绝 eval 注入。
所属 Step：查数段 · 收敛版
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.conf.metric_dict import METRIC_DICT
from app.graph.state import AnalystState, TaskStatus, task_field
from app.utils.safe_calc import safe_calc

logger = logging.getLogger(__name__)


def _row_value(row: dict, dims: list[str]) -> float:
    """从 SQL 结果行提取数值（排除维度列）。行 = {维度列..., 字段列: 数值}。
    注意 MySQL 聚合返回 Decimal（非 int/float），必须显式兼容。"""
    for k, v in row.items():
        if k not in dims and isinstance(v, (int, float, Decimal)):
            return float(v)
    raise ValueError(f"行中无数值列: {row}")


def _result_rows(by_key: dict, metric: str, period: str, target: str | None = None) -> list[dict]:
    """按 (metric, period, target) 取结果行；base 查询 target_metric=父指标名。缺失 → 抛错（上层记 degraded）。"""
    r = by_key.get((metric, period, target))
    if r is None or r.get("degraded"):
        raise ValueError(f"缺失 {metric}[{period}] 结果（查询失败或未生成）")
    return r.get("rows") or []


def _calc_atomic(metric: str, m: dict, by_key: dict, dims: list[str]) -> dict:
    """原子指标：无分组 → 单值；有分组 → 维度行（保留维度键，供派生配对）。"""
    rows = _result_rows(by_key, metric, "current")
    refs = [by_key[(metric, "current", None)]["source_ref"]]
    if not dims:
        return {"metric": metric, "type": "atomic", "value": _row_value(rows[0], dims),
                "dims": [], "source_refs": refs, "degraded": False}
    out_rows = []
    for row in rows:
        out_rows.append({**{d: row[d] for d in dims if d in row}, "value": _row_value(row, dims)})
    return {"metric": metric, "type": "atomic", "rows": out_rows,
            "dims": dims, "source_refs": refs, "degraded": False}


def _calc_derived(metric: str, m: dict, by_key: dict, dims: list[str]) -> dict:
    """派生指标：base 值代入公式；分组场景按维度键配对逐行计算。"""
    formula = m["formula"]
    refs: list[str] = []
    if not dims:
        values = {}
        for base in m["base"]:
            rows = _result_rows(by_key, base, "current", metric)
            values[base] = _row_value(rows[0], dims)
            refs.append(by_key[(base, "current", metric)]["source_ref"])
        return {"metric": metric, "type": "derived", "formula": formula,
                "value": safe_calc(formula, values), "base_values": values,
                "dims": [], "source_refs": refs, "degraded": False}

    # 分组场景：各 base 按维度键建索引，逐键代入公式
    base_rows: dict[str, dict[str, dict]] = {}
    for base in m["base"]:
        rows = _result_rows(by_key, base, "current", metric)
        refs.append(by_key[(base, "current", metric)]["source_ref"])
        base_rows[base] = {tuple(r[d] for d in dims): r for r in rows if all(d in r for d in dims)}

    keys = set().union(*(set(b.keys()) for b in base_rows.values()))
    out_rows = []
    gaps: list[dict] = []  # 数据缺口：某维度键缺 base 数据 → 记录，不再静默吞掉
    for key in sorted(keys):
        values = {}
        missing: list[str] = []
        for base in m["base"]:
            row = base_rows[base].get(key)
            if row is None:
                missing.append(base)  # 该维度键缺某 base 数据 → 记录缺口
                continue
            values[base] = _row_value(row, dims)
        if missing:
            gaps.append({"key": key, "missing": missing})
            continue
        dim_row = {d: v for d, v in zip(dims, key)}
        out_rows.append({**dim_row, "value": safe_calc(formula, values)})
    return {"metric": metric, "type": "derived", "formula": formula, "rows": out_rows,
            "gaps": gaps, "dims": dims, "source_refs": refs, "degraded": not out_rows}


def _calc_cross(metric: str, m: dict, by_key: dict, dims: list[str]) -> dict:
    """跨期指标：base 的 current/compare 两段 → (cur-cmp)/cmp。"""
    base = m["base"]
    formula = m["formula"]
    cur_rows = _result_rows(by_key, base, "current", metric)
    cmp_rows = _result_rows(by_key, base, "compare", metric)
    refs = [by_key[(base, "current", metric)]["source_ref"], by_key[(base, "compare", metric)]["source_ref"]]
    if not dims:
        cur, cmp_ = _row_value(cur_rows[0], dims), _row_value(cmp_rows[0], dims)
        return {"metric": metric, "type": "cross_period", "formula": formula,
                "current": cur, "compare": cmp_, "change": safe_calc(formula, {"current": cur, "compare": cmp_}),
                "dims": [], "source_refs": refs, "degraded": False}
    cur_idx = {tuple(r[d] for d in dims): _row_value(r, dims) for r in cur_rows if all(d in r for d in dims)}
    cmp_idx = {tuple(r[d] for d in dims): _row_value(r, dims) for r in cmp_rows if all(d in r for d in dims)}
    out_rows = []
    gaps: list[dict] = []  # 数据缺口：维度键仅单期存在 → 记录，不再静默吞掉
    for key in sorted(set(cur_idx) & set(cmp_idx)):
        cur, cmp_ = cur_idx[key], cmp_idx[key]
        dim_row = {d: v for d, v in zip(dims, key)}
        out_rows.append({**dim_row, "current": cur, "compare": cmp_,
                         "change": safe_calc(formula, {"current": cur, "compare": cmp_})})
    # 差集：仅当前期有（对比期缺失）/ 仅对比期有（当前期缺失）
    for key in sorted(set(cur_idx) - set(cmp_idx)):
        gaps.append({"key": key, "missing": ["compare"]})
    for key in sorted(set(cmp_idx) - set(cur_idx)):
        gaps.append({"key": key, "missing": ["current"]})
    return {"metric": metric, "type": "cross_period", "formula": formula, "rows": out_rows,
            "gaps": gaps, "dims": dims, "source_refs": refs, "degraded": not out_rows}


async def compute(state: AnalystState) -> dict:
    """确定性计算 → computed_metrics（replace）。归因由 analysis_agent 子 agent 负责。"""
    results = state.get("research_results") or []
    metrics = task_field(state, "metrics", [])
    dims = task_field(state, "dims", [])

    by_key = {}
    for r in results:
        by_key[(r["metric"], r.get("period", "current"), r.get("target_metric"))] = r

    out: list[dict] = []
    for metric in metrics:
        m = METRIC_DICT.get(metric)
        if m is None:
            out.append({"metric": metric, "type": "unknown", "degraded": True, "error": "指标未定义"})
            continue
        try:
            if m["type"] == "atomic":
                out.append(_calc_atomic(metric, m, by_key, dims))
            elif m["type"] == "derived":
                out.append(_calc_derived(metric, m, by_key, dims))
            elif m["type"] == "cross_period":
                out.append(_calc_cross(metric, m, by_key, dims))
            else:
                out.append({"metric": metric, "type": m["type"], "degraded": True, "error": "类型未知"})
        except Exception as e:  # noqa: BLE001 —— 单指标计算失败降级，不中断整批
            out.append({"metric": metric, "type": m["type"], "degraded": True, "error": str(e)})

    return {"computed_metrics": out, "status": TaskStatus.RUNNING.value}
