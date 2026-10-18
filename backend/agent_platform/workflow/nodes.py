"""LangGraph ReAct Agent 节点函数。
agent_node → (tool_calls?) → tool_node → agent_node → ... → conclusion
主Agent 动态决策调度子Agent，而非固定流程。
"""
import hashlib
import json
import os
import re
import time
import uuid

from .. import memory
from ..agents.definitions import SYSTEM_PROMPTS
from ..llm import get_llm
from ..logging.logger import log_task_complete, log_task_start
from ..rag import advanced_search
from ..rag import format_memories_for_context as rag_format_memories
from ..runtime import tool_events
from ..storage.database import init_db
from ..storage.state import (
    cleanup_state,
    extract_task_name,
    get_current_task_dir,
    get_current_task_id,
    load_state,
    save_state,
    set_current_task,
)
from ..tools import execute_tool_structured, get_tool_definitions, get_tool_schemas
from ..tools.execution import is_tool_error as _is_tool_error
from ..tools.parser import parse_and_execute_tool_calls, scan_tool_calls
from ..utils.safe_print import safe_print
from ..utils.text import clean_markdown
from .state import WorkflowState


def init_node(state: WorkflowState) -> dict:
    """初始化：数据库、任务名、交付目录、task_id 上下文。"""
    init_db()

    user_request = state["user_request"]
    task_name = extract_task_name(user_request)

    # 优先使用队列传入的 task_id；没有则生成一个（CLI / 直接调用场景）
    task_id = state.get("task_id") or get_current_task_id()
    if task_id:
        set_current_task(task_id)

    # 顺序很重要：get_current_task_dir() 是从 state 里读任务名来拼目录名的，
    # 所以必须先落 task_name / user_request，否则每一步都读不到，
    # 交付目录就只能退化成 "任务_日期时间" 这种没意义的名字。
    save_state({"task_name": task_name, "user_request": user_request})
    delivery_dir = get_current_task_dir()
    log_task_start(task_name, user_request)

    safe_print(f"\n[工作流] INIT: 任务 '{task_name}' (task_id={task_id or 'cli'})")
    return {
        "task_name": task_name,
        "task_id": task_id or "",
        "delivery_dir": delivery_dir,
        "turns": 0,
        "max_turns": 30,   # 一轮完整协作（含测试员多轮工具循环）≈15-25 轮，20 会掐掉复测
        "completed": False,
        "status": "running",
        "messages": [],
        # 预算治理：turns 之外再加"调用总数 / 同参重复"两道闸
        "tool_call_count": 0,
        "tool_fingerprints": {},
        "max_tool_calls": DEFAULT_MAX_TOOL_CALLS,
        "max_repeat_calls": DEFAULT_MAX_REPEAT_CALLS,
        "budget_exhausted": False,
    }


def retrieve_node(state: WorkflowState) -> dict:
    """RAG 记忆检索，将历史经验注入消息列表。"""
    user_request = state["user_request"]
    messages = list(state.get("messages", []))

    try:
        relevant = advanced_search(user_request, top_k=3)
        if relevant:
            mem_ctx = rag_format_memories(relevant)
            safe_print(f"[工作流] RAG 命中 {len(relevant)} 条经验")
            messages.append({"role": "system", "content": mem_ctx})
    except Exception as e:
        safe_print(f"[工作流] RAG 失败: {e}")

    return {"messages": messages, "status": "retrieved"}


