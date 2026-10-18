"""dispatch_agent 工具 —— 主Agent 调度子Agent 的桥梁。

这个文件是全项目**唯一**的任务交接点：主Agent 想让某个子Agent 干活，
只有可能要经过 ``dispatch_agent``。因此"交接前必须人工确认"这条规则
只需要在这里实现一次，就覆盖了所有交接场景。

执行顺序（关键）：
    校验参数 → 停止标志检查 → ★交接闸门（阻塞等用户点确认）→ 真正调度子Agent

闸门放在这里还有个好处：主Agent 传进来的 ``task`` 描述就是"下面要交给谁什么任务"
的原文，直接拿去做确认卡片，不需要额外让 LLM 再输出一遍结构化的交接说明。
"""
from ...utils.safe_print import safe_print

# 上游 agent -> 它产出的 state 字段名，用于在确认卡片里展示"已完成的工作内容"
_UPSTREAM_OUTPUT_FIELD = {
    "产品经理": "功能清单",
    "程序员": "代码",
    "测试员": "测试报告",
}


def _has_any_output() -> bool:
    """本次任务是否已经产生过子Agent 的产出。"""
    try:
        from ...storage.state import load_state
        st = load_state()
    except Exception:
        return False
    return any(st.get(k) for k in ("功能清单", "代码", "测试报告"))


def _last_dispatched_agent() -> str:
    """上一次被主Agent 分派过的子Agent（用于判断"这次交接的上游是谁"）。

    不能只信 dispatch_log：那份记录挂在 ``agent_master`` 这个**没有会话隔离**
    的 key 上，上一个会话的残留会让"本次第一次交接的上游"错标成上个会话最后
    干活的 Agent（实测出现过"测试员 → 产品经理"这种荒唐的交接链）。
    所以先用本次任务的产出物校验一遍：一个产出都还没有，就说明这是开场，
    上游是主Agent 自己。
    """
    if not _has_any_output():
        return ""
    try:
        from ...memory.short_term import load_context
        log = load_context("agent_master").get("dispatch_log", [])
        if log:
            return log[-1].get("agent", "") or ""
    except Exception:
        pass
    return ""


def _last_dispatched_task() -> str:
    if not _has_any_output():
        return ""
    try:
        from ...memory.short_term import load_context
        log = load_context("agent_master").get("dispatch_log", [])
        if log:
            return log[-1].get("task", "") or ""
    except Exception:
        pass
    return ""


def _work_summary(agent_name: str) -> str:
    """取上游 agent 的产出摘要，作为确认卡片里"他的工作内容"。"""
    field = _UPSTREAM_OUTPUT_FIELD.get(agent_name)
    if not field:
        return ""
    try:
        from ...storage.state import load_state
        text = str(load_state().get(field) or "")
    except Exception:
        return ""
    if not text:
        return ""
    return text if len(text) <= 800 else text[:800] + "\n\n...（内容较长，完整产出见交付物）"


def _tool_dispatch_agent(params: str, content: str) -> str:
    agent_name = params.strip()
    task_desc = content.strip()
    if not agent_name:
        return "错误: 请指定子Agent名称"
    if not task_desc:
        return "错误: 请提供任务描述内容"
    from ...agents.definitions import VALID_AGENT_NAMES
    if agent_name not in VALID_AGENT_NAMES:
        return f"错误: 未知的子Agent '{agent_name}'，可选: {', '.join(VALID_AGENT_NAMES)}"
    safe_print(f"[dispatch_agent] 主Agent 调度 {agent_name}，任务长度 {len(task_desc)} 字")

    task_desc = _pass_approval_gate(agent_name, task_desc)
    if task_desc is None:
        # 用户打回了，上游已重跑，本次交接作废
        return "【已按用户要求打回重做】上游 Agent 的产出已重新生成，请审查后再决定下一步。"

    try:
        from ...agents.sub_agents import dispatch_sub_agent
        return dispatch_sub_agent(agent_name, task_desc)
    except Exception as e:
        # 终止信号必须继续向上传播，不能被当成"工具执行失败"吞掉
        from ...runtime.control import TaskStopped
        if isinstance(e, TaskStopped):
            raise
        return f"dispatch_agent 执行失败: {str(e)}"


