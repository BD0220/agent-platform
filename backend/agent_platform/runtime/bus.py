"""会话事件总线 —— 把 Agent 的协作过程变成一条可流式消费的"群聊消息流"。

为什么需要它？
    原来的实现是前端每秒轮询 ``/status`` 读一份被截断到 200 字的 state 摘要，
    前端拿不到"谁说了什么"，只能自己拼 UI。群聊形态要求事件是**有顺序、
    有类型、不丢**的，所以这里做一条 append-only 的事件流：
    Agent 线程只管 ``publish``，HTTP 层的 SSE 只管按游标 ``read``。

线程模型：
    多个 Agent 工作线程（TaskManager / _stream_executor 的线程）并发 publish，
    若干个 SSE 读线程并发 read。用一把 ``Condition`` 统一保护，
    publish 后 ``notify_all``，read 在无新事件时 ``wait(timeout)`` 让出 CPU。

游标语义：
    read 返回 ``(events, next_cursor)``，调用方把 ``next_cursor`` 回传即可增量拉取。
    游标是"已消费事件条数"，不是时间戳，因此不会因为时钟问题漏事件。
"""
import threading
import time
import uuid

from ..utils.safe_print import safe_print

# 事件类型（kind）—— 前端按 kind 决定渲染成哪种气泡
KIND_USER = "user"              # 用户发言
KIND_SYSTEM = "system"          # 系统提示（任务开始 / 已终止 / 出错）
KIND_PLAN = "plan"              # 主Agent 的执行计划
KIND_AGENT = "agent"            # 子Agent / 主Agent 发言
KIND_TOOL = "tool"              # 一次工具调用（start / ok / error），带精简预览
KIND_HANDOFF = "handoff"        # 交接请求 —— 需要用户确认
KIND_HANDOFF_DONE = "handoff_done"  # 交接已被处置
KIND_DELIVERABLE = "deliverable"    # 交付物（代码 / 报告）
KIND_DONE = "done"              # 整个任务完成
KIND_ERROR = "error"            # 任务失败
KIND_PROGRESS = "progress"      # 长任务心跳（不落盘、不进对话气泡，只更新状态条）

# 读到这些事件说明本轮执行已经收尾，SSE 可以关流了
TERMINAL_KINDS = {KIND_DONE, KIND_ERROR}


