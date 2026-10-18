"""工具执行引擎 —— 查找、校验、执行、重试。"""
from .registry import _get_tool, get_tool_names, map_arguments, validate_arguments
from ..utils.safe_print import safe_print


def _make_error(error_type: str, message: str, tool_name: str = "") -> str:
    parts = [f"[工具错误]"]
    if tool_name:
        parts[0] += f" 工具={tool_name}"
    parts.append(f"类型: {error_type}")
    parts.append(f"描述: {message}")
    return "\n".join(parts)


def execute_tool(tool_name: str, params: str = "", content: str = "", retry: bool = True) -> str:
    """按名称执行工具，支持自动重试。"""
    tool = _get_tool(tool_name)
    if not tool:
        return _make_error("UNKNOWN_TOOL", f"未知工具 '{tool_name}'，可用: {', '.join(get_tool_names())}", tool_name)

    last_error = None
    for attempt in range(2 if retry else 1):
        try:
            return tool.function(params, content)
        except Exception as e:
            last_error = str(e)
            if attempt == 0:
                safe_print(f"[工具系统] {tool_name} 执行失败 (第1次)，准备重试: {e}")

    return _make_error("EXECUTION_FAILED", f"工具 '{tool_name}' 执行失败（已重试）: {last_error}", tool_name)


def execute_tool_structured(tool_name: str, arguments: dict, retry: bool = True) -> str:
    """Function Calling 模式：接受结构化 arguments，校验后执行。"""
    validation = validate_arguments(tool_name, arguments)
    if not validation["valid"]:
        return _make_error("INVALID_ARGUMENTS", "; ".join(validation.get("errors", ["校验失败"])), tool_name)

    params, content = map_arguments(tool_name, arguments)
    return execute_tool(tool_name, params, content, retry=retry)
