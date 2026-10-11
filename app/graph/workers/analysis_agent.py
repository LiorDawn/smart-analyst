"""子 agent：analysis_agent（归因分析专家 · deepsearch 式字典定义）。

照 deepsearch 写法：模块导出 {name, description, system_prompt, tools} 字典，
直接作为 create_deep_agent 的 subagents 传入（原生子 agent 调度，无需手写 run/build）。
  - name：子 agent 名（主 agent 任务调度工具名）
  - description：路由依据（主 agent 据此决定是否分派）
  - system_prompt：子 agent 行为约束（数据由主 agent 委托时提供，缺失用 tools 查证，不编造）
  - tools：子 agent 真实可调用的外部能力

输出：自由自然语言（Markdown）。
"""

from __future__ import annotations

from app.tools.calc_tool import calc  # noqa: F401 —— 安全计算（防 LLM 心算错）
from app.tools.rag_tool import rag_search  # noqa: F401 —— 复用现有封装（不重复造轮子）
from app.tools.time_tool import get_current_time  # noqa: F401 —— 时间基准
from app.tools.web_tool import web_search  # noqa: F401 —— 复用现有封装

SYSTEM_PROMPT = (
    "你是 SmartAnalyst 的归因分析专家（子 agent）。你收到：主 agent 委托的任务描述"
    "（含用户问题与相关确切指标数据）。\n"
    "你的工作：\n"
    "1. 归因：基于任务描述中的确切数据解释指标变化（涨/跌原因、维度贡献、异常点、指标联动），"
    "数字只能来自给到的数据；\n"
    "2. 查补：数据不足以解释时，用 rag_search 查内部口径/业务知识、web_search 查外部背景；\n"
    "3. 输出：自然语言分析（Markdown）——先结论后依据；需要补数的项，单独列【数据缺口】小节。\n"
    "规则：不自由拼 SQL、不编造数字与来源。"
)

# deepsearch 式字典定义：name / description（主 agent 路由依据）/ system_prompt / tools
analysis_agent = {
    "name": "analysis_agent",
    "description": (
        "归因分析专家：基于确切指标数据解释涨/跌原因、维度贡献、异常点，"
        "数据不足时查内部知识库/外网补背景并上报缺口。"
        "用户问「为什么涨/跌、哪个渠道品类拖累、需要深度归因」时调用；只问数值/简单确认时不调。"
    ),
    "system_prompt": SYSTEM_PROMPT,
    "tools": [rag_search, web_search, get_current_time, calc],
}