def _pass_approval_gate(agent_name: str, task_desc: str):
    """两级人工闸门：第一级"执行计划确认"（仅开场一次），第二级"交接确认"。

    两级闸门的分工：
        计划确认 —— 用户在任务开头看一次全貌（主Agent 打算怎么干），点头后开工；
                    此时第一次 dispatch 不再重复确认（计划里已经包含它）。
        交接确认 —— 开场之后的每次交接照旧走原有闸门，看的是"上游实际产出了什么"。

    Returns:
        最终要交给下游的任务描述；用户打回时返回 None 表示本次交接作废。

    Raises:
        TaskStopped: 用户点了终止。
    """
    from ...runtime import bus
    from ...runtime.control import TaskStopped, get_control

    sid = bus.current_session_id()
    if not sid:
        # 命令行 / 测试直跑场景没有会话上下文，闸门自动旁路
        return task_desc

    ctl = get_control()
    ctl.check_stop(sid)

    if not _has_any_output():
        # 开场：先过"计划确认"这一级，通过后本次 dispatch 直接放行
        plan_raw = ""
        try:
            from ...storage.state import load_state
            plan = load_state().get("任务规划") or {}
            plan_raw = plan.get("raw", "") if isinstance(plan, dict) else str(plan)
        except Exception:
            plan_raw = ""
        if plan_raw.strip():
            safe_print("[计划闸门] 等待用户确认执行计划…")
            result = ctl.request(
                session_id=sid,
                from_agent="主Agent",
                to_agent="执行团队",
                task=plan_raw[:800],
                summary="执行计划确认（确认后按此计划开工，可在意见里直接改计划）",
            )
            decision = result["decision"]
            if decision == "stop":
                raise TaskStopped("用户终止了任务")
            if decision == "reject":
                bus.emit(bus.KIND_AGENT, agent="主Agent", phase="note",
                         content="用户打回了执行计划，主Agent 将重新规划。")
                return None
            # approve —— 计划放行；第一次交接免确认（计划已覆盖）
            return task_desc

    previous = _last_dispatched_agent() or "主Agent"
    summary = _work_summary(previous)
    safe_print(f"[交接闸门] {previous} → {agent_name}，等待用户确认…")

    result = ctl.request(
        session_id=sid,
        from_agent=previous,
        to_agent=agent_name,
        task=task_desc,
        summary=summary,
    )
    decision = result["decision"]

    if decision == "stop":
        raise TaskStopped("用户终止了任务")

    if decision == "reject":
        reason = (result.get("task") or "").strip()
        bus.emit(bus.KIND_AGENT, agent="主Agent", phase="note",
                 content=f"用户打回了 {previous} 的产出，正在安排重做。")
        if previous and previous in _UPSTREAM_OUTPUT_FIELD:
            redo = (
                f"【重做要求】用户对你上一版产出不满意，请据此重新输出。\n"
                f"用户意见：{reason or '（未填写具体意见，请自行检查是否有遗漏或错误）'}\n\n"
                f"【你上一版接到的任务】{_last_dispatched_task() or task_desc}"
            )
            try:
                from ...agents.sub_agents import dispatch_sub_agent
                dispatch_sub_agent(previous, redo)
            except TaskStopped:
                raise
            except Exception as e:
                safe_print(f"[交接闸门] 重做失败: {e}")
            return None
        return None

    # approve —— 用户放行（可能顺手改写了任务描述）
    final_task = result.get("task") or task_desc
    if final_task != task_desc:
        bus.emit(bus.KIND_AGENT, agent="主Agent", phase="note",
                 content=f"用户修改了交给 {agent_name} 的任务描述，按新描述执行。")
    return final_task


def _map_dispatch_agent(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("agent_name", ""), arguments.get("task", "")
