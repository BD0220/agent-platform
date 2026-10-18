"""LLM Provider 工厂 + Token 用量追踪。

Provider 从哪来
    优先读运行期设置里选中的**注册表模型**（也就是界面上那个下拉框），
    这样"换模型"是纯数据驱动的：换一条注册表记录 + 重置单例即可，
    不需要改代码、也不需要重启服务。

    如果选中的模型没有可用 key，就退回老的 ``LLM_PROVIDER`` 环境变量方案 ——
    保证 `.env` 里只配了 ``DEEPSEEK_API_KEY`` 的老用户升级后照样能跑。
"""
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
    """返回当前生效的 Provider（单例）。

    注意单例语义：任务运行期间拿到的是同一个对象，所以**运行中切换模型
    不会影响正在跑的那一轮**，只对之后新发起的调用生效。这是刻意的 ——
    中途换模型会让同一条协作链上的输出风格突然断裂，反而更难排查。
    """
    global _provider
    if _provider is not None:
        return _provider
    _provider = _build_provider()
    return _provider


def _build_provider() -> LLMProvider:
    """先试注册表，不行再退回环境变量。"""
    try:
        from . import settings
        from .models import get_model, resolved_endpoint

        mid = settings.get_active_model_id()
        entry = get_model(mid)
        if entry:
            ep = resolved_endpoint(mid)
            key = settings.resolve_key(ep["vendor"])

            if key and ep["base_url"] and ep["model"]:
                from ._openai_compatible import RegistryProvider

                provider = RegistryProvider(
                    {**entry, "model": ep["model"]}, key, base_url=ep["base_url"])
                safe_print(f"[LLM] 注册表模型: {ep['label']} "
                           f"({ep['model']} @ {ep['base_url']})")
                return provider

            missing = []
            if not key:
                missing.append("API Key")
            if not ep["base_url"]:
                missing.append("base_url")
            if not ep["model"]:
                missing.append("model 名")
            safe_print(f"[LLM] 模型 {mid} 缺少 {'/'.join(missing)}，"
                       f"回退到环境变量方案（可在界面「模型」里补上）")
    except Exception as e:
        safe_print(f"[LLM] 注册表解析失败，回退环境变量方案: {e}")

    return _env_provider()


def _env_provider() -> LLMProvider:
    """老的方案：按 LLM_PROVIDER 环境变量挑 Provider。"""
    provider_name = os.getenv("LLM_PROVIDER", "deepseek").lower()

    if provider_name == "openai":
        from .openai import OpenAIProvider
        provider = OpenAIProvider()
    elif provider_name == "claude":
        from .claude import ClaudeProvider
        provider = ClaudeProvider()
    elif provider_name == "deepseek":
        from .deepseek import DeepSeekProvider
        provider = DeepSeekProvider()
    else:
        safe_print(f"[LLM] 未知的 LLM_PROVIDER: {provider_name}，使用 DeepSeek 兜底")
        from .deepseek import DeepSeekProvider
        provider = DeepSeekProvider()

    safe_print(f"[LLM] Provider: {provider.provider_name}, Model: {provider.model_name}")
    return provider


def reset_llm():
    """重置 Provider 单例（切换模型 / 修改密钥后必须调用）。"""
    global _provider
    _provider = None


def switch_model(model_id: str) -> dict:
    """切换当前模型，并让下一次调用就用上它。

    Returns:
        {"ok": bool, "model": str, "message": str}
        注意这里**不报错**于"缺 key"：允许选中一个还没配 key 的模型，
        界面上会明确提示去补 key —— 比"点了没反应"友好得多。
    """
    from . import settings
    from .models import get_model

    entry = get_model(model_id)
    if not entry:
        return {"ok": False, "model": model_id, "message": f"未知模型: {model_id}"}

    settings.set_active_model(model_id)
    reset_llm()

    key = settings.resolve_key(entry["vendor"])
    if not key:
        vendor = entry["vendor"]
        return {"ok": True, "model": model_id,
                "message": f"已切到 {entry['label']}，但还没有可用的 API Key（厂商 {vendor}），"
                           f"请在「模型 → API 密钥」里填上。"}
    return {"ok": True, "model": model_id, "message": f"已切换到 {entry['label']}"}


def current_model_info() -> dict:
    """当前模型 + Provider 的实况（界面右上角展示用）。

    这里刻意把「选中的」和「实际生效的」分开报：
    选了一个还没配 key 的模型时，``_build_provider`` 会退回环境变量方案，
    于是 label 是 Qwen、真实调用却打到 DeepSeek —— 只报前者就是骗人。
    所以多给 ``effective_*`` 和 ``is_fallback``，让界面能把这句话说出来。
    """
    from . import settings
    from .models import get_model, vendor_of

    mid = settings.get_active_model_id()
    entry = get_model(mid) or {}
    vendor = vendor_of(mid) or {}
    provider = get_llm()
    key = settings.resolve_key(entry.get("vendor", "")) if entry else ""

    selected_vendor = entry.get("vendor", "")
    # RegistryProvider 的 provider_name 就是厂商 id，所以能直接比；
    # 对不上就说明没走注册表，而是回退到了环境变量方案。
    is_fallback = bool(entry) and provider.provider_name != selected_vendor

    return {
        "model_id": mid,
        "label": entry.get("label", mid),
        "vendor": selected_vendor,
        "vendor_name": vendor.get("name", ""),
        "model": provider.model_name,
        "provider": provider.provider_name,
        "has_key": bool(key),
        "key_masked": settings.mask(key),
        "effective_model": provider.model_name,
        "effective_provider": provider.provider_name,
        "is_fallback": is_fallback,
    }
