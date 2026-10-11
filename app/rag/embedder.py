from __future__ import annotations

from app.clients.embedding_client import embedding_client_manager


class Embedder:
    """基于独立部署的 embedding 服务做向量化（对标 shopkeeper 端点形态）。"""

    def __init__(self):
        self.client = embedding_client_manager.client

    def _ensure(self):
        if self.client is None:
            embedding_client_manager.init()
            self.client = embedding_client_manager.client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._ensure()
        return self.client.embed_documents(texts)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self._ensure()
        return await self.client.aembed_documents(texts)

    def embed_query(self, query: str) -> list[float]:
        self._ensure()
        return self.client.embed_query(query)

    async def aembed_query(self, query: str) -> list[float]:
        self._ensure()
        return await self.client.aembed_query(query)


embedder = Embedder()