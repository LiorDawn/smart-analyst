"""M4 在线检索：query 文本 → 向量 + 全文混合召回 → RRF 融合 → 可选 rerank → 上下文。

管线（对齐编排文档 §10.3）：
  ① 向量化：dense（embedder）+ 稀疏（sparse_embedder，BM25）
  ② 混合召回：dense + 稀疏双路 prefetch，Qdrant 服务端 RRF 融合（融合后保留 10）
  ③ rerank 精排（可选钩子）；未挂载/失败 → 直用融合结果前 top_k
  ④ 拼 context（每块带 chunk_id，供 evidence.source_ref 溯源）

分层：retriever 只做编排，向下依赖 embedder / sparse_embedder（向量化）与
doc_qdrant_repository（召回），不触碰 Qdrant client，也不做解析/切分。

降级矩阵（§10.3）：
  - dense embedding 挂 → 纯全文召回（search_text）
  - 稀疏向量生成失败 → 单路向量召回（search）
  - 双路皆不可用 → 返回空结果 + degraded 标注
  - 融合失败 → 单路向量召回（跳过融合）
  - Qdrant 挂 → 返回空结果 + degraded 标注
  - rerank 不可用/失败 → 直接用融合结果前 top_k
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol

from app.rag.embedder import embedder
from app.rag.sparse_embedder import sparse_embedder
from app.repositories.qdrant.doc_qdrant import ScoredChunk, doc_qdrant_repository

logger = logging.getLogger(__name__)

FUSION_TOP_N = 10   # RRF 融合后保留候选数（供 rerank 精排）
FINAL_TOP_K = 5     # 最终交付数


@dataclass
class RetrievalResult:
    """一次检索结果：query + 命中块 + 降级记录。"""

    query: str
    hits: list[ScoredChunk] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)  # 本次命中的降级项

    @property
    def is_empty(self) -> bool:
        return not self.hits

    @property
    def context(self) -> str:
        """命中块拼成 LLM 上下文；每块带 chunk_id（evidence.source_ref 溯源锚点）。"""
        parts: list[str] = []
        for i, hit in enumerate(self.hits, start=1):
            parts.append(
                f"[{i}] 来源：{hit.chunk.source}（chunk_id={hit.chunk.chunk_id}）\n{hit.chunk.text}"
            )
        return "\n\n".join(parts)


class Reranker(Protocol):
    """rerank 精排接口（如 bge-reranker 服务）。

    挂载方式：Retriever(reranker=MyReranker())。未挂载时按降级矩阵
    直用融合结果，不影响检索主链路。
    """

    async def rerank(self, query: str, hits: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        """按 query 对 hits 精排，返回前 top_k 条。"""
        ...


class Retriever:
    """文档知识库在线检索编排器（向量 + 全文混合召回 + 降级）。"""

    def __init__(self, reranker: Optional[Reranker] = None):
        self.reranker = reranker

    async def retrieve(self, query: str, top_k: int = FINAL_TOP_K) -> RetrievalResult:
        degraded: list[str] = []

        # ① dense 向量化（embedding 服务挂 → 降级纯全文）
        query_vector = None
        try:
            query_vector = await embedder.aembed_query(query)
        except Exception:
            logger.warning("dense embedding 不可用: %s", query)
            degraded.append("embedding")

        # ② 稀疏向量化（sparse 失败 → 降级单路 dense）
        query_sparse = None
        try:
            query_sparse = await sparse_embedder.aembed_query(query)
        except Exception:
            logger.warning("稀疏向量不可用: %s", query)
            degraded.append("sparse")

        # ③ 召回 + RRF 融合（双路可用走混合；单路可用走对应单路）
        fused: list[ScoredChunk] = []
        try:
            if query_vector is not None and query_sparse is not None:
                try:
                    fused = await doc_qdrant_repository.search_mixed(
                        query_vector, query_sparse, top_k=FUSION_TOP_N
                    )
                except Exception:
                    logger.warning("混合融合失败，降级单路向量: %s", query)
                    degraded.append("bm25")
                    fused = await doc_qdrant_repository.search(
                        query_vector, top_k=FUSION_TOP_N
                    )
            elif query_vector is not None:
                fused = await doc_qdrant_repository.search(query_vector, top_k=FUSION_TOP_N)
            elif query_sparse is not None:
                fused = await doc_qdrant_repository.search_text(query_sparse, top_k=FUSION_TOP_N)
            else:
                return RetrievalResult(query=query, degraded=[*degraded, "qdrant"])
        except Exception:
            logger.exception("Qdrant 召回失败，返回空结果")
            return RetrievalResult(query=query, degraded=[*degraded, "qdrant"])

        # ④ rerank（可选）；未挂载/失败 → 直用融合结果前 top_k
        if self.reranker is not None and len(fused) > top_k:
            try:
                hits = await self.reranker.rerank(query, fused, top_k)
            except Exception:
                logger.warning("rerank 失败，降级用融合结果前 top_k")
                degraded.append("rerank")
                hits = fused[:top_k]
        else:
            hits = fused[:top_k]

        return RetrievalResult(query=query, hits=hits, degraded=degraded)


retriever = Retriever()