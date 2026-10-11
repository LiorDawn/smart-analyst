"""请求上下文管理模块（照 deepsearch 写法）。

ContextVar 在异步请求链路中保存当前任务的 thread_id / session_dir，
工具、子 agent、监控可在深层调用中读取，无需层层传参（防并发请求串台）。
"""

from contextvars import ContextVar, Token
from typing import Optional

_session_dir_ctx: ContextVar[Optional[str]] = ContextVar("session_dir", default=None)
_thread_id_ctx: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)


def set_session_context(path: str) -> Token:
    """设置当前请求链路的会话目录。返回 reset 所需的 token。"""
    return _session_dir_ctx.set(path)


def get_session_context() -> Optional[str]:
    """获取当前任务工作目录；未设置时返回 None。"""
    return _session_dir_ctx.get()


def set_thread_context(thread_id: str) -> Token:
    """设置当前请求链路线程 ID（checkpointer / WebSocket 定向推送共用）。"""
    return _thread_id_ctx.set(thread_id)


def get_thread_context() -> Optional[str]:
    """获取当前线程 ID；未设置时返回 None。"""
    return _thread_id_ctx.get()


def reset_session_context(
    session_token: Token,
    thread_token: Optional[Token] = None,
) -> None:
    """恢复请求上下文，避免本次任务信息残留到后续请求。"""
    _session_dir_ctx.reset(session_token)
    if thread_token is not None:
        _thread_id_ctx.reset(thread_token)
