"""Agent 调用核心 —— 发送 System Prompt + 用户消息到 LLM，处理工具调用。"""
import json
import time

from ..llm import get_llm
from ..tools import get_tool_definitions, get_tool_schemas, parse_and_execute_tool_calls, execute_tool_structured
from ..utils.safe_print import safe_print
from .definitions import SYSTEM_PROMPTS


def _build_tools_prompt() -> str:
    """根据已注册的工具生成工具说明文本，供 Agent 理解可用工具。"""
    tools = get_tool_definitions()
    if not tools:
        return ""

    lines = [
        "", "## 可用工具",
        "你可以使用以下工具来辅助完成任务。调用格式为 [TOOL_CALL: 工具名: 参数]。",
        "对于需要传入内容的工具（如创建文件），在 [TOOL_CALL: ...] 的下一行开始写内容，",
        "内容结束后用单独一行的 [/TOOL_CALL] 标记结束。", "",
    ]
    for t in tools:
        lines.append(f"- **{t['name']}**: {t['description']}")
        lines.append(f"  参数: {t['parameters']}")
        lines.append("")
    return "\n".join(lines)


def _parse_tool_calls_from_message(tool_calls: list[dict]) -> list[dict]:
    """执行 Function Calling 工具调用列表，返回结果。"""
    if not tool_calls:
        return []

    results = []
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        try:
            arguments = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            arguments = {}

        safe_print(f"\n[{func_name}] Function Calling 执行, arguments: {arguments}")
        exec_result = execute_tool_structured(func_name, arguments)
        preview = exec_result[:200] + ("..." if len(exec_result) > 200 else "")
        safe_print(f"[{func_name}] 执行结果: {preview}")

        results.append({"tool": func_name, "params": str(arguments), "result": exec_result})
    return results


def _delta_emitter(role: str):
    """把 LLM 流式片段按节流发给事件总线（phase=delta，只进内存不落盘）。

    为什么要节流：token 级回调一秒几十次，每个片段都发一条 SSE 事件
    会把前端事件流和内存队列撑爆。攒够 80 字或满 0.3 秒才发一条。
    CLI 直跑（无会话上下文）时 bus.emit 内部会静默忽略。
    """
    try:
        from ..runtime import bus
    except Exception:
        return None

    def on_delta(text: str) -> None:
        nonlocal state
        if not text:
            return
        now = time.time()
        state["buf"] += text
        if now - state["last"] >= 0.3 or len(state["buf"]) >= 80:
            bus.emit(bus.KIND_AGENT, agent=role, phase="delta", content=state["buf"])
            state["buf"] = ""
            state["last"] = now

    state = {"buf": "", "last": 0.0}

    def flush():
        if state["buf"]:
            bus.emit(bus.KIND_AGENT, agent=role, phase="delta", content=state["buf"])
            state["buf"] = ""

    on_delta.flush = flush
    return on_delta


def call_agent(role: str, prompt: str, messages_override: list[dict] = None) -> str:
    """
    调用 LLM，发送角色 system prompt（含工具描述）+ 用户 prompt。
    优先 Function Calling；API 返回 tool_calls 为空时降级到 [TOOL_CALL: ...] 文本解析。
    若传入 messages_override，则使用自定义消息列表（用于多轮对话）。
    """
    system_prompt = SYSTEM_PROMPTS.get(role, "你是一个AI助手，请用中文回复。")
    tools_prompt = _build_tools_prompt()
    if tools_prompt:
        system_prompt = system_prompt + "\n" + tools_prompt

    safe_print(f"\n{'=' * 60}")
    safe_print(f"  [{role}] 正在工作中...")
    safe_print(f"{'=' * 60}")

    tool_schemas = get_tool_schemas()
    llm = get_llm()

    if messages_override:
        messages = list(messages_override)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = messages[0]["content"] + "\n" + tools_prompt
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

    # 流式生成：片段实时推给界面（谁的气泡在长字，就是谁在干活），
    # 返回值和 chat() 完全等价，下面的工具调用处理不受影响。
    on_delta = _delta_emitter(role)
    chat_result = llm.chat_collect(messages=messages,
                                   tools=tool_schemas if tool_schemas else None,
                                   on_delta=on_delta)
    if on_delta is not None:
        on_delta.flush()

    result = chat_result.content or ""

    # 优先级1：标准 Function Calling
    fc_results = _parse_tool_calls_from_message(chat_result.tool_calls)
    if fc_results:
        result += "\n\n## 工具执行结果\n"
        for tr in fc_results:
            result += f"\n- **{tr['tool']}** 执行完成:\n```\n{tr['result']}\n```\n"

    # 优先级2：降级到文本 [TOOL_CALL: ...] 解析
    elif "[TOOL_CALL:" in result:
        safe_print(f"\n[{role}] 检测到文本工具调用指令，开始执行...")
        tool_results = parse_and_execute_tool_calls(result)
        if tool_results:
            result += "\n\n## 工具执行结果\n"
            for tr in tool_results:
                result += f"\n- **{tr['tool']}** 执行完成:\n```\n{tr['result']}\n```\n"

    preview = result[:300] + ("..." if len(result) > 300 else "")
    safe_print(f"[{role}] 输出预览:\n{preview}\n")

    return result
