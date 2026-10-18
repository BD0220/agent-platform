"""闸门事件回归测试 —— 锁住"确认条反复弹"这个 bug。

现场（2026-10-19）：用户点了「✓ 确认，继续」，卡片却一直留在时间线上，
底部确认条随每一条新事件反复出现。根因有两个，都必须锁住：

  1. ``ControlPlane.resolve`` 只在 reject 时广播 ``handoff_done``，
     approve 什么都不发 —— 前端永远不知道该卡片已经处置完；
  2. 广播用的是 ``bus.emit``，靠 ContextVar 取会话 id，而"用户点确认"
     发生在 HTTP 处理线程里，那里没有任务上下文，事件被发到空会话丢弃。

所以这里用「另一个线程等闸门 + 主线程 resolve」模拟真实的 HTTP 线程行为。
每个用例用独立 session_id，避免终止标志互相污染。
"""
import os
import sys
import tempfile
import threading
import time
import uuid

_TMP = tempfile.mkdtemp(prefix="handoff_test_")
os.environ.setdefault("AGENT_DATA_DIR", _TMP)
os.environ.setdefault("AGENT_DELIVERIES_DIR", os.path.join(_TMP, "deliveries"))
os.makedirs(os.environ["AGENT_DELIVERIES_DIR"], exist_ok=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from agent_platform.runtime.bus import get_bus  # noqa: E402
from agent_platform.runtime.control import get_control  # noqa: E402


def _new_sid() -> str:
    return f"s_handoff_{uuid.uuid4().hex[:8]}"


def _wait_for(sid, kind, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = get_bus().snapshot(sid)
        hits = [e for e in events if e.get("kind") == kind]
        if hits:
            return hits
        time.sleep(0.02)
    return []


def _open_gate(sid):
    """在后台线程里打开一次闸门，模拟 Agent 线程被挂起。"""
    result = {}

    def worker():
        result.update(get_control().request(
            sid, "主Agent", "产品经理", task="写个函数", timeout=10))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    handoffs = _wait_for(sid, "handoff")
    assert handoffs, "闸门打开后没有 handoff 事件"
    return thread, result, handoffs[0]


def test_approve_broadcasts_handoff_done():
    sid = _new_sid()
    thread, result, handoff = _open_gate(sid)

    approval_id = handoff["approval_id"]
    assert get_control().resolve(approval_id, "approve") is True
    thread.join(5)

    assert not thread.is_alive(), "确认后闸门没有被放行"
    assert result["decision"] == "approve"

    done = _wait_for(sid, "handoff_done", timeout=2)
    assert done, "approve 之后没有广播 handoff_done（确认条会一直弹）"
    assert done[-1]["approval_id"] == approval_id
    assert done[-1]["decision"] == "approve"


def test_reject_broadcasts_handoff_done():
    sid = _new_sid()
    thread, result, handoff = _open_gate(sid)

    assert get_control().resolve(handoff["approval_id"], "reject", "再改改") is True
    thread.join(5)

    done = _wait_for(sid, "handoff_done", timeout=2)
    assert done and done[-1]["decision"] == "reject"
    assert result["decision"] == "reject"


def test_stop_broadcasts_handoff_done():
    sid = _new_sid()
    thread, _result, _handoff = _open_gate(sid)

    get_control().request_stop(sid)
    thread.join(5)

    done = _wait_for(sid, "handoff_done", timeout=2)
    assert done, "终止后没有广播 handoff_done"
    assert any(d.get("decision") == "stop" for d in done)


def test_handoff_event_carries_session_id():
    """ap.public() 里带 session_id，不能被 _publish 的形参吃掉。"""
    sid = _new_sid()
    thread, _result, handoff = _open_gate(sid)
    assert handoff.get("session_id") == sid
    get_control().resolve(handoff["approval_id"], "approve")
    thread.join(5)


def test_bus_cursor_matches_memory_after_replay():
    """刷新页面时前端拿 cursor 续读 SSE，游标必须与内存队列对齐。"""
    sid = _new_sid()
    bus = get_bus()
    bus.clear(sid)
    bus.publish(sid, "system", content="第一条")
    bus.publish(sid, "system", content="第二条")

    cursor = bus.cursor(sid)
    assert cursor == 2

    # 从磁盘重新灌一遍（模拟进程重启）：游标要重新对齐，不能倒流
    bus._events.pop(sid, None)
    reloaded = bus.cursor(sid)
    assert reloaded >= 2
    events, next_cursor, _running = bus.read(sid, reloaded, timeout=0.1)
    assert events == []
    assert next_cursor == reloaded
