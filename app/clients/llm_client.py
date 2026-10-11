from langchain_openai import ChatOpenAI

from app.conf.app_config import app_config


class LLMClientManager:
    """LLM 客户端：OpenAI 兼容协议统一封装（默认 DeepSeek）。"""

    def __init__(self):
        self.client = None

    def init(self):
        import os

        self.client = ChatOpenAI(
            model=app_config.llm.model_name,
            base_url=app_config.llm.base_url,
            api_key=os.getenv("LLM_API_KEY"),
            temperature=app_config.llm.temperature,
        )

    async def check(self) -> bool:
        """最小调用验证 key 与连通性，失败返回 False。"""
        try:
            resp = await self.client.agenerate([["hi"]])
            return len(resp.generations[0]) > 0
        except Exception:
            return False


llm_client_manager = LLMClientManager()