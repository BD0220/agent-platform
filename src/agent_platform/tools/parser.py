"""[TOOL_CALL: ...] 文本格式解析器 —— Function Calling 的降级兜底。"""
import re

from .execution import execute_tool
from ..utils.safe_print import safe_print

_TOOL_CALL_PATTERN = re.compile(r'\[TOOL_CALL:\s*(\w+)(?::\s*(.*?))?\]')


def parse_and_execute_tool_calls(text: str) -> list[dict]:
    """从文本中提取所有 [TOOL_CALL: ...] 指令并逐一执行。"""
    lines = text.split("\n")
    results = []
    i = 0

    while i < len(lines):
        match = _TOOL_CALL_PATTERN.match(lines[i].strip())
        if match:
            tool_name = match.group(1)
            params = (match.group(2) or "").strip()

            content_lines = []
            i += 1
            while i < len(lines):
                if lines[i].strip() == "[/TOOL_CALL]":
                    break
                content_lines.append(lines[i])
                i += 1
            content_body = "\n".join(content_lines)

            safe_print(f"\n[工具系统] 执行工具: {tool_name}, 参数: {params or '(无)'}")
            exec_result = execute_tool(tool_name, params, content_body)
            preview = exec_result[:200] + ("..." if len(exec_result) > 200 else "")
            safe_print(f"[工具系统] 执行结果: {preview}")

            results.append({"tool": tool_name, "params": params, "result": exec_result})
        i += 1

    return results
