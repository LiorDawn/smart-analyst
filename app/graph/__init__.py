"""SmartAnalyst 图编排层。

分层（沿用 shopkeeper）：clients（技术层）→ repositories（存储语义）→ graph（编排层）→ api（服务层）。
graph 层内部：state（状态定义）→ nodes（节点实现）→ graph.py（图组装）。
M5 按「先骨架后血肉」增量开发：本目录先落 state + 最小图骨架，节点逐个添加。
"""

from __future__ import annotations

from app.graph.state import AnalystState, TaskStatus

__all__ = ["AnalystState", "TaskStatus"]