def _build_system_prompt() -> str:
    """构建主Agent 的 system prompt，包含工具说明。"""
    system = SYSTEM_PROMPTS.get("主Agent", "你是总调度。")
    system += (
        "\n\n## 你的工作方式"
        "\n你是多智能体协作平台的总调度。你可以使用 Function Calling 调用工具。"
        "\n你的核心工具是 **dispatch_agent**，用于将任务分派给子Agent：产品经理、程序员、测试员。"
        "\n\n## 执行策略"
        "\n1. 分析用户需求，制定执行计划"
        "\n2. 先 dispatch 产品经理 → 审查其输出是否满足需求"
        "\n3. dispatch 程序员 → 审查代码是否正确完整"
        "\n4. dispatch 测试员 → 如测试不通过，dispatch 程序员修复，然后重新测试"
        "\n5. 审查每个子Agent 的输出，达标则推进，不达标则退回重做"
        "\n6. 全部完成后输出 DONE"
        "\n\n## 工具使用"
        "\n- dispatch_agent: 分派任务给子Agent。参数 agent_name(产品经理/程序员/测试员), task(详细任务描述)"
        "\n- search_knowledge: 搜索知识库获取参考资料"
        "\n- web_search: 搜索互联网获取最新信息"
        "\n- 你也可以使用其他文件操作工具"
    )

    tools = get_tool_definitions()
    if tools:
        lines = ["\n### 所有可用工具"]
        for t in tools:
            lines.append(f"- **{t['name']}**: {t['description']}")
        system += "\n" + "\n".join(lines)

    return system


def _stop_checkpoint(where: str) -> None:
    """协作式取消的检查点。

    终止不是杀线程，而是"下一个检查点抛 TaskStopped"。以前检查点只有交接处，
    而主循环 agent_node ⇄ tool_node **一个都没有** —— 于是模型不调 dispatch、
    只是自己反复调工具时，用户点了终止要等到下一次交接才生效，表现就是
    "终止按钮不好使"（2026-10-19 实测）。现在每轮进节点先查一次。
    """
    try:
        from ..runtime.control import get_control
        get_control().check_stop()
    except ImportError:
        return
    except Exception as e:
        # TaskStopped 必须向上穿透；其他异常（无会话上下文等）不影响执行
        from ..runtime.control import TaskStopped
        if isinstance(e, TaskStopped):
            safe_print(f"[工作流] 在 {where} 检查点命中终止指令，退出")
            raise
        safe_print(f"[工作流] 停止检查跳过（{where}）: {e}")


