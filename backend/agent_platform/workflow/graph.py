"""LangGraph ReAct Agent 工作流图。
agent ↔ tools 循环，主Agent 动态决策何时调度子Agent、何时修复、何时收尾。
"""
import uuid

from ..utils.safe_print import safe_print
from .nodes import (
    agent_node,
    conclusion_node,
    init_node,
    retrieve_node,
    review_node,
    route_after_agent,
    route_after_review,
    route_after_tools,
    tool_node,
)
from .state import WorkflowState


def build_workflow():
    """构建并编译 ReAct Agent 工作流。"""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        safe_print("[工作流] langgraph 未安装，使用线性回退模式")
        return None

    builder = StateGraph(WorkflowState)

    # 注册节点
    builder.add_node("init", init_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_node("review", review_node)
    builder.add_node("conclusion", conclusion_node)

    # 入口
    builder.set_entry_point("init")

    # init → retrieve → agent
    builder.add_edge("init", "retrieve")
    builder.add_edge("retrieve", "agent")

    # agent → tools (有工具调用) / review (准备收尾，先过独立审查) / conclusion (轮数耗尽)
    builder.add_conditional_edges("agent", route_after_agent, {
        "tools": "tools",
        "review": "review",
        "conclusion": "conclusion",
    })

    # tools → agent (继续思考) 或 conclusion (达到上限)
    builder.add_conditional_edges("tools", route_after_tools, {
        "agent": "agent",
        "conclusion": "conclusion",
    })

    # review → conclusion (通过/打回满次数) 或 agent (打回重做，带审查意见)
    builder.add_conditional_edges("review", route_after_review, {
        "conclusion": "conclusion",
        "agent": "agent",
    })

    # conclusion → END
    builder.add_edge("conclusion", END)

    compiled = builder.compile()
    safe_print("[工作流] LangGraph ReAct Agent 编译完成")
    return compiled


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_workflow()
    return _graph


def run_workflow(user_request: str, task_id: str = "") -> dict:
    """执行 ReAct Agent 工作流。

    Args:
        user_request: 用户需求文本。
        task_id: 任务队列分配的 ID。为空时自动生成（CLI 直接调用场景）。
    """
    g = _get_graph()
    if g is None:
        return _run_linear_fallback(user_request, task_id)

    if not task_id:
        from ..storage.state import get_current_task_id
        task_id = get_current_task_id() or str(uuid.uuid4())[:12]

    initial_state: WorkflowState = {"user_request": user_request, "task_id": task_id}
    safe_print("\n[工作流] ======== LangGraph ReAct Agent 开始 ========")
    final_state = g.invoke(initial_state)
    safe_print("[工作流] ======== LangGraph ReAct Agent 完成 ========")

    return _build_result(user_request, final_state)


def stream_workflow(user_request: str, task_id: str = ""):
    """流式执行。"""
    g = _get_graph()
    if g is None:
        result = _run_linear_fallback(user_request, task_id)
        yield {"event": "complete", "result": result}
        return

    if not task_id:
        from ..storage.state import get_current_task_id
        task_id = get_current_task_id() or str(uuid.uuid4())[:12]

    initial_state: WorkflowState = {"user_request": user_request, "task_id": task_id}
    for event in g.stream(initial_state):
        yield {"event": "progress", "state": event}


def _build_result(user_request: str, state: dict) -> dict:
    from ..storage.state import load_state
    full_state = load_state()

    # 为什么"测试状态"不能只从 full_state 取：
    # 走到这里时 conclusion_node 已经 cleanup_state() 把短期状态清掉了，
    # full_state 恒为空字典 —— 于是这个字段永远是空串，界面上一直显示
    # "测试状态：未知"，哪怕测试员明明跑完并给出了结论。
    # 而 test_passed 是 conclusion_node 在**清理之前**读出来的布尔值，
    # 它被放进了图状态（final_state）里，这才是可靠来源。
    tp = state.get("test_passed")
    test_state = "通过" if tp else ("未通过" if tp is False else "")

    return {
        "task": user_request,
        "task_name": state.get("task_name", ""),
        "主Agent调度轮数": state.get("turns", 0),
        "任务完成": state.get("completed", False),
        "功能清单": full_state.get("功能清单", state.get("requirements", "")),
        "代码": full_state.get("代码", state.get("code", "")),
        "测试报告": full_state.get("测试报告", state.get("test_report", "")),
        "测试状态": test_state,
        "任务规划": full_state.get("任务规划", state.get("plan", {})),
        "证据清单": state.get("evidence", []),
        "审查次数": state.get("review_count", 0),
        "交付目录": state.get("delivery_dir", ""),
        # 模型调用失败时要能透传到收尾文案，否则界面只会说"测试未通过"
        "错误": state.get("error", ""),
    }


def _run_linear_fallback(user_request: str, task_id: str = "") -> dict:
    """langgraph 未安装时的回退 —— 使用原有 master_agent_loop。"""
    safe_print("[工作流] 使用线性回退模式 (master_agent_loop)")

    import os

    from .. import memory
    from ..agents import extract_code, master_agent_loop
    from ..logging.logger import log_task_complete, log_task_start
    from ..rag import advanced_search
    from ..rag import format_memories_for_context as rag_format_memories
    from ..storage.database import init_db
    from ..storage.state import (
        cleanup_state,
        extract_task_name,
        get_current_task_dir,
        load_state,
        save_state,
        set_current_task,
    )
    from ..utils.text import clean_markdown

    if not task_id:
        task_id = str(uuid.uuid4())[:12]
    set_current_task(task_id)

    init_db()

    task_name = extract_task_name(user_request)
    save_state({"task_name": task_name, "user_request": user_request})
    log_task_start(task_name, user_request)

    memory_context = ""
    try:
        relevant = advanced_search(user_request, top_k=3)
        if relevant:
            memory_context = rag_format_memories(relevant)
    except Exception as e:
        safe_print(f"[工作流] RAG 检索失败: {e}")
        try:
            relevant = memory.search_memory(user_request)
            if relevant:
                memory_context = memory.format_memories_for_context(relevant)
        except Exception as e2:
            safe_print(f"[工作流] 关键词检索也失败: {e2}")

    safe_print("\n" + "=" * 60)
    safe_print("    主Agent 开始调度 (线性回退)")
    safe_print("=" * 60)

    master_result = master_agent_loop(user_request, memory_context=memory_context)
    turns = master_result["turns"]
    completed = master_result["任务完成"]
    log_task_complete(task_name, turns, completed, 0)
    task_plan = master_result.get("任务规划", {})

    state = load_state()
    delivery_dir = get_current_task_dir()

    req = state.get("功能清单", "")
    if req:
        with open(os.path.join(delivery_dir, "需求分析.md"), "w", encoding="utf-8") as f:
            f.write(clean_markdown(req))

    code_path = os.path.join(delivery_dir, "代码.py")
    if not os.path.exists(code_path):
        raw_code = state.get("代码", "")
        if raw_code:
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(extract_code(raw_code))

    report = state.get("测试报告", "")
    if report:
        with open(os.path.join(delivery_dir, "测试报告.md"), "w", encoding="utf-8") as f:
            f.write(report)

    try:
        memory.auto_extract_and_save(user_request, state)
    except Exception as e:
        safe_print(f"[工作流] 记忆沉淀失败: {e}")

    cleanup_state()

    return {
        "task": user_request,
        "task_name": task_name,
        "主Agent调度轮数": turns,
        "任务完成": completed,
        "功能清单": state.get("功能清单", ""),
        "代码": state.get("代码", ""),
        "测试报告": state.get("测试报告", ""),
        "测试状态": state.get("测试状态", ""),
        "任务规划": task_plan,
        "交付目录": delivery_dir,
    }
