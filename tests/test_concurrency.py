"""并发状态隔离测试 —— 验证多任务并发时 ContextVar + Redis key 不串台。

这是 P0 回归测试：旧实现使用全局 _current_task_id 变量，
4 个线程并发时会互相覆盖状态。新实现基于 ContextVar，每个线程独立。
"""
import concurrent.futures
import sys
import os
import shutil
import tempfile
import threading
import time

# 测试前设置临时数据目录（必须在 import agent_platform 之前）
_TMP_DIR = tempfile.mkdtemp(prefix="agent_test_")
os.environ["AGENT_DATA_DIR"] = _TMP_DIR
os.environ["AGENT_DELIVERIES_DIR"] = os.path.join(_TMP_DIR, "deliveries")
os.makedirs(os.environ["AGENT_DELIVERIES_DIR"], exist_ok=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_platform.storage.state import (  # noqa: E402
    set_current_task, save_state, load_state, cleanup_state,
    get_current_task_id, get_current_task_dir,
)
from agent_platform.storage.database import init_db  # noqa: E402

init_db()


def teardown_module():
    """测试结束后清理临时目录。"""
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


def test_context_var_isolation_between_threads():
    """两个线程各自设置不同 task_id，load_state 不应串台。"""
    results = {}
    errors = []
    barrier = threading.Barrier(2, timeout=10)

    def worker(task_id: str, value: str):
        try:
            set_current_task(task_id)
            assert get_current_task_id() == task_id

            # 等待两个线程都设置好，再同时写
            barrier.wait()
            save_state({"task_id": task_id, "payload": value})
            time.sleep(0.05)

            state = load_state()
            results[task_id] = state
            cleanup_state()
        except Exception as e:
            errors.append(f"{task_id}: {e}")

    t1 = threading.Thread(target=worker, args=("task-aaa", "value_a"))
    t2 = threading.Thread(target=worker, args=("task-bbb", "value_b"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert not errors, f"线程异常: {errors}"
    assert results["task-aaa"].get("payload") == "value_a", \
        f"task-aaa 串台了: {results['task-aaa']}"
    assert results["task-bbb"].get("payload") == "value_b", \
        f"task-bbb 串台了: {results['task-bbb']}"


def test_concurrent_tasks_thread_pool():
    """模拟 TaskManager 的 4 线程并发场景，验证状态完全隔离。"""
    num_tasks = 4
    barrier = threading.Barrier(num_tasks, timeout=10)
    errors = []

    def run_concurrent_task(idx: int):
        try:
            task_id = f"concurrent-{idx}"
            set_current_task(task_id)
            barrier.wait()

            for round_num in range(3):
                save_state({
                    "task_id": task_id,
                    "round": round_num,
                    "data": f"task{idx}_round{round_num}",
                })
                time.sleep(0.02)
                state = load_state()
                assert state["task_id"] == task_id, \
                    f"任务 {task_id} 第 {round_num} 轮串台: got {state.get('task_id')}"
                assert state["data"] == f"task{idx}_round{round_num}"

            cleanup_state()
        except Exception as e:
            errors.append(f"task-{idx}: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_tasks) as pool:
        futures = [pool.submit(run_concurrent_task, i) for i in range(num_tasks)]
        for f in concurrent.futures.as_completed(futures, timeout=15):
            f.result()

    assert not errors, f"并发任务异常: {errors}"


def test_save_and_load_cycle():
    """单任务的保存-读取-清理周期。"""
    set_current_task("cycle-test")
    save_state({"hello": "world", "count": 42})
    state = load_state()
    assert state["hello"] == "world"
    assert state["count"] == 42
    cleanup_state()
    assert load_state() == {}


def test_task_dir_isolation():
    """不同 task_id 的交付目录应不同（目录名含 task_id 后缀）。"""
    set_current_task("aaa-dir-test-1")
    save_state({"task_name": "test_task", "user_request": "test"})
    dir1 = get_current_task_dir()

    cleanup_state()

    set_current_task("bbb-dir-test-2")
    save_state({"task_name": "test_task", "user_request": "test"})
    dir2 = get_current_task_dir()

    assert dir1 != dir2, "不同 task_id 的交付目录不应相同"
    cleanup_state()


if __name__ == "__main__":
    test_context_var_isolation_between_threads()
    print("✓ test_context_var_isolation_between_threads")
    test_concurrent_tasks_thread_pool()
    print("✓ test_concurrent_tasks_thread_pool")
    test_save_and_load_cycle()
    print("✓ test_save_and_load_cycle")
    test_task_dir_isolation()
    print("✓ test_task_dir_isolation")
    print("\n=== ALL CONCURRENCY TESTS PASSED ===")
