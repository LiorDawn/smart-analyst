# 用 dataclass + OmegaConf 把 yaml 变成强类型单例
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
from omegaconf import OmegaConf

@dataclass
class LLMConfig:
    model_name: str = ""
    base_url: str = ""
    temperature: float = 0.0

@dataclass
class DBConfig:
    host: str = ""
    port: int = 0
    user: str = ""
    password: str = ""
    database: str = ""

@dataclass
class RedisConfig:
    host: str = ""
    port: int = 0

@dataclass
class QdrantConfig:
    host: str = ""
    port: int = 0
    embedding_size: int = 1024

@dataclass
class EmbeddingConfig:
    host: str = ""
    port: int = 0
    model: str = ""

@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    db: DBConfig = field(default_factory=DBConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)

_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env")
_context = OmegaConf.load(_project_root / "conf" / "app_config.yaml")
_schema = OmegaConf.structured(AppConfig)
app_config: AppConfig = OmegaConf.to_object(
    OmegaConf.merge(_schema, _context)
)