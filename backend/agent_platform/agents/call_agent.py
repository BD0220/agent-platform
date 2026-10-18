"""Agent 调用核心 —— 发送 System Prompt + 用户消息到 LLM，处理工具调用。

2026-10-13 关键修复：补上**多轮工具循环**。
    旧版是单发：LLM 返回一次 tool_calls，执行完把结果拼进返回值就结束 ——
    子 Agent 永远做不了"看文件 → 写脚本 → 运行 → 写报告"这类多步工作
    （测试员每轮只调一次 list_files 就被掐了，只能空口报"未通过"，
    逼得主 Agent 亲自下场代跑测试，19 轮就是这么烧掉的）。
    现在与主 Agent 的 ReAct 循环对齐：工具结果回填消息列表，让模型继续，
    直到它不再发起工具调用（或达到轮数上限）。
"""
import json
import time
import uuid

from ..llm import get_llm
from ..runtime import tool_events
from ..tools import (
    ToolCallStreamFilter,
    display_text,
    execute_tool_structured,
    get_tool_definitions,
    get_tool_schemas,
    parse_and_execute_tool_calls,
    scan_tool_calls,
    wrap_tool_result,
)
from ..tools import (
    is_tool_error as _is_tool_error,
)
from ..utils.safe_print import safe_print
from .definitions import SYSTEM_PROMPTS

MAX_TOOL_ROUNDS = 6


def _build_tools_prompt() -> str:
    """根据已注册的工具生成工具说明文本，供 Agent 理解可用工具。"""
    tools = get_tool_definitions()
    if not tools:
        return ""

    lines = [
        "", "## 可用工具",
        "你可以使用以下工具来辅助完成任务。调用格式为 [TOOL_CALL: 工具名: 参数]。",
        "对于需要传入内容的工具（如创建文件），在 [TOOL_CALL: ...] 的下一行开始写内容，",
        "内容结束后用单独一行的 [/TOOL_CALL] 标记结束。",
        "工具结果会回传给你，你可以**连续多轮**使用工具：先收集信息，再执行操作，",
        "拿到全部所需结果后，输出最终答复（如测试报告/总结）。", "",
    ]
    for t in tools:
        lines.append(f"- **{t['name']}**: {t['description']}")
        lines.append(f"  参数: {t['parameters']}")
        lines.append("")
    return "\n".join(lines)


def _delta_emitter(role: str):
    """把 LLM 流式片段按节流发给事件总线（phase=delta，只进内存不落盘）。

    为什么要节流：token 级回调一秒几十次，每个片段都发一条 SSE 事件
    会把前端事件流和内存队列撑爆。攒够 80 字或满 0.3 秒才发一条。

    为什么要过滤：模型走文本协议时，``[TOOL_CALL: create_file: index.html]``
    后面跟的是**整篇文件正文**。老版本把这些字节原样当"对话内容"推给前端，
    用户看到的是 3 万字符的 HTML 一个字一个字往外蹦（2026-10 实测的
    "输出很难受"就是这个）。工具块由 tool_events 用卡片单独上报。
    CLI 直跑（无会话上下文）时 bus.emit 内部会静默忽略。
    """
    try:
        from ..runtime import bus
    except Exception:
        return None

    state = {"buf": "", "last": 0.0}
    guard = ToolCallStreamFilter()

    def _push(text: str) -> None:
        if not text:
            return
        bus.emit(bus.KIND_AGENT, agent=role, phase="delta", content=text)

    def on_delta(text: str) -> None:
        if not text:
            return
        visible = guard.feed(text)
        if not visible:
            return
        now = time.time()
        state["buf"] += visible
        if now - state["last"] >= 0.3 or len(state["buf"]) >= 80:
            _push(state["buf"])
            state["buf"] = ""
            state["last"] = now

    def flush():
        tail = guard.flush()
        if tail:
            state["buf"] += tail
        if state["buf"]:
            _push(state["buf"])
            state["buf"] = ""

    on_delta.flush = flush
    on_delta.guard = guard
    return on_delta


