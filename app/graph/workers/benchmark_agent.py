"""子 agent：benchmark_agent（行业对标专家 · deepsearch 式字典定义）。

照 deepsearch 写法：模块导出 {name, description, system_prompt, tools} 字典，
直接作为 create_deep_agent 的 subagents 传入（原生子 agent 调度，无需手写 run/build）。

输出：自由自然语言（Markdown）。
"""

from __future__ import annotations

from app.tools.calc_tool import calc  # noqa: F401 —— 安全计算
from app.tools.rag_tool import rag_search  # noqa: F401 —— 复用现有封装
from app.tools.time_tool import get_current_time  # noqa: F401 —— 时间基准
from app.tools.web_tool import web_search  # noqa: F401 —— 复用现有封装

SYSTEM_PROMPT = (
    "你是 SmartAnalyst 的行业对标专家（子 agent）。你收到：主 agent 委托的任务描述"
    "（含用户问题与相关确切指标数据）。\n"
    "你的工作：\n"
    "1. 对标：用 web_search 查行业大盘/竞品/基准数据（增速、渗透率、均值等），\n"
    "   用 rag_search 查内部口径/业务知识，把主链指标放进外部坐标系定位；\n"
    "2. 输出：自然语言对标结论（Markdown）——每条外部数据必须带来源（链接或文档名）；\n"
    "3. 查不到的数据单独列【数据缺口】，绝不编造数字与来源。"
)

# deepsearch 式字典定义
benchmark_agent = {
    "name": "benchmark_agent",
    "description": (
        "行业对标专家：查行业大盘/竞品/基准数据（增速、渗透率、均值），"
        "把主链指标放进外部坐标系定位。用户问「行业里算什么水平/竞品怎么样/大盘增速多少」时调用；"
        "只问内部业务数据时不调。"
    ),
    "system_prompt": SYSTEM_PROMPT,
    "tools": [web_search, rag_search, get_current_time, calc],
}