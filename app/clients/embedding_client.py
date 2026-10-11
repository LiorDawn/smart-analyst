from __future__ import annotations

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.conf.app_config import app_config


class EmbeddingClientManager:
    """Embedding 服务客户端：连接独立部署的 HTTP embedding 端点（对标 shopkeeper）。"""

    def __init__(self):
        self.client = None

    def _url(self) -> str:
        return f"http://{app_config.embedding.host}:{app_config.embedding.port}"

    def init(self):
        self.client = HuggingFaceEndpointEmbeddings(model=self._url())

    async def check(self) -> bool:
        try:
            await self.client.aembed_query("hi")
            return True
        except Exception:
            return False


embedding_client_manager = EmbeddingClientManager()