def _exec_fc_tool_calls(tool_calls: list[dict], role: str = "") -> list[dict]:
    """执行一组 Function Calling 调用，返回 [{tool, params, result}]。

    每次调用都会向会话事件流上报 start / ok 两条事件（同一个 tool_id 原地更新），
    这样界面上是一行"工具卡片"，而不是一段没人看得懂的系统提示。
    """
    results = []
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        tool_id = tc.get("id") or f"fc-{uuid.uuid4().hex[:8]}"
        try:
            arguments = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        tool_events.emit_tool_start(role, func_name, args=arguments, tool_id=tool_id)
        safe_print(f"\n[{func_name}] Function Calling 执行, arguments: {arguments}")

        started = time.perf_counter()
        try:
            exec_result = execute_tool_structured(func_name, arguments)
        except Exception as e:
            from ..runtime.control import TaskStopped
            if isinstance(e, TaskStopped):
                # 用户终止是控制流信号，必须先上报再原样穿透
                tool_events.emit_tool_result(
                    role, func_name, "任务被用户终止", args=arguments,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    status="error", tool_id=tool_id)
                raise
            exec_result = f"工具执行失败: {e}"
        duration_ms = int((time.perf_counter() - started) * 1000)

        preview = exec_result[:200] + ("..." if len(exec_result) > 200 else "")
        safe_print(f"[{func_name}] 执行结果: {preview}")
        tool_events.emit_tool_result(
            role, func_name, exec_result, args=arguments, duration_ms=duration_ms,
            status="error" if _is_tool_error(exec_result) else "ok", tool_id=tool_id)

        results.append({"tool": func_name, "params": str(arguments), "result": exec_result})
    return results


