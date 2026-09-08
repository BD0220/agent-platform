"""统一工具注册表 — 合并工具执行 + Schema 定义，一次注册全部到位。"""
import json
from dataclasses import dataclass, field
from typing import Callable, Any

from ..utils.safe_print import safe_print


@dataclass
class ToolDefinition:
    """工具定义：执行函数 + 人类描述 + Function Calling Schema，一次注册全部到位。"""
    name: str
    description: str
    parameters_desc: str  # 人类可读的参数说明（用于 system prompt 注入）
    function: Callable  # fn(params: str, content: str) -> str
    schema: dict = field(default_factory=dict)  # Function Calling JSON Schema
    param_mapper: Callable | None = None  # fn(tool_name, arguments) -> (params, content)


# 全局注册表
_registry: dict[str, ToolDefinition] = {}


def register_tool(
    name: str,
    description: str,
    parameters_desc: str,
    func: Callable,
    schema: dict = None,
    param_mapper: Callable = None,
):
    """注册一个工具（自动生成 Function Calling Schema 如果未提供）。"""
    if schema is None:
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    _registry[name] = ToolDefinition(
        name=name,
        description=description,
        parameters_desc=parameters_desc,
        function=func,
        schema=schema,
        param_mapper=param_mapper,
    )
    safe_print(f"[工具系统] 已注册工具: {name}")


def get_tool_definitions() -> list[dict]:
    """返回人类可读的工具定义列表（用于 system prompt 注入）。"""
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters_desc}
        for t in _registry.values()
    ]


def get_tool_names() -> list[str]:
    return list(_registry.keys())


def get_tool_schemas() -> list[dict]:
    """返回 Function Calling Schema 列表（兼容 OpenAI tools 参数）。"""
    return [t.schema for t in _registry.values()]


def get_schema(tool_name: str) -> dict | None:
    t = _registry.get(tool_name)
    return t.schema if t else None


def map_arguments(tool_name: str, arguments: dict) -> tuple[str, str]:
    """将 Function Calling 的结构化 arguments 转为 (params, content)。"""
    t = _registry.get(tool_name)
    if t and t.param_mapper:
        return t.param_mapper(tool_name, arguments)
    return arguments.get("params", ""), arguments.get("content", "")


def validate_arguments(tool_name: str, arguments: dict) -> dict:
    """校验工具参数是否符合 Schema 定义。"""
    t = _registry.get(tool_name)
    if not t:
        return {"valid": False, "errors": [f"未知工具: {tool_name}"]}
    try:
        import jsonschema
        param_schema = t.schema["function"]["parameters"]
        jsonschema.validate(instance=arguments, schema=param_schema)
        return {"valid": True}
    except ImportError:
        required = t.schema.get("function", {}).get("parameters", {}).get("required", [])
        missing = [r for r in required if r not in arguments or not arguments[r]]
        if missing:
            return {"valid": False, "errors": [f"缺少必填参数: {', '.join(missing)}"]}
        return {"valid": True}
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}


def _get_tool(name: str) -> ToolDefinition | None:
    return _registry.get(name)
