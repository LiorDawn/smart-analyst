"""RAG 检索工具：把 retriever 包装成 LLM 可调用的 LangChain 工具。

分层：工具层只做「适配 + 输出契约」，检索管线（向量化/双路混合召回/RRF 融合/降级）
全部复用 app/rag/retriever.py 的 Retriever，本模块不重复实现任何检索逻辑。

输出契约（对齐编排文档 §10.4 溯源闭环）：
  - context ：拼好的知识块文本，供 LLM 生成答案
  - sources ：结构化引用清单（chunk_id / source / score），供 worker_results 落 evidence
  - degraded：本次检索的降级项；empty=True 时 LLM 必须标注「无知识库依据」
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.api.monitor import monitor
from app.rag.retriever import RetrievalResult, retriever


@tool
async def rag_search(query: str) -> dict:
    """从私有经营知识库检索与问题相关的文本块，用于回答指标口径、业务规则、行业知识类问题。

    何时调用：
      - 问题涉及指标计算口径（如"销售额怎么算"）、业务定义、行业背景，需以知识库为准时；
      - 用户要求溯源"知识库/口径文档里怎么说的"时。
    何时不要调用：
      - 实时业务数据查询（请用 sql 工具）；纯闲聊。

    参数 query 填写建议：
      - 用完整问题而非孤立关键词："上月销售额的口径是什么" 优于 "销售额"；
      - 保留关键业务术语（指标名、时间范围、维度），如"复购率""2026 年 9 月""分品类"；
      - 保持用户原意，不要改写、缩写或翻译。
    """
    monitor.report_tool("rag_search", {"query": query})
    result: RetrievalResult = await retriever.retrieve(query)

    return {
        "query": result.query,
        "context": result.context,
        "sources": [
            {
                "chunk_id": h.chunk.chunk_id,
                "source": h.chunk.source,
                "score": round(h.score, 3),
            }
            for h in result.hits
        ],
        "degraded": result.degraded,
        "empty": result.is_empty,
    }
