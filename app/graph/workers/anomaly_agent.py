"""子 agent：anomaly_agent（异常检测专家 · deepsearch 式字典定义）。

照 deepsearch 写法：模块导出 {name, description, system_prompt, tools} 字典，
直接作为 create_deep_agent 的 subagents 传入（原生子 agent 调度，无需手写 run/build）。

输出：自由自然语言（Markdown）。
"""

from __future__ import annotations

from app.tools.calc_tool import calc  # noqa: F401 —— 安全计算
from app.tools.rag_tool import rag_search  # noqa: F401 —— 复用现有封装
from app.tools.time_tool import get_current_time  # noqa: F401 —— 时间基准（判断"最近"异常）
from app.tools.web_tool import web_search  # noqa: F401 —— 复用现有封装

SYSTEM_PROMPT = (
    "你是 SmartAnalyst 的异常检测专家（子 agent）。你收到：主 agent 委托的任务描述"
    "（含用户问题与相关确切指标数据，如跨期 change、原始查询行）。\n"
    "你的工作：\n"
    "1. 扫描：找异常——环比突变（|change| 明显偏离常态，如 >±20%）、渠道/品类异动、退款率飙升等；\n"
    "2. 判断：用 web_search 对照行业/季节性，用 rag_search 查指标口径，判断异常是否合理（如大促/节日效应）；\n"
    "3. 输出：自然语言异常分析（Markdown）——每条异常给指标、维度、变化数值、可疑原因、严重程度；\n"
    "   查不到依据的列【数据缺口】，绝不编造数字。"
)

# deepsearch 式字典定义
anomaly_agent = {
    "name": "anomaly_agent",
    "description": (
        "异常检测专家：自动扫指标找异常（环比突变/渠道品类异动/退款率飙升），"
        "输出「哪异常 + 可疑原因」。用户问「有没有异常/突然变化/哪不对」时调用；纯例行查数时不调。"
    ),
    "system_prompt": SYSTEM_PROMPT,
    "tools": [web_search, rag_search, get_current_time, calc],
}