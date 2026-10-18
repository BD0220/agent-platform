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
