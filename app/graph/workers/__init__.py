"""Worker 子图集合（app/graph/workers/）。

子 agent 是唯一「内部多步决策」阶段：research_agent。
输出契约统一为 ResearchResult（见 app/entities/planner_schema.py / task_schema.py）。
"""

from __future__ import annotations
