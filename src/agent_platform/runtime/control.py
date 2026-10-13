"""控制面 —— 交接审批闸门 + 协作式取消。

这是整个改动里最关键的一块：让"智能体之间的任务交接"必须经过人。

## 闸门插在哪
    ``tools/builtins/dispatch.py`` 是主Agent 调用 ``dispatch_agent`` 工具的唯一入口，
    也就是全项目**唯一**的任务交接点。闸门放在这里，就覆盖了所有交接场景，
    不需要在 4 个 Agent 里各写一遍。

## 为什么阻塞而不是回调
    主Agent 的调度循环是同步的（LangGraph 节点 / master_agent_loop），
    它调用工具时天然期待一个同步返回值。所以这里用 ``threading.Event`` 把
    Agent 线程挂起，HTTP 侧收到用户的确认后再把它唤醒 —— 这是最小侵入的
    做法，不需要把整个编排改成异步。

## 为什么用协作式取消
    强杀线程（``_thread.interrupt_main`` / ctypes）会破坏状态文件、留下半个
    交付物。这里改用**检查点**方案：在每次交接等闸门时、以及每个子Agent
    开始前，检查一次停止标志，命中就抛 ``TaskStopped`` 顺着调用栈退出，
    由会话执行器统一收尾。代价是响应有一个 agent 粒度（秒级）的延迟，
    换来的是状态一致。
"""
import os
import threading
import time
import uuid

from .bus import KIND_HANDOFF, KIND_HANDOFF_DONE, emit

# 审批超时（秒）：超过则自动放行，避免演示时忘记点确认导致任务永久挂起。
# 设为 0 表示永不超时。
APPROVAL_TIMEOUT = int(os.environ.get("AGENT_APPROVAL_TIMEOUT", "1800"))


class TaskStopped(Exception):
    """用户主动终止任务 —— 沿着调用栈向上传播，由会话执行器捕获。"""


class _Approval:
    __slots__ = ("id", "session_id", "from_agent", "to_agent", "task",
                 "summary", "event", "decision", "task_override", "created_at", "auto")

    def __init__(self, session_id, from_agent, to_agent, task, summary):
        self.id = f"ap_{uuid.uuid4().hex[:10]}"
        self.session_id = session_id
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.task = task
        self.summary = summary
        self.event = threading.Event()
        self.decision = ""
        self.task_override = None
        self.created_at = time.time()
        self.auto = False

    def public(self) -> dict:
        return {
            "approval_id": self.id,
            "session_id": self.session_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "task": self.task,
            "summary": self.summary,
            "created_at": self.created_at,
        }


