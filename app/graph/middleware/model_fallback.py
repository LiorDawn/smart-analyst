"""中间件：主模型失败自动降级备用模型（ModelFallback）。

意义：主模型/key 失效时 agent 内部自动切备用模型，不必整个 supervisor 降级。
建议配第二个 provider（不同 base_url + 不同 key），配同 provider 的另一模型救不了 key 失效。
未配置 LLM_BACKUP_API_KEY 时返回 None（跳过该中间件，走外层降级矩阵兜底）。
"""

from __future__ import annotations

import logging
import os

from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_openai import ChatOpenAI

from app.conf.app_config import app_config

logger = logging.getLogger(__name__)


def build_backup_llm() -> ChatOpenAI | None:
    """构造备用模型客户端（读 LLM_BACKUP_* 环境变量），未配置返回 None。"""
    key = os.getenv("LLM_BACKUP_API_KEY")
    if not key:
        logger.warning("未配置 LLM_BACKUP_API_KEY，ModelFallbackMiddleware 跳过（主模型失效时走外层降级）")
        return None
    return ChatOpenAI(
        model=os.getenv("LLM_BACKUP_MODEL", "deepseek-chat"),
        base_url=os.getenv("LLM_BACKUP_BASE_URL", app_config.llm.base_url),
        api_key=key,
        temperature=app_config.llm.temperature,
    )


def build() -> ModelFallbackMiddleware | None:
    """构造模型降级中间件；备用模型未配置时返回 None（调用方跳过挂载）。"""
    backup = build_backup_llm()
    if backup is None:
        return None
    return ModelFallbackMiddleware(fallback_model=backup)
