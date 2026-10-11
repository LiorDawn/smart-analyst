"""路由：route_intent（task_parser 出口 · 三路分流）。

判定顺序（语义优先级）：
  ① 纯知识问答（intents 含 quick）→ "quick"（research_agent 单发，不查数）
  ② 阻断性缺失（missing 非空）→ "clarify"（⏸ 人工澄清，不瞎猜默认值）
  ③ 其余 → "analyze"（进 planner）

NOTE：missing 只含"阻断性缺失"（如 metrics 解析不出 / 时间转绝对失败）——
  可选信息缺失（dims / compare）不进 missing，由 planner 默认处理（整体分析 / 不对比）。
输入：task（TaskSchema dict，task_parser 写入，replace）
输出：路由字符串 "clarify" / "quick" / "analyze"
所属 Step：S1
"""

from __future__ import annotations

from app.graph.nodes.clarify_interrupt import MAX_CLARIFY
from app.graph.state import AnalystState


def route_intent(state: AnalystState) -> str:
    """按 TaskSchema 判定三路：quick > clarify(≤2轮) > analyze。"""
    task = state.get("task") or {}
    intents = task.get("intents") or []
    missing = task.get("missing") or []

    if "quick" in intents:
        return "quick"       # ① 纯知识问答：最高优先级，不需要其他字段
    if missing:
        # ② 阻断性缺失：先问用户；超澄清轮数闸 → 硬走 analyze（planner 降级出 errors，链路不断）
        if (state.get("clarify_count") or 0) < MAX_CLARIFY:
            return "clarify"
        return "analyze"
    return "analyze"         # ③ 正常分析任务