def agent_node(state: WorkflowState) -> dict:
    """主Agent 节点：调用 LLM 决定下一步行动。"""
    _stop_checkpoint("agent_node")
    user_request = state["user_request"]
    messages = list(state.get("messages", []))
    turns = state.get("turns", 0)

    # 首轮：构建完整消息列表
    if turns == 0:
        system_prompt = _build_system_prompt()
        messages = [{"role": "system", "content": system_prompt}]
        # 追加已有的 system 消息（如 RAG 记忆）
        for m in state.get("messages", []):
            if m.get("role") == "system":
                messages.append(m)

        user_content = (
            f"## 用户需求\n{user_request}\n\n"
            f"请制定执行计划，然后通过 dispatch_agent 逐步调度子Agent 完成任务。"
            f"每个子Agent 完成后请审查输出，达标则推进，不达标则退回重做。全部完成后输出 DONE。"
        )
        messages.append({"role": "user", "content": user_content})

    safe_print(f"\n{'=' * 60}")
    safe_print(f"  [主Agent] 第 {turns + 1} 轮思考")
    safe_print(f"{'=' * 60}")

    tool_schemas = get_tool_schemas()
    llm = get_llm()

    # T3：超预算时压缩早期轮次（证据类消息不压），治 max_turns 硬截断失忆
    from ..agents.context import assemble_messages
    messages, _ctx_stats = assemble_messages(messages)
    if _ctx_stats.get("compressed"):
        safe_print(f"[上下文] 超预算压缩: {_ctx_stats['before']} → {_ctx_stats['after']} 字"
                   f"（折叠 {_ctx_stats['collapsed']} 条早期轮次）")

    try:
        # 主 Agent 走流式（delta 只进内存不落盘）。注意：**不发** working/done
        # 事件 —— 每轮都发的话前端每轮画两张"收到任务"卡，把时间线刷成
        # 垃圾场（2026-10-18 实测）。整轮一条流式气泡，空轮（只调工具
        # 不说话）不画，工具动静由 tool_node 的系统徽章负责。
        from ..agents.call_agent import _delta_emitter
        on_delta = _delta_emitter("主Agent")
        try:
            result = llm.chat_collect(
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
                on_delta=on_delta,
            )
        finally:
            if on_delta is not None:
                on_delta.flush()
    except Exception as e:
        safe_print(f"[工作流] LLM 调用失败: {e}")
        # 带上原因：error 分支会直接跳到收尾节点，不写下来的话用户只会看到
        # 一句"测试未通过"，完全看不出其实是模型没配好。
        return {"messages": messages, "turns": turns + 1, "status": "error",
                "error": f"{type(e).__name__}: {e}"}

    content = result.content or ""
    preview = content[:300] + ("..." if len(content) > 300 else "")
    safe_print(f"[主Agent] 输出预览:\n{preview}")

    assistant_msg = {"role": "assistant", "content": content}

    # 附加 tool_calls（Function Calling 格式）
    if result.tool_calls:
        assistant_msg["tool_calls"] = result.tool_calls
        tc_names = [tc["function"]["name"] for tc in result.tool_calls]
        safe_print(f"[主Agent] 发起工具调用: {tc_names}")
    elif "[TOOL_CALL:" in content:
        # 文本格式工具调用 → 标记出来让 tool_node 处理
        safe_print("[主Agent] 检测到文本格式工具调用")
        assistant_msg["_text_tool_calls"] = True

    messages.append(assistant_msg)
    # T5：执行计划显式落盘（图状态 + 存储状态双写）。
    # 图状态给总结/回放用；存储状态给 dispatch 的"计划确认闸门"用——
    # 那边拿不到图状态，只能读 state 文件。
    extra = {}
    has_sub_output = any(load_state().get(k) for k in ("功能清单", "代码", "测试报告"))
    if turns == 0 and content.strip():
        extra["plan"] = {"raw": content[:2000]}
        save_state({"任务规划": {"raw": content[:2000]}})
    elif not result.tool_calls and not has_sub_output and content.strip():
        # 规划阶段（尚未有任何子Agent 产出）的纯文本输出都视为计划或计划修订，
        # 这样计划被打回后主Agent 重写的版本也能进闸门，而不是永远展示旧计划
        save_state({"任务规划": {"raw": content[:2000]}})
    return {**extra, "messages": messages, "turns": turns + 1, "status": "thinking"}


DEFAULT_MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "60"))
DEFAULT_MAX_REPEAT_CALLS = int(os.environ.get("AGENT_MAX_REPEAT_CALLS", "3"))

# 这些工具的"同样参数再来一次"是合法的：代码改完还要再跑一遍，
# 目录内容也可能刚变。只受总量闸约束，不受重复闸约束。
REPEAT_EXEMPT_TOOLS = {"run_python_file", "list_files", "read_file"}


def _fingerprint(tool: str, args: dict | str) -> str:
    """调用指纹：工具名 + 归一化参数。用于发现"同一个调用反复执行"。"""
    payload = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _budget_reject_reason(count: int, seen: int, tool: str,
                          max_calls: int, max_repeat: int) -> str:
    """返回空串表示放行，否则返回要喂回模型的拦截说明。"""
    if count >= max_calls:
        return (f"[预算拦截] 本次任务已执行 {count} 次工具调用，达到上限 {max_calls}。"
                f"请立即基于已有信息给出最终结论，不要继续调用工具。")
    if seen >= max_repeat and tool not in REPEAT_EXEMPT_TOOLS:
        return (f"[预算拦截] {tool} 用同样参数已经执行过 {seen} 次，没有产生新信息。"
                f"请换一种做法，或基于已有结果给出结论。")
    return ""


