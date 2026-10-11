"""客户端层：封装外部服务访问（LLM/Embedding/Redis/Qdrant/MySQL），供上层 repository 与工具调用。"""

from app.clients.embedding_client import embedding_client_manager
from app.clients.llm_client import llm_client_manager
from app.clients.mysql_client import mysql_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.clients.redis_client import redis_client_manager

__all__ = [
    "embedding_client_manager",
    "llm_client_manager",
    "mysql_client_manager",
    "qdrant_client_manager",
    "redis_client_manager",
]