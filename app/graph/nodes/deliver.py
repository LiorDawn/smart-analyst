"""节点：deliver（交付 · 最简版）。

定位（用户定稿·最简闭环）：把 writer 产出的 Markdown 报告落盘 doc/output/，状态置 DONE。
幂等写库 / WebSocket 推送 / Redis 缓存 = 后续升级（S5 完整版），当前先跑通主干。

输入：report（writer 写）
输出：status（TaskStatus.value，replace）；无报告 → FAILED（错误是数据）
所属 Step：S5 · 最简版
"""

from __future__ import annotations

import time
from pathlib import Path

from app.graph.state import AnalystState, TaskStatus

# smart-analyst/doc/output（deliver.py 在 app/graph/nodes/，上 3 级到项目根）
_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "doc" / "output"


async def deliver(state: AnalystState) -> dict:
    """报告落盘 + 状态 DONE；无报告 → 降级 FAILED（错误是数据，不中断调用方）。"""
    report = state.get("report")
    if not report:
        return {
            "status": TaskStatus.FAILED.value,
            "errors": [{"node": "deliver", "msg": "无报告可交付（writer 未产出）", "step": "deliver"}],
        }

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = _OUTPUT_DIR / f"report_{ts}.md"
    path.write_text(report, encoding="utf-8")
    print(f"[deliver] 报告已保存: {path}")
    return {"status": TaskStatus.DONE.value}
