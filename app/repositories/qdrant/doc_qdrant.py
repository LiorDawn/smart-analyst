"""Qdrant 文档知识库仓储：封装 doc_knowledge collection 的写读操作。

分层约定（沿用 shopkeeper）：client（技术层连服务）→ repository（业务层转实体）
→ agent/tool（业务使用），各层只依赖下层。

本仓储只负责向量库的存储语义（collection 管理 / 批量写入 / 召回），
向量化由调用方经 app/rag/embedder.py（dense）与 app/rag/sparse_embedder.py（BM25 稀疏）
完成——仓储不反向依赖业务管线，也不生成任何向量。

collection 名 doc_knowledge 与 M3 建库 / M4 在线检索约定一致，统一收敛在本模块。

向量结构（混合召回）：
  - dense 命名向量 "dense"（余弦相似度）
  - sparse 命名向量 "text"（BM25 稀疏向量，供服务端 RRF 融合）
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import models

from app.clients.qdrant_client import qdrant_client_manager
from app.conf.app_config import app_config
from app.entities.doc_chunk import DocChunk

COLLECTION = "doc_knowledge"
DENSE_VECTOR = "dense"
SPARSE_VECTOR = "text"


@dataclass
class ScoredChunk:
    """召回结果：命中块实体 + 相似度分数。"""

    chunk: DocChunk
    score: float


class DocQdrantRepository:
    """文档知识库向量仓储（业务语义层）。

    职责：
      - ensure_collection：幂等创建 doc_knowledge（dense + sparse 双向量；--rebuild 先删后建）
      - upsert：把 DocChunk（dense + 稀疏向量）批量写入向量库（幂等覆盖同一点）
      - search / search_text / search_mixed：单路向量 / 单路全文 / RRF 混合召回
      - count：collection 现有点数（建库幂等抽验用）

    不负责：文档解析/切分（chunker）、文本向量化（embedder / sparse_embedder）。
    """

    def __init__(self, client=None):
        self.client = client  # AsyncQdrantClient；未注入时 init() 时从 manager 获取

    def init(self) -> None:
        """绑定底层 Qdrant 客户端（未初始化时经 manager 建立连接）。"""
        if self.client is None:
            qdrant_client_manager.init()
            self.client = qdrant_client_manager.client

    async def ensure_collection(
        self, rebuild: bool = False, vector_size: int | None = None
    ) -> None:
        """幂等创建 collection；rebuild=True 时先删除再重建。"""
        self.init()
        if rebuild:
            try:
                await self.client.delete_collection(COLLECTION)
            except Exception:
                pass  # collection 不存在时删除报错，忽略即可
        existing = {c.name for c in (await self.client.get_collections()).collections}
        if COLLECTION not in existing:
            await self.client.create_collection(
                COLLECTION,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=vector_size or app_config.qdrant.embedding_size,
                        distance=models.Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    SPARSE_VECTOR: models.SparseVectorParams(),
                },
            )

    async def upsert(
        self,
        chunks: list[DocChunk],
        vectors: list[list[float]],
        sparse_vectors: list[models.SparseVector],
    ) -> None:
        """批量写入向量点（dense + 稀疏各一路）。

        参数:
            chunks: 入库级块实体（chunk_id 为 uuid5(source#index)，保证幂等）
            vectors: dense embedding（list[float]），与 chunks 一一对应
            sparse_vectors: BM25 稀疏向量（models.SparseVector），与 chunks 一一对应

        幂等性：同 source 同 chunk_index 的 chunk_id 稳定不变，
        重复建库时 upsert 覆盖同一点，不产生重复向量。
        """
        self.init()
        points = [
            models.PointStruct(
                id=c.chunk_id,
                vector={
                    DENSE_VECTOR: dense_vec,
                    SPARSE_VECTOR: sparse_vec,
                },
                payload={
                    "text": c.text,
                    "source": c.source,
                    "chunk_index": c.chunk_index,
                    "title": c.title,
                    "page": c.page,
                },
            )
            for c, dense_vec, sparse_vec in zip(chunks, vectors, sparse_vectors)
        ]
        await self.client.upsert(COLLECTION, points=points, wait=True)

    def _to_scored(self, hits) -> list[ScoredChunk]:
        """ScoredPoint → ScoredChunk 实体化（payload 反序列化）。"""
        results: list[ScoredChunk] = []
        for point in hits.points:
            p = point.payload
            results.append(
                ScoredChunk(
                    chunk=DocChunk(
                        chunk_id=point.id,
                        text=p["text"],
                        source=p["source"],
                        chunk_index=p["chunk_index"],
                        title=p.get("title", ""),
                        page=p.get("page"),
                    ),
                    score=point.score,
                )
            )
        return results

    async def search(self, query_vector: list[float], top_k: int = 5) -> list[ScoredChunk]:
        """单路向量召回（dense 余弦相似度）。"""
        self.init()
        hits = await self.client.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            using=DENSE_VECTOR,
            limit=top_k,
            with_payload=True,
        )
        return self._to_scored(hits)

    async def search_text(self, query_sparse: models.SparseVector, top_k: int = 5) -> list[ScoredChunk]:
        """单路全文召回（BM25 稀疏向量）。"""
        self.init()
        hits = await self.client.query_points(
            collection_name=COLLECTION,
            query=query_sparse,
            using=SPARSE_VECTOR,
            limit=top_k,
            with_payload=True,
        )
        return self._to_scored(hits)

    async def search_mixed(
        self,
        query_vector: list[float],
        query_sparse: models.SparseVector,
        top_k: int = 5,
    ) -> list[ScoredChunk]:
        """dense + 稀疏双路召回 → Qdrant 服务端 RRF 融合。

        注意：融合后 score 为 RRF 融合分（非余弦相似度），仅用于排序。
        """
        self.init()
        hits = await self.client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                models.Prefetch(query=query_vector, using=DENSE_VECTOR, limit=top_k),
                models.Prefetch(query=query_sparse, using=SPARSE_VECTOR, limit=top_k),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
        return self._to_scored(hits)

    async def count(self) -> int:
        """collection 现有点数（建库幂等抽验：重跑后点数不增长）。"""
        self.init()
        info = await self.client.count(COLLECTION, exact=True)
        return info.count


doc_qdrant_repository = DocQdrantRepository()