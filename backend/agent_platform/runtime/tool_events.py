"""工具调用的可视化上报。

为什么单独一个模块：
    工具调用是"Agent 在工作"的唯一硬证据，但它的原始结果是给模型看的
    （可能是几万字的文件内容），直接塞进聊天区会把界面糊死。
    这里负责把一次调用压成前端能画的一行卡片：
        （工具名 / 目标 / 状态 / 耗时 / 结果大小 / 精简预览）

设计取舍：
    - **预览有上限**：默认 700 字，超出部分标记 truncated=true，
      前端提示"完整内容见交付目录或日志"，而不是让整段结果穿到浏览器；
    - **敏感内容不外泄**：预览只发结果开头，不拼参数里的完整内容
      （create_file 的参数就是整个文件正文，绝不能当"目标"发出去）；
    - **失败也要上报**：status=error 时同样发一条，前端画红点，
      否则用户只会看到 Agent 卡住却没有原因。
"""
from __future__ import annotations

from ..utils.safe_print import safe_print

PREVIEW_LIMIT = 700

# 参数里能代表"操作对象"的键，按优先级取第一个非空值
_TARGET_KEYS = ("agent_name", "filename", "file", "filepath", "path", "name",
                "target", "url", "query")


def _text(value) -> str:
    return value if isinstance(value, str) else str(value or "")


def summarize_args(tool: str, args: dict | None = None, params: str = "") -> str:
    """从参数里提炼一个"目标"短语，用于卡片上显示操作对象。

    create_file 这类工具的参数就是整篇文件正文，所以只认白名单键，
    绝不把 args 整体 stringify 出来。
    """
    if isinstance(args, dict):
        for key in _TARGET_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:120]
    raw = _text(params).strip()
    if not raw:
        return ""
    # 文本协议下参数就是一行 "index.html"，取冒号后的部分并截断
    first = raw.splitlines()[0].strip()
    return first[:120]


def preview_of(result: str, limit: int = PREVIEW_LIMIT) -> tuple[str, bool]:
    """返回 (预览, 是否被截断)。空结果返回空串。"""
    text = _text(result)
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "…", True


def emit_tool_start(agent: str, tool: str, args: dict | None = None,
                    params: str = "", tool_id: str = "") -> None:
    """工具开始执行：先画一行"运行中"，长工具期间界面不会是死寂的。"""
    _emit(agent, tool, args=args, params=params, result="", status="start",
          duration_ms=0, tool_id=tool_id)


def emit_tool_result(agent: str, tool: str, result: str, *, args: dict | None = None,
                     params: str = "", duration_ms: int = 0, status: str = "ok",
                     tool_id: str = "") -> None:
    """工具执行结束：原地更新那一行（前端按 tool_id 替换，不新增一行）。"""
    _emit(agent, tool, args=args, params=params, result=result, status=status,
          duration_ms=duration_ms, tool_id=tool_id)


def _emit(agent: str, tool: str, *, args, params, result: str, status: str,
          duration_ms: int, tool_id: str) -> None:
    try:
        from . import bus
    except Exception:  # pragma: no cover - 事件总线不可用时静默降级
        return
    preview, truncated = preview_of(result)
    try:
        bus.emit(
            bus.KIND_TOOL,
            agent=agent or "",
            tool=tool or "",
            target=summarize_args(tool, args, params),
            status=status,
            duration_ms=duration_ms,
            chars=len(_text(result)),
            preview=preview,
            truncated=truncated,
            tool_id=tool_id or "",
        )
    except Exception as e:  # pragma: no cover - 上报失败不该影响任务
        safe_print(f"[工具上报] 事件发送失败: {e}")
