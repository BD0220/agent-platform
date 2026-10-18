"""异步任务队列 — 线程池 + 任务生命周期管理，支持并发任务隔离。

每个任务在独立线程中执行，通过 ``contextvars.ContextVar`` 隔离 task_id，
确保状态读写、Redis key、交付目录均按任务隔离，不会串台。
"""
import contextvars
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable

from ..storage.database import (
    db_create_task, db_update_task_progress, db_complete_task,
    db_fail_task, db_get_task, db_list_tasks, db_get_stats,
)
from ..storage.state import (
    get_state_summary, extract_task_name, set_current_task, cleanup_state,
)
from ..utils.safe_print import safe_print


class TaskManager:
    """异步任务管理器。"""

    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, user_request: str, run_fn: Callable, username: str = "") -> str:
        task_id = str(uuid.uuid4())[:12]
        cancel_event = threading.Event()
        task_name = extract_task_name(user_request)
        db_create_task(task_id, user_request, task_name, username)

        with self._lock:
            self._cancel_flags[task_id] = cancel_event

        # 捕获当前 context，在工作线程中重放（复制 ContextVar 状态）
        ctx = contextvars.copy_context()
        future = self._executor.submit(
            ctx.run, self._run_wrapper, task_id, user_request, run_fn, cancel_event
        )
        with self._lock:
            self._futures[task_id] = future

        safe_print(f"[任务队列] 已提交任务 {task_id}: {user_request[:80]}")
        return task_id

    def _run_wrapper(self, task_id: str, user_request: str, run_fn: Callable,
                     cancel_event: threading.Event):
        # 在当前工作线程设置 task_id 到 ContextVar（线程独立）
        set_current_task(task_id)
        progress_stop = threading.Event()

        try:
            if cancel_event.is_set():
                db_fail_task(task_id, "任务已取消")
                return

            def monitor_progress():
                # 监控线程同样需要设置 task_id，才能读到正确的任务状态
                set_current_task(task_id)
                while not progress_stop.is_set():
                    try:
                        state = get_state_summary()
                        phase = 0
                        active_agent = ""
                        if not state:
                            phase, active_agent = 1, "产品经理"
                        elif "功能清单" in state and "代码" not in state:
                            phase, active_agent = 2, "程序员"
                        elif "代码" in state and "测试报告" not in state:
                            phase, active_agent = 3, "测试员"
                        elif "测试报告" in state:
                            if state.get("测试状态") != "未通过":
                                phase, active_agent = 5, ""
                            else:
                                phase, active_agent = 4, "程序员"

                        db_update_task_progress(task_id, {
                            "phase": phase, "active_agent": active_agent, "state": state,
                        })
                    except Exception as e:
                        safe_print(f"[任务队列] 进度监控异常: {e}")
                    progress_stop.wait(1.5)

            progress_thread = threading.Thread(target=monitor_progress, daemon=True)
            progress_thread.start()

            result = run_fn(user_request)

            progress_stop.set()
            progress_thread.join(timeout=3)
            db_complete_task(task_id, result)
            safe_print(f"[任务队列] 任务 {task_id} 完成")

        except Exception as e:
            progress_stop.set()
            db_fail_task(task_id, str(e))
            safe_print(f"[任务队列] 任务 {task_id} 失败: {e}")
        finally:
            cleanup_state()
            with self._lock:
                self._futures.pop(task_id, None)
                self._cancel_flags.pop(task_id, None)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            cancel_event = self._cancel_flags.get(task_id)
            if cancel_event:
                cancel_event.set()
                safe_print(f"[任务队列] 任务 {task_id} 已请求取消")
                return True
        return False

    def get_status(self, task_id: str) -> dict | None:
        return db_get_task(task_id)

    def list_tasks(self, username: str = "", status: str = "", limit: int = 20) -> list[dict]:
        return db_list_tasks(username=username, status=status, limit=limit)

    def get_stats(self) -> dict:
        return db_get_stats()

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            future = self._futures.get(task_id)
            return future is not None and future.running()

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for f in self._futures.values() if f.running())


_task_manager: TaskManager | None = None
_tm_lock = threading.Lock()


def get_task_manager() -> TaskManager:
    """获取全局任务管理器单例（线程安全懒加载）。"""
    global _task_manager
    if _task_manager is None:
        with _tm_lock:
            if _task_manager is None:
                _task_manager = TaskManager(max_workers=4)
                safe_print("[任务队列] 全局任务管理器已初始化")
    return _task_manager
