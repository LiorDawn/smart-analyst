"""任务图节点集合（app/graph/nodes/）。

每个节点 = async def xxx(state) -> dict：
  - 只读自己声明的 state 字段（写进 docstring）
  - 只返回增量更新（reducer 语义见 state.py）
  - LLM 调用一律过 safe_call，输出一律 with_structured_output
"""

from __future__ import annotations
