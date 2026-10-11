"""节点：fixer（局部修正 · 已实现）。

功能：把 reviewer 检出的问题如实标注为「数据质量说明」段追加到报告，
      不编造、不修补缺失数据（错误是数据，如实声明）。修正后直接交付。
输入：report / report_issues
输出：report（追加数据质量说明，replace）、fix_count（+1，replace）
所属 Step：S4 · 质量门
"""

from __future__ import annotations

from app.graph.state import AnalystState


async def fixer(state: AnalystState) -> dict:
    """把 issues 如实标注为「数据质量说明」段，不编造修补。"""
    issues = state.get("report_issues") or []
    report = state.get("report") or ""
    lines = ["\n## 数据质量说明", ""]
    for i in issues:
        lines.append(f"- ⚠ {i}")
    lines.append("")
    lines.append("> 说明：以上为如实标注的数据缺口 / 不可溯源项，未经编造修补，供决策参考。")
    return {
        "report": report + "\n".join(lines),
        "fix_count": (state.get("fix_count") or 0) + 1,
    }