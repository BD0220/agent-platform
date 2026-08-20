"""DeepSeek API Provider（兼容 OpenAI SDK）。"""
import os

from ._openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek API Provider"""

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        super().__init__(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        )

    @property
    def provider_name(self) -> str:
        return "deepseek"
