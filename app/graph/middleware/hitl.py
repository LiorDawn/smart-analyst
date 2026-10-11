"""中间件：Human-in-the-loop（工具执行前人工确认）。

仅对"重操作"工具调用前暂停，征求用户确认——生成分析文档（save_doc）/ 导出 PDF（export_pdf）。
其他工具不打断，保持全自动链路。
"""

from __future__ import annotations

# 工具名 → 调用前是否暂停等用户确认（True = 暂停）
INTERRUPT_ON_TOOLS: dict[str, bool] = {"save_doc": True, "export_pdf": True}


def build() -> dict[str, bool]:
    """返回 HITL 工具配置（传给 create_deep_agent 的 interrupt_on 参数）。"""
    return dict(INTERRUPT_ON_TOOLS)
