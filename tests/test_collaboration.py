"""协作层自测 —— 事件总线 / 交接闸门 / 工作空间 / 会话存储。

这几块是"人在环路"能力的底座，出问题会表现成很难查的现象
（比如点了确认没反应、终止按钮无效），所以单独测一遍。

运行：
    .venv\\Scripts\\python.exe tests\\test_collaboration.py
"""
import os
import sys
import tempfile
import threading

# 必须在导入项目模块之前改掉数据目录，否则会往真实 data/ 里写测试垃圾
_TMP = tempfile.mkdtemp(prefix="agent_collab_test_")
os.environ["AGENT_DATA_DIR"] = _TMP
os.environ["AGENT_DELIVERIES_DIR"] = os.path.join(_TMP, "deliveries")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from agent_platform.runtime import bus, session_store, workspace  # noqa: E402
from agent_platform.runtime.bus import KIND_AGENT, KIND_HANDOFF  # noqa: E402
from agent_platform.runtime.control import TaskStopped, get_control  # noqa: E402

PASSED = []
FAILED = []


def check(name: str, condition: bool, extra: str = ""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {extra}" if extra and not condition else ""))


# ---------------------------------------------------------------------------
def test_bus_delivers_in_order():
    b = bus.get_bus()
    sid = "s_bus_001"
    b.clear(sid)
    for i in range(5):
        b.publish(sid, KIND_AGENT, agent="产品经理", content=f"msg-{i}")

    events, cursor, _ = b.read(sid, 0, timeout=0.2)
    check("总线按序返回全部事件", len(events) == 5 and cursor == 5, f"got {len(events)}")
    check("事件带自增 seq", [e["seq"] for e in events] == [0, 1, 2, 3, 4])

    # 增量读取：从游标 5 继续，应立刻超时返回空
    more, cursor2, _ = b.read(sid, cursor, timeout=0.2)
    check("游标之后无新事件时返回空", more == [] and cursor2 == cursor)


def test_bus_wakes_up_on_publish():
    """阻塞的读者应该被后到的写入者唤醒，而不是等满超时。"""
    b = bus.get_bus()
    sid = "s_bus_002"
    b.clear(sid)
    box = {}

    def reader():
        start = __import__("time").time()
        events, _, _ = b.read(sid, 0, timeout=5.0)
        box["elapsed"] = __import__("time").time() - start
        box["events"] = events

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    __import__("time").sleep(0.3)
    b.publish(sid, KIND_AGENT, agent="程序员", content="hello")
    t.join(timeout=3)

    check("publish 能立刻唤醒阻塞中的订阅者",
          box.get("elapsed", 99) < 2.0 and len(box.get("events", [])) == 1,
          f"elapsed={box.get('elapsed')}")


# ---------------------------------------------------------------------------
def test_gate_blocks_until_approved():
    """闸门必须真的把调用方挂住，直到另一侧点了放行。"""
    ctl = get_control()
    sid = "s_gate_001"
    ctl.clear(sid)
    box = {}

    def caller():
        box["result"] = ctl.request(sid, "主Agent", "产品经理",
                                    task="分析登录需求", summary="上一轮产出")

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    __import__("time").sleep(0.4)

    check("闸门挂起时调用方仍在阻塞", t.is_alive())
    pending = ctl.pending(sid)
    check("待确认项可被查询到", bool(pending) and pending["to_agent"] == "产品经理")

    ctl.resolve_session(sid, "approve")
    t.join(timeout=3)
    check("放行后闸门返回", box.get("result", {}).get("decision") == "approve",
          str(box.get("result")))
    check("放行后任务描述不变", box.get("result", {}).get("task") == "分析登录需求")


def test_gate_can_rewrite_task():
    ctl = get_control()
    sid = "s_gate_002"
    ctl.clear(sid)
    box = {}

    def caller():
        box["result"] = ctl.request(sid, "主Agent", "程序员", task="原任务")

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    __import__("time").sleep(0.3)
    ctl.resolve_session(sid, "approve", task="改成：只实现登录，不要注册")
    t.join(timeout=3)

    check("用户可以改写交给下游的任务",
          box.get("result", {}).get("task") == "改成：只实现登录，不要注册",
          str(box.get("result")))


def test_gate_reject():
    ctl = get_control()
    sid = "s_gate_003"
    ctl.clear(sid)
    box = {}

    def caller():
        box["result"] = ctl.request(sid, "主Agent", "测试员", task="测试一下")

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    __import__("time").sleep(0.3)
    ctl.resolve_session(sid, "reject", task="用例太少，补充边界场景")
    t.join(timeout=3)

    res = box.get("result", {})
    check("打回时用户意见随返回值传出",
          res.get("decision") == "reject" and "边界场景" in res.get("task", ""),
          str(res))


def test_stop_wakes_gate_and_raises():
    """终止必须能穿透阻塞中的闸门，并表现为 TaskStopped。"""
    ctl = get_control()
    sid = "s_gate_004"
    ctl.clear(sid)
    box = {}

    def caller():
        try:
            ctl.request(sid, "主Agent", "产品经理", task="x")
        except TaskStopped:
            box["stopped"] = True

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    __import__("time").sleep(0.3)
    ctl.request_stop(sid)
    t.join(timeout=3)

    # 闸门本身返回 decision=stop（由调用方决定是否抛异常），
    # 但 check_stop 必须在后续任意检查点能命中
    check("终止能唤醒阻塞中的闸门", not t.is_alive())
    hit = False
    try:
        ctl.check_stop(sid)
    except TaskStopped:
        hit = True
    check("终止后检查点抛 TaskStopped", hit)

    ctl.clear(sid)
    ok = True
    try:
        ctl.check_stop(sid)
    except TaskStopped:
        ok = False
    check("clear 之后检查点恢复正常", ok)


# ---------------------------------------------------------------------------
def test_session_store_roundtrip():
    meta = session_store.create(
        title="回文函数", user_request="写一个判断回文的函数",
        workspace_id="ws_x", workspace_name="演示", workspace_path=_TMP,
    )
    sid = meta["id"]
    session_store.append(sid, {"kind": "user", "content": "你好"})
    session_store.append(sid, {"kind": "agent", "content": "收到"})

    events = session_store.read_events(sid)
    check("会话事件能落盘并读回", len(events) == 2 and events[1]["content"] == "收到")
    check("会话元信息能读回", session_store.load(sid)["title"] == "回文函数")
    check("工作空间路径可解析", session_store.get_workspace_path(sid) == _TMP)
    check("会话出现在列表中",
          any(s["id"] == sid for s in session_store.list_sessions()))
    check("删除会话生效", session_store.delete(sid) and session_store.load(sid) is None)


def test_path_traversal_rejected():
    check("非法 session_id 被拒绝", session_store.load("../../etc/passwd") is None)


def test_workspace_registry():
    items = workspace.list_workspaces()
    check("默认工作空间始终存在", any(w["builtin"] for w in items))

    target = os.path.join(_TMP, "ws_demo")
    ok, msg, item = workspace.add_workspace("演示空间", target)
    check("新增工作空间成功", ok, msg)

    ok2, msg2, _ = workspace.add_workspace("重复", target)
    check("同一路径不能重复注册", not ok2, msg2)

    path, name = workspace.resolve_workspace_dir(item["id"])
    check("能按 id 解析出目录", path == os.path.normpath(target), path)

    ok3, _ = workspace.remove_workspace(item["id"])
    check("移除工作空间成功", ok3)


def test_emit_without_session_is_silent():
    """命令行直跑没有会话上下文，发布事件必须是安全的空操作。"""
    from agent_platform.runtime.bus import current_session_id, emit
    check("无会话上下文时 session_id 为空", current_session_id() == "")
    try:
        emit(KIND_AGENT, agent="x", content="y")
        ok = True
    except Exception as e:
        ok = False
        print(e)
    check("无会话上下文时 emit 不报错", ok)


# ---------------------------------------------------------------------------
def main():
    print("=" * 62)
    print("  协作层自测")
    print("=" * 62)
    for fn in [
        test_bus_delivers_in_order,
        test_bus_wakes_up_on_publish,
        test_gate_blocks_until_approved,
        test_gate_can_rewrite_task,
        test_gate_reject,
        test_stop_wakes_gate_and_raises,
        test_session_store_roundtrip,
        test_path_traversal_rejected,
        test_workspace_registry,
        test_emit_without_session_is_silent,
    ]:
        print(f"\n-- {fn.__name__}")
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILED.append(f"{fn.__name__} 抛异常: {e}")

    print("\n" + "=" * 62)
    print(f"  通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("  失败项:")
        for f in FAILED:
            print(f"    - {f}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