def tool_node(state: WorkflowState) -> dict:
    """工具执行节点：执行主Agent 发起的工具调用，返回结果。"""
    _stop_checkpoint("tool_node")
    messages = list(state.get("messages", []))

    if not messages:
        return {"messages": messages}

    last_msg = messages[-1]
    if last_msg.get("role") != "assistant":
        return {"messages": messages}

    # ── 预算闸门（2026-10-19）──
    # 只看 turns 挡不住"一轮里无限调工具"，也挡不住"同一个调用反复跑"。
    # 这里按"总数 + 同参重复次数"两道闸限流，命中就喂回一条说明让模型自己收尾，
    # 而不是直接杀线程 —— 已经拿到的结果必须能交出去。
    count = int(state.get("tool_call_count") or 0)
    fingerprints = dict(state.get("tool_fingerprints") or {})
    max_calls = int(state.get("max_tool_calls") or DEFAULT_MAX_TOOL_CALLS)
    max_repeat = int(state.get("max_repeat_calls") or DEFAULT_MAX_REPEAT_CALLS)
    blocked_reason = ""

    # 路径1: 标准 Function Calling tool_calls
    tool_calls = last_msg.get("tool_calls", [])
    if tool_calls:
        safe_print(f"\n[工具执行] 执行 {len(tool_calls)} 个 Function Calling 调用")
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            tool_id = tc.get("id") or f"fc-{uuid.uuid4().hex[:8]}"
            try:
                import json as _json
                arguments = _json.loads(tc["function"]["arguments"])
            except Exception:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}

            fp = _fingerprint(func_name, arguments)
            seen = int(fingerprints.get(fp, 0))
            reject = _budget_reject_reason(count, seen, func_name, max_calls, max_repeat)
            if reject:
                # 被拦下也必须补一条 tool 消息：Function Calling 协议要求
                # 每个 tool_call 都有对应的 tool 结果，否则下一轮请求会 400。
                blocked_reason = reject
                safe_print(f"[预算拦截] {func_name}: {reject}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": func_name,
                    "content": reject,
                })
                continue

            count += 1
            fingerprints[fp] = seen + 1
            # 工具动静上屏：长工具（读大文件/跑脚本）期间界面不再是死寂一片
            tool_events.emit_tool_start("主Agent", func_name, args=arguments, tool_id=tool_id)
            started = time.perf_counter()
            try:
                exec_result = execute_tool_structured(func_name, arguments)
            except Exception as e:
                # 终止信号必须向上穿透。之前这里是一把抓的 except Exception，
                # 把 TaskStopped 也当成"工具执行失败"变成一段字符串喂回模型，
                # 于是用户点了终止，Agent 却当作一次普通失败继续往下跑，
                # 最后还正常收尾成"任务完成"—— 终止按钮等于失效。
                from ..runtime.control import TaskStopped
                if isinstance(e, TaskStopped):
                    tool_events.emit_tool_result(
                        "主Agent", func_name, "任务被用户终止", args=arguments,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        status="error", tool_id=tool_id)
                    raise
                exec_result = f"工具执行失败: {e}"
            duration_ms = int((time.perf_counter() - started) * 1000)
            preview = exec_result[:150] + ("..." if len(exec_result) > 150 else "")
            safe_print(f"[{func_name}] → {preview}")
            tool_events.emit_tool_result(
                "主Agent", func_name, exec_result, args=arguments,
                duration_ms=duration_ms,
                status="error" if _is_tool_error(exec_result) else "ok", tool_id=tool_id)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": func_name,
                "content": exec_result,
            })
        return {
            "messages": messages,
            "tool_call_count": count,
            "tool_fingerprints": fingerprints,
            "budget_exhausted": bool(blocked_reason) and count >= max_calls,
        }

    # 路径2: 文本格式 [TOOL_CALL: ...]
    if last_msg.get("_text_tool_calls"):
        content = last_msg.get("content", "")
        safe_print("\n[工具执行] 解析文本工具调用...")
        scanned = scan_tool_calls(content)
        tool_ids = [f"txt-{uuid.uuid4().hex[:8]}" for _ in scanned]

        # 先过预算闸：只给放行的调用发"运行中"卡片
        approved_ids = {}
        for call, tid in zip(scanned, tool_ids, strict=False):
            fp = _fingerprint(call["tool"], call.get("params", ""))
            seen = int(fingerprints.get(fp, 0))
            reject = _budget_reject_reason(count, seen, call["tool"], max_calls, max_repeat)
            if reject:
                blocked_reason = reject
                safe_print(f"[预算拦截] {call['tool']}: {reject}")
                approved_ids[tid] = reject
                continue
            count += 1
            fingerprints[fp] = seen + 1
            tool_events.emit_tool_start("主Agent", call["tool"], params=call["params"],
                                        tool_id=tid)

        started = time.perf_counter()
        tool_results = parse_and_execute_tool_calls(content)
        duration_ms = int((time.perf_counter() - started) * 1000)
        for index, tr in enumerate(tool_results):
            tid = tool_ids[index] if index < len(tool_ids) else f"txt-{uuid.uuid4().hex[:8]}"
            tool_events.emit_tool_result(
                "主Agent", tr["tool"], tr["result"], params=tr.get("params", ""),
                duration_ms=duration_ms // max(1, len(tool_results)),
                status="error" if _is_tool_error(tr["result"]) else "ok", tool_id=tid)
        if blocked_reason:
            tool_results.append({"tool": "预算闸门", "params": "", "result": blocked_reason})
        if tool_results:
            results_text = "\n\n".join(
                f"## {tr['tool']} 执行结果\n{tr['result']}" for tr in tool_results
            )
            messages.append({"role": "user", "content": results_text})
        return {
            "messages": messages,
            "tool_call_count": count,
            "tool_fingerprints": fingerprints,
            "budget_exhausted": bool(blocked_reason) and count >= max_calls,
        }

    return {"messages": messages}


