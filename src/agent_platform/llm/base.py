"""LLM Provider 抽象基类 + 数据类。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator


@dataclass
class ChatResult:
    """非流式聊天返回"""
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    finish_reason: str = ""


@dataclass
class ChatChunk:
    """流式聊天的单个 chunk"""
    content: str = ""
    finish_reason: str = ""
    tool_calls_delta: list[dict] = field(default_factory=list)


class LLMProvider(ABC):
    """LLM Provider 抽象基类"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] = None,
             temperature: float = 0.7) -> ChatResult:
        ...

    def chat_structured(self, messages: list[dict], output_schema: dict = None,
                        temperature: float = 0.3) -> dict:
        """结构化输出：强制模型返回 JSON，自动解析为 dict。"""
        import json
        from ..utils.safe_print import safe_print

        modified_messages = []
        for m in messages:
            if m["role"] == "system":
                modified_messages.append({
                    "role": "system",
                    "content": m["content"] + (
                        "\n\n⚠️ 你必须且只能输出合法 JSON，不要有任何额外文字、注释或 Markdown 标记。"
                    ),
                })
            else:
                modified_messages.append(m)

        result = self.chat(modified_messages, tools=None, temperature=temperature)
        content = result.content.strip()

        json_str = content
        if "```json" in content:
            import re
            match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
            if match:
                json_str = match.group(1)
        elif "```" in content:
            import re
            match = re.search(r'```\s*([\s\S]*?)\s*```', content)
            if match:
                json_str = match.group(1)

        start = json_str.find("{")
        end = json_str.rfind("}")
        if start != -1 and end != -1:
            json_str = json_str[start:end + 1]

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            safe_print(f"[LLM] 结构化输出解析失败: {e}")
            return {"_parse_error": str(e), "_raw": content[:500]}

    @abstractmethod
    def chat_stream(self, messages: list[dict], tools: list[dict] = None,
                    temperature: float = 0.7) -> Generator[ChatChunk, None, None]:
        ...
