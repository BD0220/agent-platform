"""OpenAI 兼容协议 Provider —— DeepSeek 和 OpenAI 共用逻辑，仅环境变量名不同。"""
from .base import LLMProvider, ChatResult, ChatChunk
from .factory import _report_usage


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI SDK 兼容的 LLM Provider 基类。子类只需传 env/URL/model。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)

    @property
    def model_name(self) -> str:
        return self._model

    def _build_kwargs(self, messages, tools, temperature):
        kwargs = dict(model=self._model, temperature=temperature, messages=messages)
        if tools:
            kwargs["tools"] = tools
        return kwargs

    def chat(self, messages: list[dict], tools: list[dict] = None,
             temperature: float = 0.7) -> ChatResult:
        kwargs = self._build_kwargs(messages, tools, temperature)
        response = self._client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id, "type": tc.type,
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                })

        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        result = ChatResult(
            content=msg.content or "", tool_calls=tool_calls, usage=usage,
            model=self._model, finish_reason=response.choices[0].finish_reason or "",
        )
        if usage:
            _report_usage(self._model, usage)
        return result

    def chat_stream(self, messages: list[dict], tools: list[dict] = None,
                    temperature: float = 0.7):
        kwargs = self._build_kwargs(messages, tools, temperature)
        kwargs["stream"] = True
        response = self._client.chat.completions.create(**kwargs)
        for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            yield ChatChunk(
                content=delta.content or "",
                finish_reason=chunk.choices[0].finish_reason or "",
            )

    def chat_collect(self, messages: list[dict], tools: list[dict] = None,
                     temperature: float = 0.7, on_delta=None) -> ChatResult:
        """流式生成：边出字边回调 on_delta，最后汇总成完整 ChatResult。

        tool_calls 在流式里是分片到达的（按 index 增量拼 name/arguments），
        这里负责把它们拼回和 chat() 一样的结构，调用方无需关心差别。
        流式失败（个别兼容端点不支持 stream_options 等）自动退回非流式。
        """
        kwargs = self._build_kwargs(messages, tools, temperature)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception:
            # 兜底：退回一次性生成，功能不丢，只是没有逐字效果
            return super().chat_collect(messages, tools, temperature, on_delta)

        content_parts: list[str] = []
        tool_parts: dict[int, dict] = {}
        usage = {}
        finish_reason = ""

        for chunk in response:
            if getattr(chunk, "usage", None):
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta
            if delta is None:
                continue
            text = delta.content or ""
            if text and on_delta is not None:
                on_delta(text)
            content_parts.append(text)
            for tc in (delta.tool_calls or []):
                acc = tool_parts.setdefault(
                    tc.index, {"id": "", "type": "function",
                               "function": {"name": "", "arguments": ""}})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function and tc.function.name:
                    acc["function"]["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    acc["function"]["arguments"] += tc.function.arguments

        tool_calls = [tool_parts[i] for i in sorted(tool_parts)]
        result = ChatResult(
            content="".join(content_parts), tool_calls=tool_calls, usage=usage,
            model=self._model, finish_reason=finish_reason,
        )
        if usage:
            _report_usage(self._model, usage)
        return result


class RegistryProvider(OpenAICompatibleProvider):
    """由注册表里的**一条数据**驱动的 Provider。

    上面那个基类故意留了 ``provider_name`` 抽象属性没实现（DeepSeek / OpenAI
    各自的子类去实现它）。注册表方案下没有必要再为每家厂商写子类 ——
    这个类直接吃一条字典，把 (base_url, model, api_key) 三件套用上就行。
    """

    def __init__(self, entry: dict, api_key: str, base_url: str = ""):
        super().__init__(
            api_key=api_key,
            base_url=(base_url or entry.get("base_url") or "").rstrip("/"),
            model=entry.get("model") or entry.get("id") or "",
        )
        self._entry = entry

    @property
    def provider_name(self) -> str:
        return self._entry.get("vendor") or "registry"

    @property
    def entry(self) -> dict:
        return self._entry
