"""中间件：模型调用次数上限（成本闸）。

防一次请求（单次 run）烧几十次 LLM 调用。正常路径 13~28 次（主 agent 规划 2 + 5 子 agent 各 2~5 + 汇总 1），
run_limit=30 卡住异常死循环；达上限优雅结束（exit_behavior="end"）。
"""

from __future__ import annotations

from langchain.agents.middleware import ModelCallLimitMiddleware

RUN_LIMIT = 30


def build() -> ModelCallLimitMiddleware:
    """构造模型调用上限中间件（单次运行最多 30 次模型调用）。"""
    return ModelCallLimitMiddleware(run_limit=RUN_LIMIT, exit_behavior="end")
