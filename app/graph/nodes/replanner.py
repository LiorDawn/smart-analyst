"""节点：replanner（补查计划）。

功能：只把 evidence_gaps 转成新 PlannerSchema 追加进 plan（不推翻原计划；
      重复同缺口 → 不补，靠 iteration 触发闸 1）。
输入：evidence_gaps / plan
输出：plan（追加后，replace）
所属 Step：S3
状态：占位（未实现）
"""

from __future__ import annotations

from app.graph.state import AnalystState


async def replanner(state: AnalystState) -> dict:
    """S3 实现：return {"plan": state["plan"] + new_items}。"""
    return {}
