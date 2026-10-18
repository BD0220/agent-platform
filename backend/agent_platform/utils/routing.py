"""任务路由：决定一个任务要不要先过产品经理。

背景（2026-10-18 用户反馈"生成也慢，测试也慢"）：
产品经理走的是**结构化 JSON 生成**，一次要吐 3000~6000 字，实测 60~90 秒，
而"做个计算器""写个回文判断函数"这类任务根本不需要它 —— 用户需求本身就
是功能清单。把它跳过，单任务就能省下最大的一笔固定开销。

判定刻意保守：只有"短且不含文档/系统类意图词"才跳过。拿不准就走完整流程，
宁可慢一点，也不要因为省一步把需求理解错。

环境变量：
    AGENT_SKIP_PM=1   强制跳过产品经理
    AGENT_SKIP_PM=0   强制走完整流程
    AGENT_SIMPLE_TASK_CHARS  视为"简单任务"的最大字数（默认 40）
"""
from __future__ import annotations

import os

# 出现这些词说明用户要的是"方案/文档/系统"级别的东西，必须要产品经理拆解
_DOC_INTENT = (
    "需求", "文档", "PRD", "prd", "方案", "设计", "架构", "系统", "平台",
    "流程", "模块", "报告", "规划", "评估", "调研", "多个", "支持多",
)


def _max_simple_chars() -> int:
    try:
        return int(os.environ.get("AGENT_SIMPLE_TASK_CHARS", "40"))
    except ValueError:
        return 40


def needs_product_manager(user_request: str) -> bool:
    """True = 需要产品经理先拆需求；False = 简单任务可直接开工。"""
    flag = str(os.environ.get("AGENT_SKIP_PM", "auto")).strip().lower()
    if flag in ("1", "true", "yes"):
        return False
    if flag in ("0", "false", "no"):
        return True

    text = (user_request or "").strip()
    if not text:
        return True
    if len(text) > _max_simple_chars():
        return True
    if any(k in text for k in _DOC_INTENT):
        return True
    return False


def skip_reason(user_request: str) -> str:
    """给界面用的一句解释（用户要知道为什么这次没产品经理）。"""
    chars = len((user_request or "").strip())
    return f"简单任务（{chars} 字，无文档/系统类要求）：跳过产品经理，直接开工"
