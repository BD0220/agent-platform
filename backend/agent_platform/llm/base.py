"""LLM Provider 抽象基类 + 数据类。"""
from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass, field


class LLMNotConfigured(RuntimeError):
    """模型没配好（缺 API Key / base_url）。

    单独一个异常类型，是为了让启动、界面和测试都能区分
    "没配置" 和 "调用失败"：前者是配置问题，不该伪装成一次请求错误。
    """


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
                        temperature: float = 0.3, on_delta=None) -> dict:
        """结构化输出：强制模型返回 JSON，自动解析为 dict。

        ``on_delta`` 可选：边生成边回调（结构化输出的 JSON 正文）。产品经理
        一次性生成几千字 JSON 时要 60~90 秒，不回调的话界面就干等（2026-10-18
        实测"一到产品经理这就卡半天"）。调用方只拿它做进度提示，不解析。
        """
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

        result = self.chat_collect(modified_messages, tools=None,
                                   temperature=temperature, on_delta=on_delta)
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

    def chat_collect(self, messages: list[dict], tools: list[dict] = None,
                     temperature: float = 0.7, on_delta=None) -> ChatResult:
        """流式生成 + 汇总成完整 ChatResult。

        ``on_delta(text)`` 在每个内容片段到达时被回调（用于界面实时显示），
        最终返回和 ``chat()`` 等价的结果。基类默认降级为非流式 —— 各家
        Provider 想支持实时显示就覆写它；不支持时功能照常，只是没有逐字效果。
        """
        if on_delta is not None:
            # 非流式路径：把整段内容一次性回调，保证界面至少能显示
            result = self.chat(messages, tools, temperature)
            if result.content:
                on_delta(result.content)
            return result
        return self.chat(messages, tools, temperature)
