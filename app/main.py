"""SmartAnalyst 运行入口（照 deepsearch run_deep_agent 写法）。

- session 独立目录：doc/output/session_{session_id}（产物隔离，避免不同会话互相覆盖）
- ContextVar：session_dir / thread_id（照 deepsearch context.py，深层工具/监控读取）
- 澄清断点：__interrupt__ → 控制台提问 → Command(resume=answer) 恢复（≤2 轮由轮数闸兜底）
- 监控：monitor.report_* 上报节点/子 agent/工具/结果（控制台保底，可挂 WebSocket）

用法（项目根目录下）：
    python -m app.main
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from langgraph.types import Command

from app.api.context import reset_session_context, set_session_context, set_thread_context
from app.api.monitor import monitor
from app.graph.graph import build_graph

# 当前文件位于 app/main.py，parents[1] 即项目根目录
project_root = Path(__file__).parents[1].resolve()
output_root = project_root / "doc" / "output"


async def run_agent(question: str, session_id: str | None = None) -> dict:
    """异步执行一次分析任务：起跑 → 澄清断点循环 → 结果落盘 → 监控上报。"""
    session_id = session_id or uuid.uuid4().hex[:8]

    # 每个会话独立工作目录，避免不同任务产物互相覆盖（照 deepsearch）
    session_dir = output_root / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir_str = str(session_dir).replace("\\", "/")

    # ContextVar：监控与深层工具读取 thread_id / session_dir，无需层层传参
    session_token = set_session_context(session_dir_str)
    thread_token = set_thread_context(session_id)
    monitor.report_session_dir(session_dir_str)

    graph = build_graph()
    config = {"configurable": {"thread_id": session_id}}  # checkpointer 按 thread_id 保存状态

    try:
        # ① 起跑
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": question}]}, config
        )

        # ② 澄清断点循环：__interrupt__ → 展示 → 回答 → resume
        while interrupts := result.get("__interrupt__") or []:
            payload = interrupts[0].value
            monitor.report_node("clarify_interrupt", payload)
            print(f"\n[需要补充] {payload['prompt']}")
            answer = input("你的回答: ").strip() or "无补充"
            result = await graph.ainvoke(Command(resume=answer), config)

        # ③ 结果落盘 + 监控上报
        report = result.get("report")
        if report:
            report_path = session_dir / "report.md"
            report_path.write_text(report, encoding="utf-8")
            print(f"\n报告已保存: {report_path}")
            monitor.report_task_result(f"报告已保存: {report_path}")
        else:
            monitor.report_error("无报告产出", {"errors": result.get("errors")})
        return result
    finally:
        # 恢复 ContextVar，避免本次任务信息残留到后续请求
        reset_session_context(session_token, thread_token)


async def main() -> None:
    """CLI 主循环：一问一答，每次新建会话（thread_id 随机）。"""
    print("SmartAnalyst CLI（照 deepsearch 写法 · Ctrl+C 退出）")
    while True:
        q = input("\n问题: ").strip()
        if not q:
            continue
        result = await run_agent(q)
        print("\n" + "=" * 40)
        print(result.get("report") or "(无报告)")
        if result.get("errors"):
            print("\n[降级记录]", result["errors"])


if __name__ == "__main__":
    asyncio.run(main())
