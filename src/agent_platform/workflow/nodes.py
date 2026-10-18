"""LangGraph ReAct Agent 节点函数。
agent_node → (tool_calls?) → tool_node → agent_node → ... → conclusion
主Agent 动态决策调度子Agent，而非固定流程。
"""
import os
import re

from ..utils.safe_print import safe_print
from ..utils.text import clean_markdown
from ..storage.state import (
    save_state, load_state, extract_task_name, get_current_task_dir,
    set_current_task, cleanup_state, get_current_task_id,
)
from ..storage.database import init_db
from ..llm import get_llm
from ..tools import get_tool_definitions, get_tool_schemas, execute_tool_structured
from ..tools.parser import parse_and_execute_tool_calls
from ..agents.definitions import SYSTEM_PROMPTS
from ..rag import advanced_search, format_memories_for_context as rag_format_memories
from .. import memory
from ..logging.logger import log_task_start, log_task_complete

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


def agent_node(state: WorkflowState) -> dict:
    """主Agent 节点：调用 LLM 决定下一步行动。"""
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
        return {"messages": messages, "turns": turns + 1, "status": "error"}

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


def tool_node(state: WorkflowState) -> dict:
    """工具执行节点：执行主Agent 发起的工具调用，返回结果。"""
    messages = list(state.get("messages", []))

    if not messages:
        return {"messages": messages}

    last_msg = messages[-1]
    if last_msg.get("role") != "assistant":
        return {"messages": messages}

    # 路径1: 标准 Function Calling tool_calls
    tool_calls = last_msg.get("tool_calls", [])
    if tool_calls:
        safe_print(f"\n[工具执行] 执行 {len(tool_calls)} 个 Function Calling 调用")
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            # 工具动静上屏：长工具（读大文件/跑脚本）期间界面不再是死寂一片
            try:
                from ..runtime import bus
                bus.emit(bus.KIND_SYSTEM, content=f"🔧 主Agent 调用工具 {func_name}")
            except Exception:
                pass
            try:
                import json as _json
                arguments = _json.loads(tc["function"]["arguments"])
            except Exception:
                arguments = {}
            try:
                exec_result = execute_tool_structured(func_name, arguments)
            except Exception as e:
                # 终止信号必须向上穿透。之前这里是一把抓的 except Exception，
                # 把 TaskStopped 也当成"工具执行失败"变成一段字符串喂回模型，
                # 于是用户点了终止，Agent 却当作一次普通失败继续往下跑，
                # 最后还正常收尾成"任务完成"—— 终止按钮等于失效。
                from ..runtime.control import TaskStopped
                if isinstance(e, TaskStopped):
                    raise
                exec_result = f"工具执行失败: {e}"
            preview = exec_result[:150] + ("..." if len(exec_result) > 150 else "")
            safe_print(f"[{func_name}] → {preview}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": func_name,
                "content": exec_result,
            })
        return {"messages": messages}

    # 路径2: 文本格式 [TOOL_CALL: ...]
    if last_msg.get("_text_tool_calls"):
        content = last_msg.get("content", "")
        safe_print(f"\n[工具执行] 解析文本工具调用...")
        tool_results = parse_and_execute_tool_calls(content)
        if tool_results:
            results_text = "\n\n".join(
                f"## {tr['tool']} 执行结果\n{tr['result']}" for tr in tool_results
            )
            messages.append({"role": "user", "content": results_text})
        return {"messages": messages}

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
    轮数耗尽 → 直接 conclusion。"""
    if _max_turns_reached(state):
        safe_print(f"[工作流] 达到最大轮数 {state.get('max_turns')}，强制收尾")
        return "conclusion"
    if _has_tool_calls(state):
        return "tools"
    return "review"


def route_after_tools(state: WorkflowState) -> str:
    """tool_node 之后的路由：回到 agent_node 继续思考。"""
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
            with open(path, "r", encoding="utf-8", errors="replace") as f:
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
