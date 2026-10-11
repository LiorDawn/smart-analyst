from qdrant_client import AsyncQdrantClient

from app.conf.app_config import app_config


class QdrantClientManager:
    """Qdrant 客户端：RAG 语义向量检索用。"""

    def __init__(self):
        self.client = None

    def init(self):
        self.client = AsyncQdrantClient(
            url=f"http://{app_config.qdrant.host}:{app_config.qdrant.port}"
        )

    async def check(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception:
            return False


qdrant_client_manager = QdrantClientManager()