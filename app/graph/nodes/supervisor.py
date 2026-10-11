"""节点：supervisor（增值层入口 · deepsearch 原生 subagents 版）。

用 create_deep_agent 的 subagents 参数原生实现子 agent 调度：
5 个 worker 导出的 {name, description, system_prompt, tools} 字典直接作为 subagents 传入，
框架自动为每个子 agent 生成 task 调度工具，主 agent LLM 靠 description 自主路由，
无需手写 dispatch 工具 / ContextVar 上下文注入。

子 agent 模式：isolated（默认）——子 agent 只看到主 agent 委托的任务描述，
因此主 agent 委托时须把「相关确切指标数据」写进任务描述（SYSTEM_PROMPT 已约束）。

输出（deepsearch 式呈现）：只收主 agent 最终综合分析总结——
  analysis_insights = [{"agent": "supervisor", "text": final}]（writer 渲染「二、AI 分析」）。
  子 agent 结果是过程轨迹，不进交付。

降级（错误是数据不是异常）：
  - LLM 不可用/构造失败/调用失败 → 空 insights + errors（主链照走）

输入：messages（question）/ computed_metrics
输出：analysis_insights（list, add）
所属 Step：查数段之后 · 增值层入口（compute → supervisor → writer）
"""

from __future__ import annotations

import json
import logging

from deepagents import create_deep_agent

from app.api.monitor import monitor
from app.clients.llm_client import llm_client_manager
from app.graph.middleware import build_supervisor_middleware
from app.graph.state import AnalystState, last_user_text
from app.graph.workers.analysis_agent import analysis_agent
from app.graph.workers.anomaly_agent import anomaly_agent
from app.graph.workers.benchmark_agent import benchmark_agent
from app.graph.workers.forecast_agent import forecast_agent
from app.graph.workers.proposal_agent import proposal_agent

logger = logging.getLogger(__name__)

# 5 个专家子 agent（deepsearch 原生：subagents= 直接吃字典定义，字段名即 SubAgent 契约）
SUBAGENTS = [
    analysis_agent,
    benchmark_agent,
    anomaly_agent,
    forecast_agent,
    proposal_agent,
]

MAX_AGENT_STEPS = 20  # ReAct 步数预算闸：5 子 agent 各调一次 + 总结，余量充足，防长尾循环烧 token
# 中间件闸（模型调用上限 / 工具调用上限 / 模型降级 / HITL）统一在 app/graph/middleware/ 装配

SYSTEM_PROMPT = (
    "你是 SmartAnalyst 的监督者（supervisor，主 agent）。输入：用户问题 + 主链已算出的确切指标数据。\n"
    "你有 5 个专家子 agent 调度工具（每个工具的 description 说明适用场景，先判断「这次要不要深入分析」）：\n"
    "1. 只问数值/简单确认 → 不调子 agent，直接简要总结回答（省 token）；\n"
    "2. 需要归因/行业对标/异常排查/趋势预测/方案产出 → 调对应子 agent（可多个，一个一次）；\n"
    "   委托时必须把「相关确切指标数据」原样写进任务描述（子 agent 看不到你的上下文，缺数会编造）；\n"
    "3. 最后输出一份【综合分析总结】（Markdown，面向用户，不要分小节列子 agent 名）：\n"
    "   - 结论：本期表现 + 主因/次因，落到具体数字；\n"
    "   - 建议：2-4 条可执行动作，可量化目标；\n"
    "   - 预测：仅当用户明确要求预测/展望时给出，带区间和置信度；\n"
    "   只基于给定指标数据与子 agent 结果，禁止编造数字。"
)


def _build_supervisor_agent():
    """惰性构造主 agent：create_deep_agent（原生 subagents 调度 + 中间件闸 + HITL）。"""
    if llm_client_manager.client is None:
        llm_client_manager.init()

    middleware, interrupt_on = build_supervisor_middleware()
    return create_deep_agent(
        model=llm_client_manager.client,
        system_prompt=SYSTEM_PROMPT,
        subagents=SUBAGENTS,
        interrupt_on=interrupt_on,   # HITL：save_doc/export_pdf 调用前暂停等用户确认
        middleware=middleware,       # 模型/工具调用上限 + 模型降级（未配备用模型时自动跳过）
    )


async def supervisor(state: AnalystState) -> dict:
    """增值层入口：主 agent 自主调度子 agent → 只收最终综合分析总结（deepsearch 式）。"""
    task = state.get("task") or {}
    question = (task.get("rewritten") or "").strip() or last_user_text(state.get("messages") or [])
    computed_metrics = state.get("computed_metrics") or []

    try:
        agent = _build_supervisor_agent()
    except Exception as e:  # noqa: BLE001 —— 构造失败当"不调"，错误是数据
        logger.warning("supervisor agent 构造失败: %s", e)
        return {
            "analysis_insights": [],
            "errors": [{"node": "supervisor", "msg": f"增值层不可用: {e}"}],
        }

    try:
        monitor.report_assistant("supervisor", {"question": question[:200]})
        resp = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"用户问题：{question}\n\n"
                            f"已算出的指标数据：\n{json.dumps(computed_metrics, ensure_ascii=False)}"
                        ),
                    }
                ]
            },
            config={"recursion_limit": MAX_AGENT_STEPS},
        )
    except Exception as e:  # noqa: BLE001 —— LLM 不可用/超时，降级空结果，主链照走
        logger.warning("supervisor 调用失败，降级不调子 agent: %s", e)
        return {
            "analysis_insights": [],
            "errors": [{"node": "supervisor", "msg": f"增值层调用失败: {e}"}],
        }

    final = str(resp["messages"][-1].content or "")
    if not final:
        return {
            "analysis_insights": [],
            "errors": [{"node": "supervisor", "msg": "supervisor 无输出"}],
        }
    return {"analysis_insights": [{"agent": "supervisor", "text": final}]}