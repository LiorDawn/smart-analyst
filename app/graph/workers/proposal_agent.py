"""子 agent：proposal_agent（方案生成专家 · deepsearch 式字典定义）。

照 deepsearch 写法：模块导出 {name, description, system_prompt, tools} 字典，
直接作为 create_deep_agent 的 subagents 传入（原生子 agent 调度，无需手写 run/build）。

与 writer 分工：writer 出"报告"（数据+结论，主链强制）；本 agent 出"方案"（行动建议+落地步骤，用户明确要时调）。

输出：自由自然语言（Markdown）。
"""

from __future__ import annotations

from app.tools.calc_tool import calc  # noqa: F401
from app.tools.doc_tool import export_pdf, save_doc  # noqa: F401 —— 产出型工具
from app.tools.rag_tool import rag_search  # noqa: F401
from app.tools.time_tool import get_current_time  # noqa: F401
from app.tools.web_tool import web_search  # noqa: F401

SYSTEM_PROMPT = (
    "你是 SmartAnalyst 的方案生成专家（子 agent）。你收到：主 agent 委托的任务描述"
    "（含用户问题、相关确切指标数据，必要时含其他子 agent 分析的洞察）。\n"
    "你的工作：\n"
    "1. 组织方案：把数据 + 洞察组织成结构清晰的 Markdown 方案（现状结论 → 问题/机会 → 行动建议 → 预期影响），"
    "   只使用已给数据和有来源的信息，不编造；\n"
    "2. 落盘：用 save_doc 保存方案（title 建议含主题+时间）；用户明确要 PDF 时用 export_pdf 导出；\n"
    "3. 输出：自然语言方案正文 + 末尾一行【文档】标注已保存路径（若无则说明原因）。\n"
    "规则：数据不足的项列【数据缺口】；绝不编造数字与来源。"
)

# deepsearch 式字典定义
proposal_agent = {
    "name": "proposal_agent",
    "description": (
        "方案生成专家：把数据+洞察组织成可交付的方案/建议文档（save_doc 落盘 / export_pdf 导出）。"
        "用户明确要「方案/建议/行动计划/复盘文档/PDF」等产出型交付时调用；只要对话回答时不调。"
    ),
    "system_prompt": SYSTEM_PROMPT,
    "tools": [save_doc, export_pdf, rag_search, web_search, get_current_time, calc],
}