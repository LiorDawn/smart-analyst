"""图状态定义（S1 最小版：只含 task_parser 消费的字段）。

演进原则：字段跟着消费它的节点走，用到再加——不预置未来字段。
task_parser 消费：messages（读）/ task（写）/ errors（写）/ status（写）。

已删除（预声明未消费）：thread_id/user_id（走 config 不进 state）、
  plan / research_results / evidence_gaps / artifacts、
  iteration / budget / fallbacks_used。
对应节点实现时按需加回（加到本文件即表示该节点开始消费它）。
（report 已于最简闭环加回：writer 写 → deliver 读。）
"""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class TaskStatus(StrEnum):
    """任务状态（与 MySQL tasks.status 对齐）。"""

    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"  # 人工中断中
    DONE = "done"
    FAILED = "failed"
    DEGRADED = "degraded"  # 降级出报告


class AnalystState(TypedDict):
    """任务图状态（S1 最小版）。

    reducer 语义：task / status 是覆盖（replace，默认行为）；errors 累加（operator.add）。
    """

    messages: Annotated[list, add_messages]  # 对话历史（task_parser 读取最后一条 user 消息）
    task: dict | None = None  # TaskSchema：{intents, metrics, time_range, dims, compare, missing}（replace）
    plan: list | None = None  # QueryItem 列表（planner 写，query_executor 消费，replace）
    research_results: Annotated[list, operator.add]  # ResearchResult 列表（query_executor 写，join 整理，compute 消费）
    computed_metrics: list | None = None  # 指标计算结果（compute 写，writer/子 agent 消费，replace）
    analysis_insights: Annotated[list, operator.add]  # 子 agent 分析结论（supervisor 写，writer 消费，add）
    report: str | None = None  # 报告 Markdown（writer 写，deliver 消费，replace）
    clarify_count: int = 0  # 澄清轮数（clarify_interrupt 写，route_intent 读 · 闸 ≤2，replace）
    report_ok: bool | None = None  # 质量门结论（reviewer 写，route_after_validate 读，replace）
    report_issues: list | None = None  # 质量门检出问题（reviewer 写，fixer 消费，replace）
    fix_count: int = 0  # 修正轮数（fixer 写，route_after_validate 读 · 闸 ≤2，replace）
    errors: Annotated[list, operator.add]  # 错误是数据不是异常（task_parser 降级时追加）
    status: str = ""  # 任务状态（存 TaskStatus 的 .value，枚举实例不进 checkpoint）


def task_field(state: AnalystState, key: str, default=None):
    """读取 state.task 子字段的便捷辅助（避免各节点写 state["task"]["xxx"] 两级路径）。

    用法：metrics = task_field(state, "metrics", [])
    设计：task 保持封装（一个意图契约内聚），读取用辅助函数获得平铺般的直观。
    """
    task = state.get("task") or {}
    return task.get(key, default)


def last_user_text(messages) -> str:
    """取最后一条用户消息文本（兼容 dict 与 LangChain 消息对象）。

    messages 由 add_messages reducer 管理：输入 dict 会被转成 LangChain 消息对象
    （HumanMessage），读取必须兼容两种形态，否则节点 `m.get("role")` 会崩。
    """
    for m in reversed(messages or []):
        if isinstance(m, dict):
            if m.get("role") == "user":
                return str(m.get("content", ""))
        else:
            if getattr(m, "type", "") == "human":
                return str(getattr(m, "content", ""))
    return ""
