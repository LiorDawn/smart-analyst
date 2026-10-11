"""路由：fan_out（动态并行分派路由函数）。

功能：按 plan 生成 list[Send]，每个 PlannerSchema → 一个 research_agent 实例。
      ★ 本函数作为 add_conditional_edges 的路由函数，不注册为 add_node 节点。
输入：plan（list[PlannerSchema]，planner 写入）
输出：list[Send("research_agent", {"plan_item": item})]

payload 设计：用 plan_item 而非 task ——
  每个 Send 实例的 state 是隔离的，plan_item 只属于该实例；
  若用 {"task": item} 会覆盖主 state 的 task（TaskSchema），语义污染。
所属 Step：S2
"""

from __future__ import annotations

from langgraph.types import Send

from app.graph.state import AnalystState


def fan_out(state: AnalystState) -> list[Send]:
    """按 plan 拆成并行 Send：一个查询单 = 一个 research_agent 实例。"""
    plan = state.get("plan") or []
    return [Send("research_agent", {"plan_item": item}) for item in plan]
