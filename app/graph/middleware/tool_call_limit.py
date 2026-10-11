"""中间件：工具调用次数上限（防死循环）。

正常路径 5~15 次（5 子 agent 各调 1~3 个工具），run_limit=15 卡住"反复调同一工具"的长尾循环；
达上限继续（exit_behavior="continue"，agent 收提示后收敛）。
"""

from __future__ import annotations

from langchain.agents.middleware import ToolCallLimitMiddleware

RUN_LIMIT = 15


def build() -> ToolCallLimitMiddleware:
    """构造工具调用上限中间件（单次运行最多 15 次工具调用）。"""
    return ToolCallLimitMiddleware(run_limit=RUN_LIMIT, exit_behavior="continue")
