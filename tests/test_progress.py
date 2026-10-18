"""长任务进度心跳（progress 事件）测试。

背景（2026-10-18 用户反馈）："他老是卡住不动你没发现吗，观感非常差"。
产品经理那次结构化调用要一次性生成几千字 JSON，期间界面零反馈。
修复分两层：后端 progress 心跳事件 + 前端状态条。这里锁定后端契约。
"""
import sys

sys.path.insert(0, "backend")

import time  # noqa: E402

from agent_platform.runtime import bus  # noqa: E402
from agent_platform.runtime.progress import Progress, track  # noqa: E402

SID = "s_test_progress"


def _reset():
    b = bus.get_bus()
    with b._cond:  # noqa: SLF001 —— 测试直接清内存队列，避免依赖其他用例产生的事件
        b._events.pop(SID, None)


def test_progress_emits_heartbeat_and_done():
    """track() 期间会发内容型心跳，退出时发一条 done=True。"""
    _reset()
    with bus.session_context(SID):
        with track("产品经理", "分析需求", interval=0.05) as prog:
            prog.add(120)
            time.sleep(0.12)

    events, _cursor, _running = bus.get_bus().read(SID, 0, timeout=0.01)
    kinds = [e for e in events if e["kind"] == bus.KIND_PROGRESS]
    assert kinds, "应至少发出一条 progress 事件"
    assert any("分析需求" in (e.get("content") or "") for e in kinds), "心跳应带当前在做什么"
    assert any(e.get("content", "").endswith("字") for e in kinds), "心跳应带已生成字数"
    assert kinds[-1].get("done") is True, "退出 track 必须发 done=True 让状态条消失"


def test_progress_events_are_not_persisted():
    """心跳不能落盘：否则历史回放会看到一堆过期状态行。"""
    _reset()
    with bus.session_context(SID):
        with track("程序员", "写代码", interval=0.05):
            time.sleep(0.08)

    from agent_platform.runtime import session_store
    stored = session_store.read_events(SID)
    assert not [e for e in stored if e.get("kind") == bus.KIND_PROGRESS], \
        "progress 事件不应写进会话日志"


def test_progress_text_reports_elapsed_and_chars():
    prog = Progress("测试员", "跑测试", interval=60)
    prog.add(42)
    text = prog.text()
    assert "跑测试" in text
    assert "已 0s" in text or "已 1s" in text
    assert "42 字" in text
