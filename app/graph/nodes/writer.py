"""节点：writer（报告撰写 · 数据固定字段 + AI 分析一处 LLM 总结）。

定位（用户定稿·收敛版）：
  - 「一、指标结果」：computed_metrics 确切数据，代码固定渲染（不编造，失败标注）
  - 「二、AI 分析」：supervisor 的综合分析总结（LLM 对前面所有结论的总结输出，整段原文）
  不再调 LLM（supervisor 已输出完整分析，避免重复总结烧 token）。

输入：computed_metrics（analysis 写）/ analysis_insights（supervisor 写，取 supervisor 条目）/ task / messages
输出：report（str，replace）
所属 Step：S4
"""

from __future__ import annotations

from app.graph.state import AnalystState, last_user_text, task_field


def _fmt_value(v) -> str:
    """数值格式：int 千分位；float 保留 2 位去尾零；其余原样。"""
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _render_metric(item: dict) -> list[str]:
    """单条指标结果 → markdown 行（固定字段，不编造）。"""
    metric = item.get("metric", "?")
    mtype = item.get("type", "?")
    if item.get("degraded"):
        return [f"- **{metric}**（{mtype}）：⚠ 计算失败 —— {item.get('error', '未知')}"]
    if "value" in item:
        return [f"- **{metric}**（{mtype}）：{_fmt_value(item['value'])}"]
    if "change" in item:
        return [
            f"- **{metric}**（{mtype}）：本期 {_fmt_value(item.get('current'))} → 上期 "
            f"{_fmt_value(item.get('compare'))}，变动 {_fmt_value(item.get('change'))}"
        ]
    if "rows" in item:
        dims = item.get("dims") or []
        head = " | ".join([*dims, "数值"])
        sep = " | ".join(["---"] * (len(dims) + 1))
        lines = ["", f"**{metric}**（{mtype}）按 {'、'.join(dims)} 分组：", head, sep]
        for row in item["rows"]:
            cells = [_fmt_value(row.get(d)) for d in dims]
            cells.append(_fmt_value(row.get("value", row.get("change", ""))))
            lines.append(" | ".join(cells))
        return lines
    return [f"- **{metric}**（{mtype}）：{item}"]


async def writer(state: AnalystState) -> dict:
    """报告：指标结果（固定字段）+ AI 分析（supervisor 综合总结）。"""
    results = state.get("computed_metrics") or []
    insights = state.get("analysis_insights") or []
    task = state.get("task") or {}
    question = last_user_text(state.get("messages") or [])

    lines: list[str] = [
        "# SmartAnalyst 经营分析报告",
        "",
        f"> 问题：{question}",
    ]
    time_range = task.get("time_range")
    if time_range:
        lines.append(f"> 分析期：{time_range.get('start')} ~ {time_range.get('end')}")
    metrics = task_field(state, "metrics", [])
    if metrics:
        lines.append(f"> 指标：{'、'.join(metrics)}")

    # 一、指标结果：确切数据，代码固定渲染
    lines += ["", "## 一、指标结果", ""]
    for item in results:
        lines += _render_metric(item)

    # 二、AI 分析：supervisor 的综合分析总结（LLM 对前面结论的总结输出）
    lines += ["", "## 二、AI 分析", ""]
    super_insights = [i for i in insights if i.get("agent") == "supervisor"]
    if super_insights:
        for insight in super_insights:
            text = insight.get("text") or insight.get("summary") or ""
            if text:
                lines += ["", text]
    else:
        lines.append("（未触发综合分析，仅指标结果）")

    return {"report": "\n".join(lines)}