def call_agent(role: str, prompt: str, messages_override: list[dict] = None,
               display_sink: list[str] | None = None) -> str:
    """
    调用 LLM 完成角色任务，带**多轮工具循环**：
    每轮模型可以发起工具调用（FC 或文本协议），结果回填后继续，
    直到模型给出不含工具调用的最终答复，或达到轮数上限。

    返回值是全部轮次的输出与工具结果的拼接（供状态提取/落盘），
    ``extract_code`` 等下游逻辑依赖其中的 [TOOL_CALL: ...] 块，**不能清洗**。

    ``display_sink``：传入一个 list 时，额外把"给人看"的版本追加进去
    （去掉工具调用块、工具结果区块和 [RESULT] 信封）。展示层用它，
    逻辑层继续用返回值，两者互不污染。
    """
    system_prompt = SYSTEM_PROMPTS.get(role, "你是一个AI助手，请用中文回复。")
    tools_prompt = _build_tools_prompt()
    if tools_prompt:
        system_prompt = system_prompt + "\n" + tools_prompt

    safe_print(f"\n{'=' * 60}")
    safe_print(f"  [{role}] 正在工作中...")
    safe_print(f"{'=' * 60}")

    if messages_override:
        messages = list(messages_override)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = messages[0]["content"] + "\n" + tools_prompt
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

    # 产品经理也流式：之前担心 JSON 刷屏关掉了它，结果成了"等 30 秒
    # 然后出来一堆"的重灾区（2026-10-18 实测反馈）。它的最终输出是
    # Markdown 正文，流式没问题；结尾的 [RESULT] 信封随正文一起滚出即可。
    on_delta = _delta_emitter(role)
    tool_schemas = get_tool_schemas()
    llm = get_llm()

    parts: list[str] = []          # 每轮输出 + 工具结果，最后拼接返回
    visible_parts: list[str] = []  # 只放"给人看"的正文（展示层用）
    finished = False               # 是否拿到了"不含工具调用的最终答复"
    try:
        for _ in range(MAX_TOOL_ROUNDS + 1):
            # 协作式取消检查点：子 Agent 一次有多达 7 轮 LLM + 工具，
            # 中间没有检查点的话，用户点了终止要等它彻底跑完才生效。
            from ..runtime.control import get_control
            get_control().check_stop()
            # T3：超预算时压缩早期轮次（子 Agent 循环短，通常不触发；
            # 程序员修复轮会带上一版代码+测试报告，很容易超）
            from .context import assemble_messages
            messages, _ctx_stats = assemble_messages(messages)
            if _ctx_stats.get("compressed"):
                safe_print(f"[上下文] 超预算压缩: {_ctx_stats['before']} → "
                           f"{_ctx_stats['after']} 字（折叠 {_ctx_stats['collapsed']} 条）")
            chat_result = llm.chat_collect(
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
                on_delta=on_delta,
            )
            if on_delta is not None:
                on_delta.flush()
            text = chat_result.content or ""
            parts.append(text)
            _visible = display_text(text)
            if _visible:
                visible_parts.append(_visible)

            # 路径1：标准 Function Calling —— 执行后回填，继续下一轮
            if chat_result.tool_calls:
                messages.append({"role": "assistant", "content": text,
                                 "tool_calls": chat_result.tool_calls})
                fc_results = _exec_fc_tool_calls(chat_result.tool_calls, role=role)
                for tc, tr in zip(chat_result.tool_calls, fc_results, strict=False):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": tr["tool"],
                        "content": tr["result"],
                    })
                # 工具结果也要进返回值：否则模型全程只发工具调用时
                # （轮数耗尽没来得及给最终答复），返回值就是空串，
                # 上层的契约校验只能报 empty_output（2026-10-18 实测）
                parts.append(wrap_tool_result("\n\n".join(
                    f"## {tr['tool']} 执行结果\n{tr['result']}" for tr in fc_results)))
                continue

            # 路径2：文本协议 [TOOL_CALL: ...] —— 同样回填继续
            if "[TOOL_CALL:" in text:
                # 先按顺序上报"运行中"，再执行；执行结果按同样的顺序对回同一个 tool_id
                scanned = scan_tool_calls(text)
                tool_ids = [f"txt-{uuid.uuid4().hex[:8]}" for _ in scanned]
                for call, tid in zip(scanned, tool_ids, strict=False):
                    tool_events.emit_tool_start(role, call["tool"], params=call["params"],
                                                tool_id=tid)
                started = time.perf_counter()
                tool_results = parse_and_execute_tool_calls(text)
                duration_ms = int((time.perf_counter() - started) * 1000)
                for index, tr in enumerate(tool_results):
                    tid = tool_ids[index] if index < len(tool_ids) else f"txt-{uuid.uuid4().hex[:8]}"
                    tool_events.emit_tool_result(
                        role, tr["tool"], tr["result"],
                        params=tr.get("params", ""),
                        # 文本协议是一批一起执行的，耗时按调用数平摊更诚实
                        duration_ms=duration_ms // max(1, len(tool_results)),
                        status="error" if _is_tool_error(tr["result"]) else "ok",
                        tool_id=tid)
                if tool_results:
                    messages.append({"role": "assistant", "content": text})
                    results_text = "\n\n".join(
                        f"## {tr['tool']} 执行结果\n{tr['result']}" for tr in tool_results)
                    parts.append(wrap_tool_result(results_text))
                    messages.append({"role": "user", "content":
                                     results_text + ("\n\n工具结果已返回。请继续下一步；"
                                                     "若工作已全部完成，请输出最终答复。")})
                    continue

            # 无工具调用 → 最终答复，结束循环
            finished = True
            break
    finally:
        if on_delta is not None:
            on_delta.flush()

    if not finished:
        # 轮数耗尽还没给最终答复：强制收尾一轮（不给工具），保证有产出。
        # 不做这一步，模型把轮数全花在工具上时返回值就是空的。
        safe_print(f"[{role}] 工具轮数耗尽，强制要求输出最终答复")
        messages.append({"role": "user", "content":
                         "已达到工具调用轮数上限。请不要再调用任何工具，"
                         "立即基于以上已获得的信息输出最终答复（报告/结论/代码）。"})
        try:
            chat_result = llm.chat_collect(messages=messages, tools=None, on_delta=on_delta)
            if on_delta is not None:
                on_delta.flush()
            forced = chat_result.content or ""
            parts.append(forced)
            _visible = display_text(forced)
            if _visible:
                visible_parts.append(_visible)
        except Exception as e:
            safe_print(f"[{role}] 强制收尾轮失败: {e}")

    result = "\n\n".join(p for p in parts if p)
    preview = result[:300] + ("..." if len(result) > 300 else "")
    safe_print(f"[{role}] 输出预览:\n{preview}\n")
    if display_sink is not None:
        display_sink.append("\n\n".join(p for p in visible_parts if p))
    return result
