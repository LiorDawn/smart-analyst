"""节点：report_confirm（报告方向人工确认断点）。

功能：reviewer 质量门通过后，interrupt_before 拦停让用户确认报告方向
      （可改口径 / 维度），确认后 resume 进 deliver。
输入：report（草稿）
输出：report（确认后，replace）
所属 Step：S5
状态：占位（未实现）
"""

from __future__ import annotations

from langgraph.types import interrupt

from app.graph.state import AnalystState


async def report_confirm(state: AnalystState) -> dict:
    """S5 实现：interrupt(report 预览) → 用户确认/调整 → 返回最终 report。"""
    confirm = interrupt({"report": state.get("report"), "prompt": "确认报告方向，可修改口径 / 维度"})
    return {"report": confirm.get("report", state.get("report"))}