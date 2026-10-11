"""三层图组装 —— 唯一拓扑权威（最简闭环 + 质量门版）。

只接线，不写业务逻辑。主链路：… → writer → reviewer →（deliver | fixer → deliver）。
质量门（reviewer+fixer）已接入：报告每个数字须可溯源，不通过则 fixer 如实标注后降级交付。
仍后置（暂不接线）：reflect（证据缺口+双闸）/ replanner（补查环）/ report_confirm（人工确认）。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.state import AnalystState
from app.graph.nodes.task_parser import task_parser
from app.graph.nodes.route_intent import route_intent
from app.graph.nodes.clarify_interrupt import clarify_interrupt
from app.graph.nodes.planner import planner
from app.graph.nodes.query_executor import query_executor
from app.graph.nodes.quick_answer import quick_answer
from app.graph.nodes.join import join
from app.graph.nodes.compute import compute
from app.graph.nodes.supervisor import supervisor
from app.graph.nodes.writer import writer
from app.graph.nodes.reviewer import reviewer, route_after_validate
from app.graph.nodes.fixer import fixer
from app.graph.nodes.deliver import deliver


def build_graph():
    builder = StateGraph(AnalystState)

    # ── ① 注册节点（最简闭环 10 个） ──
    builder.add_node("task_parser", task_parser)
    builder.add_node("clarify_interrupt", clarify_interrupt)
    builder.add_node("planner", planner)
    builder.add_node("query_executor", query_executor)
    builder.add_node("quick_answer", quick_answer)
    builder.add_node("join", join)
    builder.add_node("compute", compute)
    builder.add_node("supervisor", supervisor)
    builder.add_node("writer", writer)
    builder.add_node("reviewer", reviewer)
    builder.add_node("fixer", fixer)
    builder.add_node("deliver", deliver)

    # ── ② 入口：解析 → 三路分流 ──
    builder.add_edge(START, "task_parser")
    builder.add_conditional_edges(
        "task_parser",
        route_intent,
        {"clarify": "clarify_interrupt", "quick": "quick_answer", "analyze": "planner"},
    )
    builder.add_edge("quick_answer", "deliver")  # quick 旁路：RAG 直答产 report 直接交付
    builder.add_edge("clarify_interrupt", "task_parser")  # resume 回答进 messages → 回 task_parser 重解析（补齐 task）

    # ── ③ 最简主干：规划 → 执行 → 汇聚 → 计算 → 增值 → 成稿 → 交付 ──
    builder.add_edge("planner", "query_executor")
    builder.add_edge("query_executor", "join")
    builder.add_edge("join", "compute")
    builder.add_edge("compute", "supervisor")
    builder.add_edge("supervisor", "writer")
    builder.add_edge("writer", "reviewer")
    builder.add_conditional_edges(
        "reviewer",
        route_after_validate,
        {"deliver": "deliver", "fixer": "fixer"},  # 通过→交付；不通过→修正
    )
    builder.add_edge("fixer", "deliver")  # 修正后直接交付（如实标注，不硬卡）
    builder.add_edge("deliver", END)

    return builder.compile(checkpointer=InMemorySaver())


graph = build_graph()


# ==================== 验证入口（修复验收：可导入 · 可编译 · 拓扑完整） ====================

if __name__ == "__main__":
    # python -m app.graph.graph
    g = graph.get_graph()
    node_ids = set(g.nodes)
    required = {
        "task_parser", "clarify_interrupt", "planner", "query_executor",
        "quick_answer", "join", "compute", "supervisor", "writer",
        "reviewer", "fixer", "deliver",
    }
    missing = required - node_ids
    if missing:
        raise AssertionError(f"缺失节点: {missing}")
    print(f"[修复通过] 图编译成功 · 节点数 {len(node_ids)} · 12 个必需节点齐全")
