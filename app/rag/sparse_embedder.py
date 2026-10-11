"""稀疏向量生成器：为 BM25 全文召回提供稀疏向量（Qdrant/bm25）。

dense 向量走 app/rag/embedder.py（独立 embedding 服务）；
稀疏向量是进程内 CPU 计算（fastembed 纯 BM25，无 onnx 推理），
无「连接管理」语义，故不设 client 层，直接在 rag 层封装。

对外接口与 embedder 对称：embed_documents / embed_query（同步）+ a 前缀（异步）。
首次加载会从 HuggingFace 下载 Qdrant/bm25 分词资源，之后离线可用。
"""

from __future__ import annotations

import asyncio

from fastembed import SparseTextEmbedding
from qdrant_client import models

MODEL_NAME = "Qdrant/bm25"


class SparseEmbedder:
    """BM25 稀疏向量封装：文本 → SparseVector(indices, values)。"""

    def __init__(self) -> None:
        self._model: SparseTextEmbedding | None = None

    def _ensure(self) -> SparseTextEmbedding:
        if self._model is None:
            self._model = SparseTextEmbedding(model_name=MODEL_NAME)
        return self._model

    @staticmethod
    def _to_sparse(emb) -> models.SparseVector:
        return models.SparseVector(
            indices=emb.indices.tolist(),
            values=emb.values.tolist(),
        )

    def embed_documents(self, texts: list[str]) -> list[models.SparseVector]:
        model = self._ensure()
        return [self._to_sparse(e) for e in model.embed(texts)]

    def embed_query(self, text: str) -> models.SparseVector:
        return self.embed_documents([text])[0]

    async def aembed_documents(self, texts: list[str]) -> list[models.SparseVector]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text: str) -> models.SparseVector:
        return await asyncio.to_thread(self.embed_query, text)


sparse_embedder = SparseEmbedder()