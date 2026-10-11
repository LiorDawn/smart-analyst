import redis.asyncio as aioredis

from app.conf.app_config import app_config


class RedisClientManager:
    """Redis 客户端：会话/缓存/限流/幂等去重用。"""

    def __init__(self):
        self.client = None

    def init(self):
        self.client = aioredis.Redis(
            host=app_config.redis.host,
            port=app_config.redis.port,
            decode_responses=True,
        )

    async def check(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            return False


redis_client_manager = RedisClientManager()