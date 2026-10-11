"""节点：reflect（反思 + 双闸守卫）。

功能：量化信息充分性（计划覆盖率/引用数/置信度）→ 产出 evidence_gaps，iteration + 1。
      route_after_reflect 提供双闸路由（闸 1 迭代上限；闸 2 由 safe_call 预算检查承担）。
输入：plan / research_results / iteration
输出：evidence_gaps（operator.add 累加）、iteration（replace）
所属 Step：S3
状态：占位（未实现）
"""

from __future__ import annotations

from app.graph.state import AnalystState


async def reflect(state: AnalystState) -> dict:
    """S3 实现：compute_gaps(plan, research_results) → {"evidence_gaps": gaps, "iteration": n+1}。"""
    return {}


def route_after_reflect(state: AnalystState) -> str:
    """S3 实现：iteration>=max → "writer"；无缺口 → "writer"；有缺口 → "replanner"。"""
    return "writer"
