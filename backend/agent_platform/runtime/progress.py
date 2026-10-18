"""长任务进度心跳。

问题（2026-10-18 用户反馈）："他老是卡住不动你没发现吗，观感非常差"。

根因不是卡死，而是**没有任何可见进度**：产品经理那次结构化调用要一次性
生成几千字 JSON，期间界面一粒字都不会动；程序员在工具循环里等模型时也
一样。用户看到的是一块冻住的屏幕，只能以为挂了。

这里提供一个极轻量的方案：后台线程每 ``interval`` 秒发一条 ``progress``
事件（kind 独立于 agent/delta，不会污染对话气泡），前端把它渲染成一行
灰色的状态条并按 agent 原地更新。任务结束时状态条自动消失。

只发"当前在干什么 + 已经等了多久 + 生成到多少字"，不发任何模型内容，
所以不会把界面刷成日志墙。
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from ..utils.safe_print import safe_print

DEFAULT_INTERVAL = 2.0


class Progress:
    """一条可更新的进度：标签 + 累计字数 + 已用时间。"""

    def __init__(self, agent: str, label: str, interval: float = DEFAULT_INTERVAL,
                 session_id: str | None = None):
        self.agent = agent
        self.label = label
        self.chars = 0
        self.started = time.time()
        self.interval = interval
        # 心跳线程是**新线程**，拿不到主线程的 ContextVar（会话 id）。
        # 所以必须在创建时就把它记下来，跟着线程一起用 —— 否则每条心跳
        # 都会被当成"没有会话"而静默丢弃（2026-10-19 实测：状态条一直空）。
        self.session_id = session_id
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dirty = True

    # ---- 调用方接口 ----
    def set(self, label: str) -> None:
        self.label = label
        self._dirty = True

    def add(self, count: int) -> None:
        """累加已生成字数（字数变了就刷新，让用户看到真的在动）。"""
        if count:
            self.chars += int(count)
            self._dirty = True

    def text(self) -> str:
        elapsed = int(time.time() - self.started)
        parts = [self.label, f"已 {elapsed}s"]
        if self.chars:
            parts.append(f"{self.chars} 字")
        return " · ".join(parts)

    # ---- 内部 ----
    def _emit(self) -> None:
        try:
            from . import bus
            sid = self.session_id or bus.current_session_id()
            bus.get_bus().publish(sid, bus.KIND_PROGRESS, _persist=False,
                                  agent=self.agent, content=self.text())
        except Exception:  # 进度只是观感，任何异常都不能影响主流程
            pass

    def _loop(self) -> None:
        # 先发一条，别让用户还要等一个 interval 才看到反应
        self._emit()
        while not self._stop.wait(self.interval):
            self._emit()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"progress-{self.agent}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


@contextmanager
def track(agent: str, label: str, interval: float = DEFAULT_INTERVAL):
    """``with track("产品经理", "分析需求并生成需求清单") as prog: ...``"""
    try:
        from . import bus
        sid = bus.current_session_id()
    except Exception:
        bus = None  # type: ignore[assignment]
        sid = ""
    prog = Progress(agent, label, interval, session_id=sid)
    prog.start()
    try:
        yield prog
    finally:
        prog.stop()
        if bus is not None:
            try:
                bus.get_bus().publish(sid, bus.KIND_PROGRESS, _persist=False,
                                      agent=agent, content="", done=True)
            except Exception:
                pass
        safe_print(f"[{agent}] {prog.text()}（结束）")
