"""Python 代码执行工具 —— 带资源限制的安全沙箱。

安全措施：
- 仅在任务交付目录内执行（cwd 锁定，禁止路径穿越）。
- 超时强制终止（默认 30 秒，防止死循环）。
- POSIX 下通过 resource.setrlimit 限制 CPU/内存/文件大小/进程数。
- 清除子进程环境变量中的 API Key 等敏感信息。
- 设置 AGENT_DOCKER_SANDBOX=1 可启用 Docker 一次性容器强隔离（需安装 Docker）。
"""
import os
import shutil
import subprocess
import sys

from ...storage.state import get_current_task_dir
from ...utils.paths import safe_path
from ...utils.safe_print import safe_print

DEFAULT_TIMEOUT = 30
MAX_MEMORY_MB = 512
MAX_FILE_SIZE_MB = 10
MAX_SUBPROCESSES = 32


def _run_in_docker(filepath: str, task_dir: str, timeout: int) -> subprocess.CompletedProcess | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    cmd = [
        docker, "run", "--rm", "-i",
        "--network", "none",
        "--read-only",
        "--memory", f"{MAX_MEMORY_MB}m",
        "--cpus", "1",
        "--pids-limit", str(MAX_SUBPROCESSES),
        "--tmpfs", "/tmp:size=16m",
        "-v", f"{task_dir}:/workspace",
        "-w", "/workspace",
        "python:3.11-slim",
        "python", "-B", os.path.basename(filepath),
    ]
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=task_dir,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        safe_print(f"[沙箱] Docker 执行失败，降级到本地沙箱: {e}")
        return None


def _build_sanitized_env() -> dict:
    sensitive_prefixes = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "ANTHROPIC",
                          "DEEPSEEK", "OPENAI", "REDIS")
    env = {}
    for k, v in os.environ.items():
        if any(k.upper().startswith(p) for p in sensitive_prefixes):
            continue
        env[k] = v
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _build_posix_preexec_fn():
    """返回一个 preexec_fn，在 fork 后、exec 前应用资源限制。

    比 -c 包装更可靠：不经过 shell、不依赖 runpy、不会有模块查找问题。
    """
    def _set_limits():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
            resource.setrlimit(
                resource.RLIMIT_AS,
                (MAX_MEMORY_MB * 1024 * 1024, MAX_MEMORY_MB * 1024 * 1024),
            )
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (MAX_FILE_SIZE_MB * 1024 * 1024, MAX_FILE_SIZE_MB * 1024 * 1024),
            )
            resource.setrlimit(resource.RLIMIT_NPROC, (MAX_SUBPROCESSES, MAX_SUBPROCESSES))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except Exception:
            pass
    return _set_limits


def _tool_run_python_file(params: str, content: str) -> str:
    task_dir = get_current_task_dir()
    if not params.strip():
        return "错误: 请指定要运行的 Python 文件名"

    filepath = safe_path(params.strip(), task_dir)
    if not os.path.exists(filepath):
        return f"错误: 文件不存在: {filepath}"

    safe_print(f"[工具系统] 在沙箱中运行: python {filepath}")
    timeout = DEFAULT_TIMEOUT

    if os.environ.get("AGENT_DOCKER_SANDBOX", "0") == "1":
        proc = _run_in_docker(filepath, task_dir, timeout)
        if proc is not None:
            return _format_result(proc)

    env = _build_sanitized_env()
    cmd = [sys.executable, "-B", filepath]

    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "cwd": task_dir,
        "env": env,
    }
    if sys.platform != "win32":
        kwargs["preexec_fn"] = _build_posix_preexec_fn()

    try:
        proc = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        return (f"运行超时（{timeout} 秒）: {filepath}\n"
                "程序可能在死循环或等待输入。已终止。")
    except OSError as e:
        return f"沙箱启动失败: {e}"

    return _format_result(proc, filepath)


def _format_result(proc: subprocess.CompletedProcess, filepath: str = "") -> str:
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    parts = [f"退出码: {proc.returncode}"]
    if stdout:
        parts.append(f"--- 标准输出 ---\n{stdout}")
    if stderr:
        parts.append(f"--- 标准错误 ---\n{stderr}")
    if not stdout and not stderr:
        parts.append("(无输出)")
    return "\n".join(parts)


def _map_run_python(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("filename", ""), ""
