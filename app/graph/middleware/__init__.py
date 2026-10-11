"""supervisor 中间件包：一个中间件一个文件，统一从这里装配。

用法：
    from app.graph.middleware import build_supervisor_middleware
    middleware, interrupt_on = build_supervisor_middleware()
    create_deep_agent(..., middleware=middleware, interrupt_on=interrupt_on)

文件对应：
    model_call_limit.py  模型调用次数上限（成本闸）
    tool_call_limit.py   工具调用次数上限（防死循环）
    model_fallback.py    主模型失败降级备用模型（跨 provider）
    hitl.py              Human-in-the-loop 工具执行前人工确认
    summarization.py     长对话自动总结（deepagents 基础栈默认已内置，默认不重复挂载）
"""

from __future__ import annotations

from app.graph.middleware.hitl import build as build_hitl_config
from app.graph.middleware.model_call_limit import build as build_model_call_limit
from app.graph.middleware.model_fallback import build as build_model_fallback
from app.graph.middleware.tool_call_limit import build as build_tool_call_limit


def build_supervisor_middleware() -> tuple[list, dict[str, bool]]:
    """装配 supervisor 的中间件栈。

    返回 (middleware 列表, interrupt_on 配置)，直接传给 create_deep_agent。
    ModelFallback 未配置备用模型时自动跳过（返回 None 不挂载）。
    """
    middleware = [build_model_call_limit(), build_tool_call_limit()]
    fallback = build_model_fallback()
    if fallback is not None:
        middleware.append(fallback)
    return middleware, build_hitl_config()