def _has_tool_calls(state: WorkflowState) -> bool:
    """判断最后一条消息是否包含待执行的工具调用。"""
    messages = state.get("messages", [])
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") != "assistant":
        return False
    if last.get("tool_calls"):
        return True
    if last.get("_text_tool_calls"):
        return True
    return False


def _max_turns_reached(state: WorkflowState) -> bool:
    return state.get("turns", 0) >= state.get("max_turns", 20)


def route_after_agent(state: WorkflowState) -> str:
    """agent_node 之后的路由：有工具调用 → tool_node；准备收尾 → review（独立审查）；
    轮数耗尽 / 模型调用出错 → 直接 conclusion。"""
    # 错误分流（必须放在最前面）：LLM 抛错时 agent_node 会把 status 置成 "error"，
    # 但老逻辑不看 status —— 于是 review 拿到空产出判 reject，回到 agent 再失败，
    # 一次 401 / 超时会白烧掉几十轮。出错就直线收尾，把已拿到的产出交出去。
    if state.get("status") == "error":
        safe_print("[工作流] 上一轮模型调用失败，跳过 review 直接收尾")
        return "conclusion"
    if _max_turns_reached(state):
        safe_print(f"[工作流] 达到最大轮数 {state.get('max_turns')}，强制收尾")
        return "conclusion"
    if _has_tool_calls(state):
        return "tools"
    return "review"


def route_after_tools(state: WorkflowState) -> str:
    """tool_node 之后的路由：回到 agent_node 继续思考；预算耗尽则强制收尾。"""
    if state.get("budget_exhausted"):
        safe_print("[工作流] 工具调用预算已耗尽，强制收尾")
        return "conclusion"
    if _max_turns_reached(state):
        return "conclusion"
    return "agent"


