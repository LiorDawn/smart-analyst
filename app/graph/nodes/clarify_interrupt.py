"""节点：clarify_interrupt（任务口径人工澄清断点 · 已实现）。

方式（LangGraph 官方机制）：节点内 `interrupt(payload)` → 图暂停，payload 经
`graph.invoke()` 结果的 `__interrupt__` 抛给调用方（前端/入口）；调用方展示给用户，
回答后 `graph.invoke(Command(resume=answer), config)` 从断点继续，`interrupt()` 返回值 = 回答。

流程（resume 后回 task_parser 重新解析）：
  ① task_parser 出 TaskSchema，missing 非空 → route_intent 判 clarify
  ② 本节点按 missing 生成澄清问题 → interrupt(payload) 暂停，等用户补充
  ③ resume 回答 → append 进 messages（[澄清补充] 前缀）→ 回 task_parser 重解析
     （补齐信息进 task，replace）→ route_intent 再判
防死循环：clarify_count 轮数闸（≤ MAX_CLARIFY=2）——超限硬走 planner（missing 仍缺
  则 planner 降级出 errors，链路不断，错误是数据）。

注意：`interrupt()` 不能包在 try/except 里（LangGraph 限制，会抛 GraphInterrupt）。

输入：task（missing 缺项）/ clarify_count
输出：messages（append 回答）/ clarify_count（+1，replace）
所属 Step：S1 · clarify 分支
"""

from __future__ import annotations

from langgraph.types import interrupt

from app.graph.state import AnalystState

MAX_CLARIFY = 2  # 澄清轮数上限（route_intent 引用，超限硬走 planner）

# 缺失项 → 人类可读的问法（planner 侧字段名对齐 TaskSchema）
_MISSING_ASK = {
    "metrics": "指标（如：GMV、订单数）",
    "time_range": "时间范围（如：9月21日到9月27日）",
    "dims": "维度（如：按品类、按渠道）",
    "compare": "对比要求（如：和上周比、同比）",
}


def _build_prompt(task: dict) -> str:
    """按缺失项生成澄清问题（一个缺项一个问题，用户可一次答完）。"""
    missing = task.get("missing") or []
    if not missing:
        return "请补充分析所需的信息（指标 / 时间范围 / 维度）。"
    asks = [f"「{_MISSING_ASK.get(m, m)}」" for m in missing]
    return "缺少以下信息，请补充：" + "、".join(asks) + "。"


async def clarify_interrupt(state: AnalystState) -> dict:
    """暂停等用户补充 → resume 回答进 messages → 回 task_parser 重解析。"""
    task = state.get("task") or {}
    answer = interrupt(
        {
            "type": "clarify",
            "prompt": _build_prompt(task),
            "missing": task.get("missing") or [],
        }
    )
    return {
        "messages": [{"role": "user", "content": f"[澄清补充] {answer}"}],
        "clarify_count": (state.get("clarify_count") or 0) + 1,
    }
