from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.conf.app_config import app_config


class MySQLClientManager:
    """MySQL 客户端：SQLAlchemy 异步引擎 + 会话工厂（权威持久化）。"""

    def __init__(self):
        self.engine: AsyncEngine | None = None
        self.session_factory = None

    def _dsn(self) -> str:
        db = app_config.db
        return (
            f"mysql+aiomysql://{db.user}:{db.password}"
            f"@{db.host}:{db.port}/{db.database}?charset=utf8mb4"
        )

    def init(self):
        self.engine = create_async_engine(self._dsn(), pool_pre_ping=True, echo=False)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def check(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


mysql_client_manager = MySQLClientManager()