class ControlPlane:
    """审批闸门与停止标志的容器。所有状态按 session_id 组织。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._approvals: dict[str, _Approval] = {}        # approval_id -> 记录
        self._by_session: dict[str, list[str]] = {}       # session_id -> approval_id 列表
        self._stopped: set[str] = set()                   # 已被请求终止的会话

    # ── 审批闸门 ────────────────────────────────────────────
    @staticmethod
    def _set_session_status(session_id: str, status: str) -> None:
        """同步会话状态。control 只关心"等人确认"这一件事，
        落盘失败不能影响闸门本身，所以整体吞掉异常。"""
        try:
            from . import session_store
            session_store.update(session_id, status=status)
        except Exception:
            pass

    def request(self, session_id: str, from_agent: str, to_agent: str,
                task: str, summary: str = "", timeout: int | None = None) -> dict:
        """发起一次交接确认，**阻塞**直到用户处置或超时。

        Returns:
            {"decision": "approve"|"reject"|"stop", "task": <最终要交给下游的任务描述>,
             "auto": bool, "approval_id": str}

        ``decision`` 语义：
            approve —— 按原任务（或用户改写后的任务）继续交接
            reject  —— 打回重做，调用方应把上游 agent 重新跑一遍
            stop    —— 终止整个任务
        """
        # 进入闸门之前先查一次停止标志：避免用户在上一环节已点终止、这里还傻等
        if self.is_stopped(session_id):
            raise TaskStopped("任务已被用户终止")

        ap = _Approval(session_id, from_agent, to_agent, task, summary)
        with self._lock:
            self._approvals[ap.id] = ap
            self._by_session.setdefault(session_id, []).append(ap.id)

        # 会话状态切到"待确认"，侧边栏才能把这条会话标出来
        self._set_session_status(session_id, "waiting")
        emit(KIND_HANDOFF, **ap.public())

        wait_timeout = APPROVAL_TIMEOUT if timeout is None else timeout
        signaled = ap.event.wait(wait_timeout) if wait_timeout else ap.event.wait()

        with self._lock:
            self._approvals.pop(ap.id, None)
            ids = self._by_session.get(session_id, [])
            if ap.id in ids:
                ids.remove(ap.id)
        self._set_session_status(session_id, "running")

        if not signaled:
            # 超时：自动放行，但要让用户知道这是自动的
            ap.decision = "approve"
            ap.auto = True
            emit(KIND_HANDOFF_DONE, approval_id=ap.id, decision="approve",
                 task=ap.task, auto=True,
                 note=f"等待确认超过 {wait_timeout} 秒，已自动放行")

        if ap.decision == "stop":
            emit(KIND_HANDOFF_DONE, approval_id=ap.id, decision="stop", task=ap.task)

        return {
            "decision": ap.decision or "approve",
            "task": ap.task_override if ap.task_override else ap.task,
            "auto": ap.auto,
            "approval_id": ap.id,
        }

    def resolve(self, approval_id: str, decision: str, task: str | None = None) -> bool:
        """用户处置一次交接确认。返回是否命中待确认项。"""
        if decision not in ("approve", "reject", "stop"):
            return False
        with self._lock:
            ap = self._approvals.get(approval_id)
            if not ap:
                return False
            ap.decision = decision
            if task is not None and task.strip():
                ap.task_override = task.strip()
            if decision == "reject":
                emit(KIND_HANDOFF_DONE, approval_id=ap.id, decision="reject",
                     task=ap.task, note="用户打回，要求重做")
            ap.event.set()
        return True

    def pending(self, session_id: str) -> dict | None:
        """查看某会话当前待确认的交接项（没有则 None）。"""
        with self._lock:
            for aid in self._by_session.get(session_id, []):
                ap = self._approvals.get(aid)
                if ap and not ap.event.is_set():
                    return ap.public()
        return None

    def resolve_session(self, session_id: str, decision: str,
                        task: str | None = None) -> str:
        """对某会话当前待确认项直接处置（前端只知道 session_id 时用）。"""
        ap = self.pending(session_id)
        if not ap:
            return ""
        ok = self.resolve(ap["approval_id"], decision, task)
        return ap["approval_id"] if ok else ""

    # ── 协作式取消 ──────────────────────────────────────────
    def request_stop(self, session_id: str) -> bool:
        """请求终止：置停止标志，并唤醒所有正在等待的审批闸门。"""
        with self._lock:
            self._stopped.add(session_id)
            for aid in list(self._by_session.get(session_id, [])):
                ap = self._approvals.get(aid)
                if ap and not ap.event.is_set():
                    ap.decision = "stop"
                    ap.event.set()
        emit(KIND_HANDOFF_DONE, approval_id="", decision="stop", task="",
             note="已收到终止指令")
        return True

    def is_stopped(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._stopped

    def check_stop(self, session_id: str | None = None) -> None:
        """检查点：命中停止标志则抛 TaskStopped。"""
        from .bus import current_session_id
        sid = session_id or current_session_id()
        if sid and self.is_stopped(sid):
            raise TaskStopped("任务已被用户终止")

    def clear(self, session_id: str) -> None:
        """清除某会话的停止标志（新任务开始时调用）。"""
        with self._lock:
            self._stopped.discard(session_id)


_control: ControlPlane | None = None
_control_lock = threading.Lock()


def get_control() -> ControlPlane:
    """获取全局控制面单例。"""
    global _control
    if _control is None:
        with _control_lock:
            if _control is None:
                _control = ControlPlane()
    return _control