class SessionBus:
    """按 session_id 隔离的进程内事件总线。"""

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.RLock())
        self._events: dict[str, list[dict]] = {}
        self._running: dict[str, bool] = {}

    # ── 写入侧 ──────────────────────────────────────────────
    def publish(self, sid: str, kind: str, _persist: bool = True, **payload) -> dict | None:
        """追加一条事件并唤醒所有等待者。无 sid 时静默忽略。

        第一个参数刻意叫 ``sid`` 而不是 ``session_id``：事件负载里天然会带
        一个 ``session_id`` 字段（前端要靠它知道消息属于哪个会话），
        如果形参同名，``publish(sid, kind, **payload)`` 会直接抛
        TypeError: got multiple values for argument。命名避让比到处 pop 更稳。

        ``_persist=False`` 用于 LLM 流式输出的增量片段：只推给正在看的
        浏览器，不写进会话日志 —— 否则一次任务要往 JSONL 里塞几百条
        碎片，历史回放时靠最终的 done 消息就够了。
        """
        if not sid:
            return None

        event = {"id": f"e_{uuid.uuid4().hex[:10]}", "ts": time.time(), "kind": kind}
        event.update(payload)

        with self._cond:
            bucket = self._ensure_loaded(sid)
            event["seq"] = len(bucket)
            bucket.append(event)
            self._cond.notify_all()

        if _persist:
            # 落盘放在锁外：磁盘 IO 不能阻塞其他 SSE 读线程
            self._persist(sid, event)
        return event

    def _ensure_loaded(self, sid: str) -> list[dict]:
        """内存队列懒加载 —— cursor 语义正确的前提。

        页面刷新后前端拿 ``cursor`` 来续读 SSE，而这个下标必须与**内存队列**
        对齐。进程刚起来时内存是空的，如果游标却按磁盘事件数给，两边就错位：
        要么重复推一遍已经回放的片段，要么整段跳过。

        所以第一次碰某个会话时，先把落盘的事件灌进内存，让"内存在下标 k"
        和"磁盘有 k 条"重新对齐。调用方必须已持有 ``self._cond``。
        """
        bucket = self._events.get(sid)
        if bucket is None:
            try:
                from . import session_store
                bucket = list(session_store.read_events(sid))
            except Exception as e:
                safe_print(f"[事件总线] 历史事件加载失败: {e}")
                bucket = []
            self._events[sid] = bucket
        return bucket

    def cursor(self, session_id: str) -> int:
        """当前会话的 SSE 续读游标（= 内存里已有的事件条数）。"""
        if not session_id:
            return 0
        with self._cond:
            return len(self._ensure_loaded(session_id))

    @staticmethod
    def _persist(session_id: str, event: dict) -> None:
        """把事件写入会话日志。

        内存队列只服务于"正在看的那一个页面"，进程重启就没了；
        历史会话要能重新点开并完整回放，所以每条事件都必须落盘。
        落盘失败只在日志里报一声，绝不让它影响正在进行的任务。
        """
        try:
            from . import session_store
            session_store.append(session_id, event)
        except Exception as e:
            safe_print(f"[事件总线] 事件落盘失败: {e}")

    def mark_running(self, session_id: str, running: bool) -> None:
        """标记本会话是否有任务正在跑（供前端显示"思考中"状态）。"""
        if not session_id:
            return
        with self._cond:
            self._running[session_id] = running
            self._cond.notify_all()

    def is_running(self, session_id: str) -> bool:
        with self._cond:
            return self._running.get(session_id, False)

    # ── 读取侧 ──────────────────────────────────────────────
    def read(self, session_id: str, cursor: int = 0, timeout: float = 15.0):
        """增量读取事件。

        Returns:
            (events, next_cursor, running)
            events 为空表示本次等待超时，调用方应发个心跳保持连接。
        """
        if not session_id:
            return [], cursor, False

        deadline = time.time() + timeout
        with self._cond:
            while True:
                bucket = self._ensure_loaded(session_id)
                if len(bucket) > cursor:
                    return bucket[cursor:], len(bucket), self._running.get(session_id, False)
                remaining = deadline - time.time()
                if remaining <= 0:
                    return [], cursor, self._running.get(session_id, False)
                self._cond.wait(remaining)

    def snapshot(self, session_id: str, cursor: int = 0) -> list[dict]:
        """非阻塞读取当前已有事件（用于刷新页面后回放）。"""
        with self._cond:
            return list(self._ensure_loaded(session_id)[cursor:])

    def clear(self, session_id: str) -> None:
        """清空某会话的事件（重跑或删除会话时调用）。

        注意要留一个空列表而不是 pop 掉 key —— 否则下一次读会重新从磁盘
        把刚被删掉的历史灌回来。
        """
        with self._cond:
            self._events[session_id] = []
            self._running.pop(session_id, None)


_bus: SessionBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> SessionBus:
    """获取全局事件总线单例。"""
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = SessionBus()
                safe_print("[事件总线] 已初始化")
    return _bus


def current_session_id() -> str:
    """取当前执行上下文的会话 ID。

    直接复用 storage.state 的 ContextVar，因此每个工作线程拿到的都是自己的
    task_id，不需要在调用链上层层传参。CLI 直跑（无任务上下文）时返回空串，
    所有 publish 会静默忽略，不影响命令行使用。
    """
    from ..storage.state import get_current_task_id
    return get_current_task_id() or ""


def emit(kind: str, **payload) -> dict | None:
    """便捷函数：向当前会话发一条事件。"""
    return get_bus().publish(current_session_id(), kind, **payload)


def emit_progress(agent: str, text: str) -> dict | None:
    """进度心跳：只推给浏览器，不落盘（否则历史回放会看到一堆过期状态）。"""
    return get_bus().publish(
        current_session_id(), KIND_PROGRESS, _persist=False,
        agent=agent, content=text)


def emit_progress_done(agent: str) -> dict | None:
    """告诉前端这条状态条可以撤掉了。"""
    return get_bus().publish(
        current_session_id(), KIND_PROGRESS, _persist=False,
        agent=agent, content="", done=True)


def session_context(session_id: str):
    """把当前上下文切到某个会话（测试 / 同线程模拟用），见 state.task_context。"""
    from ..storage.state import task_context
    return task_context(session_id)
