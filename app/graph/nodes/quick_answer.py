"""节点：quick_answer（纯知识问答 · RAG 直答）。

定位：route_intent 判定的「quick」意图（纯口径/知识问答，不查数）——
      走 retriever 混合召回知识库 → LLM 基于召回上下文回答 → 产出 report 直达 deliver。
      与主链路（planner→query_executor→…→writer）是平行旁路，轻量快捷。

降级（错误是数据不是异常）：
  - 召回失败/未命中 → 只用 LLM 兜底直答（此时回答无知识库支撑）
  - LLM 不可用 → 退回召回原文片段；两者皆无 → 明确提示无法回答
输入：messages（question）
输出：report（str，replace）
所属 Step：M4 在线检索 · 快捷问答
"""

from __future__ import annotations

import logging

from app.clients.llm_client import llm_client_manager
from app.graph.state import AnalystState, last_user_text
from app.rag.retriever import retriever

logger = logging.getLogger(__name__)


def _question(state: AnalystState) -> str:
    """优先用改写后的标准问题（task.rewritten），无改写则回退原始用户消息。"""
    task = state.get("task") or {}
    rewritten = (task.get("rewritten") or "").strip()
    if rewritten:
        return rewritten
    return last_user_text(state.get("messages") or [])


async def quick_answer(state: AnalystState) -> dict:
    """RAG 召回 + LLM 生成答案；任一层失败优雅降级，不中断。"""
    question = _question(state)

    # ① 知识库召回
    context = ""
    try:
        result = await retriever.retrieve(question)
        if not result.is_empty:
            context = result.context
    except Exception as e:  # noqa: BLE001 —— 召回失败降级纯 LLM
        logger.warning("知识库召回失败，降级纯 LLM 回答: %s", e)

    # ② LLM 生成（有召回 → 带引用回答；无召回 → 兜底直答）
    if llm_client_manager.client is None:
        llm_client_manager.init()
    try:
        if context:
            prompt = (
                "你是经营分析知识助手。仅依据下方参考资料回答，"
                "引用处标注资料编号 [n]；资料不足以回答时明确说明。\n\n"
                f"参考资料：\n{context}\n\n"
                f"问题：{question}"
            )
        else:
            prompt = f"你是经营分析知识助手。回答以下问题，不确定时如实说明：\n{question}"
        resp = await llm_client_manager.client.ainvoke(prompt)
        answer = str(resp.content) if hasattr(resp, "content") else str(resp)
    except Exception as e:  # noqa: BLE001 —— LLM 失败退回召回原文
        logger.warning("LLM 不可用，退回召回原文: %s", e)
        answer = context or "（知识库未命中且 LLM 不可用，暂时无法回答）"

    report = f"# 快速问答\n\n> 问题：{question}\n\n## 回答\n\n{answer}"
    return {"report": report}