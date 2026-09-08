"""数据库层测试 —— 验证幂等初始化、CRUD 和线程安全。"""
import os
import sys
import tempfile
import shutil
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_TMP = tempfile.mkdtemp(prefix="db_test_")
os.environ["AGENT_DATA_DIR"] = _TMP
os.environ["AGENT_DELIVERIES_DIR"] = os.path.join(_TMP, "del")

# 重新加载路径模块以确保使用临时目录
for mod in list(sys.modules.keys()):
    if mod.startswith("agent_platform"):
        del sys.modules[mod]

from agent_platform.storage import database as db  # noqa: E402


def setup():
    db.init_db()


def teardown():
    shutil.rmtree(_TMP, ignore_errors=True)


def test_init_idempotent():
    """init_db 多次调用不应报错，且表应存在。"""
    db.init_db()
    db.init_db()
    db.init_db()
    stats = db.db_get_stats()
    assert "total_tasks" in stats
    assert "total_memories" in stats
    print("✓ test_init_idempotent")


def test_user_crud():
    ok, msg = db.db_create_user("testuser", "fakehash123")
    assert ok, msg
    user = db.db_get_user("testuser")
    assert user is not None
    assert user["username"] == "testuser"

    ok2, _ = db.db_create_user("testuser", "anotherhash")
    assert not ok2, "重复用户名应失败"

    users = db.db_get_all_users()
    assert "testuser" in users
    print("✓ test_user_crud")


def test_task_crud():
    task_id = "test-task-001"
    db.db_create_task(task_id, "帮我写一个函数", "写函数", "testuser")
    task = db.db_get_task(task_id)
    assert task is not None
    assert task["status"] == "running"

    db.db_update_task_progress(task_id, {"phase": 2, "active_agent": "程序员"})
    task = db.db_get_task(task_id)
    assert task["progress"]["phase"] == 2

    db.db_complete_task(task_id, {"代码": "print('hello')"})
    task = db.db_get_task(task_id)
    assert task["status"] == "completed"
    assert task["result_json"]["代码"] == "print('hello')"
    print("✓ test_task_crud")


def test_task_fail():
    task_id = "test-task-fail"
    db.db_create_task(task_id, "会失败的任务", "失败任务", "testuser")
    db.db_fail_task(task_id, "something went wrong")
    task = db.db_get_task(task_id)
    assert task["status"] == "failed"
    assert "something went wrong" in task["error"]
    print("✓ test_task_fail")


def test_memory_crud():
    exp = {
        "id": "exp-001",
        "task_type": "coding",
        "tags": ["python", "test"],
        "task_description": "写测试",
        "one_line_summary": "测试经验",
        "successes": ["用了 pytest"],
        "lessons": ["不要硬编码路径"],
        "improvements": ["加 CI"],
        "quality_score": 8,
    }
    db.db_save_memory(exp)
    results = db.db_search_memories(keyword="测试")
    assert len(results) >= 1
    found = [r for r in results if r["id"] == "exp-001"]
    assert len(found) == 1
    assert found[0]["tags"] == ["python", "test"]
    print("✓ test_memory_crud")


def test_concurrent_task_creation():
    """多线程并发创建任务不应出错。"""
    errors = []

    def create_many(prefix):
        try:
            for i in range(10):
                tid = f"{prefix}-{i}"
                db.db_create_task(tid, f"task {i}", f"name{i}", "user")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=create_many, args=(f"t{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"并发错误: {errors}"
    stats = db.db_get_stats()
    assert stats["total_tasks"] >= 40
    print("✓ test_concurrent_task_creation")


if __name__ == "__main__":
    setup()
    test_init_idempotent()
    test_user_crud()
    test_task_crud()
    test_task_fail()
    test_memory_crud()
    test_concurrent_task_creation()
    teardown()
    print("\n=== ALL DATABASE TESTS PASSED ===")
