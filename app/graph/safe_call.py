"""节点统一健壮性封装：预算检查 → 重试退避 → 超时 → 错误转数据。

功能：节点不裸写 try/except，统一过 safe_call：
      预算耗尽 → BudgetExceeded；末次失败 → ErrorRecord 进 state.errors + FallbackNeeded。
核心语义：「错误是数据不是异常」——不抛断全图，降级由条件边读 errors 决定。
所属 Step：S2 起逐步使用，S6 全面接入
状态：占位（未实现）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass
class ErrorRecord:
    """节点错误记录（进 state.errors 审计）。"""

    node: str
    exc: str
    ts: str = ""


class BudgetExceeded:
    """闸 2：步骤预算耗尽信号。"""

    def __init__(self, node: str) -> None:
        self.node = node


class FallbackNeeded:
    """降级信号：节点失败，路由走降级路径。"""

    def __init__(self, node: str) -> None:
        self.node = node


async def safe_call(
    fn: Callable[[], Awaitable[T]],
    *,
    node: str,
    state: dict,
    retries: int = 3,
    timeout_s: float = 60,
) -> T | FallbackNeeded | BudgetExceeded:
    """S2 实现：预算检查 → 指数退避重试 → 超时 → 错误转 ErrorRecord。

    末次失败返回 FallbackNeeded（不抛出），调用方读信号走降级。
    """
    raise NotImplementedError("S2 实现")
