"""节点：reviewer（质量门 · 已实现）。

功能：规则校验报告每个数字可溯源（computed_metrics 的 source_refs 非空）、数据完整
      （无 degraded 缺失）→ 产出 report_ok + report_issues。
      校验器自身异常 → 放行 + warn（不阻塞主链路，错误是数据）。
输入：computed_metrics / report
输出：report_ok（bool，replace）、report_issues（list，replace）
所属 Step：S4 · 质量门
"""

from __future__ import annotations

from app.graph.state import AnalystState

MAX_FIX = 2  # 修正轮数上限（route_after_validate 引用，超限放行 deliver）


def _validate(state: AnalystState) -> list[str]:
    """规则校验（纯规则无 LLM）：数字可溯源 + 数据完整。"""
    issues: list[str] = []
    metrics = state.get("computed_metrics") or []
    if not metrics:
        issues.append("无指标计算结果")
    for item in metrics:
        name = item.get("metric", "?")
        if item.get("degraded"):
            issues.append(f"指标「{name}」计算失败，数据缺失")
            continue
        if not item.get("source_refs"):
            issues.append(f"指标「{name}」缺少溯源引用（source_ref）")
    if not state.get("report"):
        issues.append("报告为空")
    return issues


async def reviewer(state: AnalystState) -> dict:
    """质量门：校验数字可溯源，产出 report_ok + report_issues。"""
    issues = _validate(state)
    return {"report_ok": not issues, "report_issues": issues}


def route_after_validate(state: AnalystState) -> str:
    """通过 → deliver；不通过且未超修正上限 → fixer；超限 → deliver（错误是数据）。"""
    if state.get("report_ok"):
        return "deliver"
    if (state.get("fix_count") or 0) >= MAX_FIX:
        return "deliver"
    return "fixer"