"""LLM Provider 工厂 + Token 用量追踪。"""
import os

from ..utils.safe_print import safe_print
from .base import LLMProvider

_usage_callbacks: list = []
_provider: LLMProvider | None = None


def on_usage(callback):
    """注册 token 用量回调。callback(model: str, usage: dict)。"""
    _usage_callbacks.append(callback)


def _report_usage(model: str, usage: dict):
    for cb in _usage_callbacks:
        try:
            cb(model, usage)
        except Exception as e:
            safe_print(f"[LLM] usage 回调异常: {e}")


def get_llm() -> LLMProvider:
    """根据环境变量 LLM_PROVIDER 返回对应的 Provider（单例）。"""
    global _provider
    if _provider is not None:
        return _provider

    provider_name = os.getenv("LLM_PROVIDER", "deepseek").lower()

    if provider_name == "deepseek":
        from .deepseek import DeepSeekProvider
        _provider = DeepSeekProvider()
    elif provider_name == "openai":
        from .openai import OpenAIProvider
        _provider = OpenAIProvider()
    elif provider_name == "claude":
        from .claude import ClaudeProvider
        _provider = ClaudeProvider()
    else:
        safe_print(f"[LLM] 未知的 LLM_PROVIDER: {provider_name}，使用 DeepSeek 兜底")
        from .deepseek import DeepSeekProvider
        _provider = DeepSeekProvider()

    safe_print(f"[LLM] Provider: {_provider.provider_name}, Model: {_provider.model_name}")
    return _provider


def reset_llm():
    """重置 Provider 单例（用于测试或切换模型）。"""
    global _provider
    _provider = None


