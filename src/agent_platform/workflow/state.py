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

    # ── 子Agent 产出（由 conclusion_node 汇总，供 _build_result 读取）──
    # 注意：LangGraph 只沿 schema 里声明过的通道传递状态，没声明的键会在
    # 出图时被静默丢弃（不报错，只是永远读不到）。这几个字段之前就漏了声明，
    # 导致 conclusion_node 辛苦汇总出来的产出拿不到，表现为交付物面板空白、
    # 测试状态缺失。
    requirements: str
    code: str
    test_report: str
    test_passed: bool
    plan: dict

    # ── 独立审查（T2）──
    review_verdict: str      # pass / reject
    review_count: int        # 已审查次数（打回满 2 次仍不过 → 如实收尾，不无限循环）
    review_feedback: str     # 最近一次打回意见（回填给主 Agent）

    # ── 证据清单（T4）：最终总结的每条结论都能指回具体证据 ──
    evidence: list