def review_node(state: WorkflowState) -> dict:
    """独立审查节点（T2）：主 Agent 宣布完成前，先过独立验收。

    与主 Agent 自审的区别：审查员只看交付物和 T1 契约记录，不看对话历史
    （独立上下文）；机械契约不过直接打回（零成本）；LLM 审查查需求覆盖度。
    打回意见作为 user 消息回填，主 Agent 据此退回对应子 Agent 重做。
    打回满 2 次仍不过 → 放行去 conclusion 如实收尾，不无限循环（LLM 成本可控）。
    """
    from ..agents.reviewer import review_task
    from ..runtime import bus
    from ..utils.routing import needs_product_manager

    # 简单任务不做独立审查（2026-10-18 用户实测：让写个 hello 前端页面，审查员
    # 连续打回 2 次，任务被反复退回重做）。简单任务没有需求清单可比对，审查员
    # 只能拿"验收标准不明确"这类理由否决，纯属空转烧钱。
    # AGENT_REVIEW=0 全局关闭；=1 强制审查；默认 auto = 简单任务跳过。
    _review_mode = str(os.environ.get("AGENT_REVIEW", "auto")).strip().lower()
    _simple = not needs_product_manager(str(state.get("user_request") or ""))
    if _review_mode in ("0", "false", "off") or (_review_mode == "auto" and _simple):
        safe_print("[审查员] 简单任务 / 审查已关闭 → 跳过独立审查，直接交付")
        bus.emit(bus.KIND_SYSTEM, content="⚡ 简单任务：跳过独立审查，直接交付")
        return {"review_verdict": "pass",
                "review_count": state.get("review_count", 0),
                "review_skipped": True}

    review_count = state.get("review_count", 0) + 1
    safe_print(f"\n[审查员] 第 {review_count} 次独立审查...")

    result = review_task()
    verdict = result.get("verdict", "reject")

    if verdict == "pass":
        bus.emit(bus.KIND_AGENT, agent="审查员", phase="done",
                 content=f"独立审查通过：{result.get('summary', '符合验收标准')}",
                 status="审查通过")
        return {"review_verdict": "pass", "review_count": review_count}

    findings = result.get("findings") or []
    problems = result.get("problems") or []
    lines = ["【独立审查员打回，请按意见退回对应角色重做，不要自行修改结论】"]
    for f in findings:
        line = f"- {f.get('type', '问题')}: {f.get('detail', '')}"
        if f.get("evidence"):
            line += f"（依据: {f['evidence']}）"
        lines.append(line)
    if problems:
        lines.append("【机械契约问题】")
        lines.extend(f"- {p}" for p in problems)
    feedback = "\n".join(lines)

    bus.emit(bus.KIND_AGENT, agent="审查员", phase="done",
             content=feedback, status="审查打回")
    messages = list(state.get("messages", []))
    messages.append({"role": "user", "content": feedback})
    return {"review_verdict": "reject", "review_count": review_count,
            "review_feedback": feedback, "messages": messages}


def route_after_review(state: WorkflowState) -> str:
    """review_node 之后：通过 → conclusion；打回未满 2 次 → agent 重做；
    打回满 2 次或轮数耗尽 → conclusion（如实收尾，不无限循环）。"""
    if state.get("review_verdict") == "pass":
        return "conclusion"
    if state.get("review_count", 0) >= 2:
        safe_print("[审查员] 已打回 2 次仍不过 → 如实收尾，不无限重做")
        return "conclusion"
    if _max_turns_reached(state):
        return "conclusion"
    return "agent"


# 交付物里的流程标记词 —— 判断"内容是否残缺"时要先剔掉
_MARKER_RE = re.compile(
    r"\[(?:程序员已生成代码|产品经理已生成需求分析|测试通过|测试未通过)\]")


