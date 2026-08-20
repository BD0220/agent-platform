"""dispatch_agent 工具 —— 主Agent 调度子Agent 的桥梁。"""
from ...utils.safe_print import safe_print


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
    try:
        from ...agents.sub_agents import dispatch_sub_agent
        return dispatch_sub_agent(agent_name, task_desc)
    except Exception as e:
        return f"dispatch_agent 执行失败: {str(e)}"


def _map_dispatch_agent(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("agent_name", ""), arguments.get("task", "")
