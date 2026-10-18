"""LangGraph 工作流状态 —— ReAct Agent 循环。"""
from typing import TypedDict


class WorkflowState(TypedDict, total=False):
    messages: list[dict]       # [{"role": "system/user/assistant/tool", "content": "...", ...}]
    user_request: str
    task_id: str               # 队列分配的任务 ID，用于并发隔离
    task_name: str
    delivery_dir: str
    turns: int
    max_turns: int
    status: str
    completed: bool
