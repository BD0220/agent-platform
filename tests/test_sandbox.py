"""代码执行沙箱测试 —— 验证资源限制和安全隔离。"""
import os
import sys
import tempfile
import time
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_TMP_DIR = tempfile.mkdtemp(prefix="sandbox_test_")
os.environ["AGENT_DATA_DIR"] = _TMP_DIR
os.environ["AGENT_DELIVERIES_DIR"] = os.path.join(_TMP_DIR, "deliveries")
os.makedirs(os.environ["AGENT_DELIVERIES_DIR"], exist_ok=True)

from agent_platform.storage.state import (  # noqa: E402
    set_current_task, save_state, get_current_task_dir, cleanup_state,
)
from agent_platform.storage.database import init_db  # noqa: E402
from agent_platform.tools.builtins.python_tools import (  # noqa: E402
    _tool_run_python_file, _build_sanitized_env,
)

init_db()
set_current_task("sandbox-test")
save_state({"task_name": "sandbox_test", "user_request": "sandbox test"})
TASK_DIR = get_current_task_dir()


def _write(name, code):
    with open(os.path.join(TASK_DIR, name), "w", encoding="utf-8") as f:
        f.write(code)
    return name


def test_normal_execution():
    f = _write("t_normal.py", "print('hello from sandbox')\nprint(2 ** 10)\n")
    r = _tool_run_python_file(f, "")
    assert "hello from sandbox" in r
    assert "1024" in r
    assert "退出码: 0" in r
    print("✓ test_normal_execution")


def test_env_sanitized():
    os.environ["DEEPSEEK_API_KEY"] = "sk-leak-test"
    env = _build_sanitized_env()
    assert "DEEPSEEK_API_KEY" not in env, "API Key 不应传递给子进程"
    assert "OPENAI_API_KEY" not in env
    del os.environ["DEEPSEEK_API_KEY"]
    print("✓ test_env_sanitized")


def test_subprocess_cant_read_secrets():
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-leak"
    f = _write("t_env.py", "import os\nprint('LEAK=' + os.environ.get('ANTHROPIC_API_KEY', 'SAFE'))\n")
    r = _tool_run_python_file(f, "")
    assert "SAFE" in r, f"子进程读到了密钥: {r}"
    assert "sk-ant-leak" not in r
    del os.environ["ANTHROPIC_API_KEY"]
    print("✓ test_subprocess_cant_read_secrets")


def test_error_exit_code():
    f = _write("t_error.py", "raise RuntimeError('intentional error')\n")
    r = _tool_run_python_file(f, "")
    assert "退出码: 1" in r
    assert "RuntimeError" in r
    print("✓ test_error_exit_code")


def test_file_write_within_task_dir():
    f = _write("t_write.py",
               "with open('output.txt', 'w') as fp:\n    fp.write('task output')\nprint('written')\n")
    r = _tool_run_python_file(f, "")
    assert "退出码: 0" in r
    assert os.path.exists(os.path.join(TASK_DIR, "output.txt"))
    print("✓ test_file_write_within_task_dir")


def test_timeout_kills_infinite_loop():
    """死循环必须被超时终止（30s）。此测试耗时较长，仅在显式启用时运行。"""
    if os.environ.get("RUN_SLOW_TESTS") != "1":
        print("⊘ test_timeout_kills_infinite_loop (skipped, set RUN_SLOW_TESTS=1)")
        return
    f = _write("t_loop.py", "while True:\n    pass\n")
    start = time.time()
    r = _tool_run_python_file(f, "")
    elapsed = time.time() - start
    assert "超时" in r or "退出码" in r
    assert elapsed < 40, f"超时未生效: {elapsed:.1f}s"
    print(f"✓ test_timeout_kills_infinite_loop ({elapsed:.1f}s)")


def teardown():
    cleanup_state()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    test_normal_execution()
    test_env_sanitized()
    test_subprocess_cant_read_secrets()
    test_error_exit_code()
    test_file_write_within_task_dir()
    test_timeout_kills_infinite_loop()
    teardown()
    print("\n=== ALL SANDBOX TESTS PASSED ===")
