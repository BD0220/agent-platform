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
from ..rag import advanced_search, format_memories_for_context as rag_format_memories, _build_bm25
from .. import memory
from ..logging.logger import log_task_start, log_task_complete

from .state import WorkflowState


def init_node(state: WorkflowState) -> dict:
    """初始化：数据库、BM25、任务名、交付目录、task_id 上下文。"""
    init_db()
    try:
        _build_bm25()
    except Exception as e:
        safe_print(f"[工作流] BM25 构建失败: {e}")

    user_request = state["user_request"]
    task_name = extract_task_name(user_request)

    # 优先使用队列传入的 task_id；没有则生成一个（CLI / 直接调用场景）
    task_id = state.get("task_id") or get_current_task_id()
    if task_id:
        set_current_task(task_id)

    delivery_dir = get_current_task_dir()
    save_state({"task_name": task_name, "user_request": user_request})
    log_task_start(task_name, user_request)

    safe_print(f"\n[工作流] INIT: 任务 '{task_name}' (task_id={task_id or 'cli'})")
    return {
        "task_name": task_name,
        "task_id": task_id or "",
        "delivery_dir": delivery_dir,
        "turns": 0,
        "max_turns": 20,
        "completed": False,
        "status": "running",
        "messages": [],
    }


def retrieve_node(state: WorkflowState) -> dict:
    """RAG 记忆检索，将历史经验注入消息列表。"""
    user_request = state["user_request"]
    messages = list(state.get("messages", []))

    try:
        relevant = advanced_search(user_request, top_k=3, use_rerank=True)
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

    try:
        result = llm.chat(messages=messages, tools=tool_schemas if tool_schemas else None)
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
    return {"messages": messages, "turns": turns + 1, "status": "thinking"}


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
            try:
                import json as _json
                arguments = _json.loads(tc["function"]["arguments"])
            except Exception:
                arguments = {}
            try:
                exec_result = execute_tool_structured(func_name, arguments)
            except Exception as e:
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
    """agent_node 之后的路由：有工具调用 → tool_node，否则 → conclusion。"""
    if _max_turns_reached(state):
        safe_print(f"[工作流] 达到最大轮数 {state.get('max_turns')}，强制收尾")
        return "conclusion"
    if _has_tool_calls(state):
        return "tools"
    return "conclusion"


def route_after_tools(state: WorkflowState) -> str:
    """tool_node 之后的路由：回到 agent_node 继续思考。"""
    if _max_turns_reached(state):
        return "conclusion"
    return "agent"


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

    # 写交付物
    if requirements and delivery_dir:
        with open(os.path.join(delivery_dir, "需求分析.md"), "w", encoding="utf-8") as f:
            f.write(clean_markdown(str(requirements)))

    if code and delivery_dir:
        code_path = os.path.join(delivery_dir, "代码.py")
        from ..agents.extractor import extract_code
        cleaned = extract_code(str(code))
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(cleaned)

    if test_report and delivery_dir:
        with open(os.path.join(delivery_dir, "测试报告.md"), "w", encoding="utf-8") as f:
            f.write(str(test_report))

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
    }
