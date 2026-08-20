"""Anthropic Claude Provider。"""
import json
import os

from .base import LLMProvider, ChatResult, ChatChunk
from .factory import _report_usage


class ClaudeProvider(LLMProvider):
    """Anthropic Claude Provider"""

    def __init__(self, api_key: str = None, model: str = None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _convert_tools(self, tools: list[dict]) -> list[dict] | None:
        result = []
        for t in tools:
            if t.get("type") == "function" and "function" in t:
                func = t["function"]
                result.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}, "required": []}),
                })
        return result if result else None

    def _split_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system_content = ""
        rest = []
        for m in messages:
            if m["role"] == "system":
                system_content += m.get("content", "") + "\n"
            else:
                rest.append(m)
        return system_content.strip(), rest

    def chat(self, messages: list[dict], tools: list[dict] = None,
             temperature: float = 0.7) -> ChatResult:
        client = self._get_client()
        system, user_messages = self._split_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        kwargs = dict(model=self._model, max_tokens=4096, temperature=temperature, messages=user_messages)
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = client.messages.create(**kwargs)

        content_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id, "type": "function",
                    "function": {"name": block.name, "arguments": json.dumps(block.input)},
                })

        usage = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }

        result = ChatResult(
            content="\n".join(content_parts) if content_parts else "",
            tool_calls=tool_calls, usage=usage,
            model=self._model, finish_reason=response.stop_reason or "",
        )
        if usage:
            _report_usage(self._model, usage)
        return result

    def chat_stream(self, messages: list[dict], tools: list[dict] = None,
                    temperature: float = 0.7):
        client = self._get_client()
        system, user_messages = self._split_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        kwargs = dict(model=self._model, max_tokens=4096, temperature=temperature, messages=user_messages)
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield ChatChunk(content=text)