def _write_deliverable(path: str, text: str, min_len: int = 60):
    """安全的交付物写入：残缺内容绝不覆盖已有的完整文件。

    实测踩坑：多轮重做后某轮程序员输出退化成一句 ``[程序员已生成代码]``，
    这里直接拿它把磁盘上 19KB 的真代码覆盖没了。规则：
      - 新内容剔掉标记词后短于 min_len → 不写（保留旧文件）；
      - 旧文件比新内容更长 → 不写（旧版更完整）。
    """
    text = (text or "").strip()
    core = _MARKER_RE.sub("", text).strip()
    if len(core) < min_len:
        safe_print(f"[交付物] 新内容过短（{len(core)} 字），保留原文件: {os.path.basename(path)}")
        return
    try:
        old = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                old = f.read().strip()
    except OSError:
        old = ""
    if len(old) > len(text):
        safe_print(f"[交付物] 旧文件更完整（{len(old)} > {len(text)}），不覆盖: {os.path.basename(path)}")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def conclusion_node(state: WorkflowState) -> dict:
    """收尾：写交付物、沉淀记忆、清理上下文。"""
    safe_print("\n[工作流] CONCLUSION: 生成交付物...")

    user_request = state.get("user_request", "")
    delivery_dir = state.get("delivery_dir", "")
    messages = state.get("messages", [])

    # 从消息中提取子Agent 产出
    requirements = ""
    code = ""
    test_report = ""

    for m in messages:
        if m.get("role") == "tool" and m.get("name") == "dispatch_agent":
            content = m.get("content", "")
            if "[产品经理已生成需求分析]" in content or "功能清单" in content:
                requirements = content
            elif "[程序员已生成代码]" in content or "```python" in content:
                code_match = re.search(r'```python\s*\n(.*?)```', content, re.DOTALL)
                if code_match:
                    code = code_match.group(1)
                else:
                    code = content
            elif "测试通过" in content or "测试未通过" in content:
                test_report = content

    # 从 current state 补充
    full_state = load_state()
    if not requirements:
        requirements = full_state.get("功能清单", "")
    if not code:
        code = full_state.get("代码", "")
    if not test_report:
        test_report = full_state.get("测试报告", "")

    # 写交付物（安全写入：残缺轮次不会覆盖之前更完整的产出）
    if delivery_dir:
        if requirements:
            _write_deliverable(os.path.join(delivery_dir, "需求分析.md"),
                               clean_markdown(str(requirements)), min_len=60)
        if code:
            from ..agents.extractor import extract_code
            _write_deliverable(os.path.join(delivery_dir, "代码.py"),
                               extract_code(str(code)), min_len=60)
        if test_report:
            _write_deliverable(os.path.join(delivery_dir, "测试报告.md"),
                               str(test_report), min_len=60)

    if delivery_dir and os.path.exists(delivery_dir):
        files = os.listdir(delivery_dir)
        safe_print(f"[工作流] 交付目录: {delivery_dir}")
        safe_print(f"[工作流] 交付物: {', '.join(files)}")

    # 记忆沉淀
    try:
        memory.auto_extract_and_save(user_request, full_state)
    except Exception as e:
        safe_print(f"[工作流] 记忆沉淀失败: {e}")

    # 清理短期记忆（按 task_id 隔离的 key）
    try:
        tid = state.get("task_id") or get_current_task_id()
        if tid:
            memory.delete_context(f"task:{tid}:state")
    except Exception as e:
        safe_print(f"[工作流] 清理短期记忆失败: {e}")

    cleanup_state()

    turns = state.get("turns", 0)
    test_passed = full_state.get("测试状态", "") == "通过"
    task_plan = full_state.get("任务规划", {})

    # 证据清单（T4）：最终总结的每条结论都能指回具体证据
    evidence_items: list[str] = []
    tester_rec = full_state.get("契约_测试员") or {}
    for e in tester_rec.get("evidence", []):
        evidence_items.append(
            f"测试运行: {e.get('type', '?')}={e.get('ref', '?')}"
            f"（{str(e.get('detail', ''))[:80]}）")
    if code:
        evidence_items.append(f"代码交付物: 代码.py（{len(str(code))} 字）")
    if requirements:
        evidence_items.append(f"需求文档: 需求分析.md（{len(str(requirements))} 字）")
    if test_report:
        evidence_items.append(f"测试报告: 测试报告.md（{len(str(test_report))} 字）")
    evidence_items.append(f"最终判定: 测试状态={full_state.get('测试状态', '未知')}，"
                          f"调度轮数={turns}")

    log_task_complete(state.get("task_name", ""), turns, True, 0)

    safe_print(f"\n[工作流] 任务完成 (共 {turns} 轮)")
    return {
        "status": "completed",
        "completed": True,
        "requirements": requirements,
        "code": code,
        "test_report": test_report,
        "test_passed": test_passed,
        "plan": task_plan,
        "evidence": evidence_items,
    }
