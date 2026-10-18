"""OpenAI API Provider。"""
import os

from ._openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI API Provider"""

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        super().__init__(
            api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
            base_url=base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=model or os.getenv("OPENAI_MODEL", "gpt-4o"),
        )

    @property
    def provider_name(self) -> str:
        return "openai"
