"""运行时资源绑定：把 checkpointer / budget / llm 等外部依赖注入图的 config。

功能：节点不直接 import 全局单例，统一经本模块取配置。
所属 Step：S1
状态：占位（未实现）
"""

from __future__ import annotations

from typing import Any


def build_config(thread_id: str, user_id: str, budget: dict) -> dict:
    """S1 实现：构造 invoke/ainvoke 的 config（含 configurable）。

    NOTE：thread_id 永远走 config，不进 state（checkpoint 锚点，已验证的坑）。
    """
    return {"configurable": {"thread_id": thread_id, "user_id": user_id, "budget": budget}}


def get_budget(state: dict) -> dict:
    """读取任务预算（safe_call 预算检查用）。"""
    return state.get("budget", {})
