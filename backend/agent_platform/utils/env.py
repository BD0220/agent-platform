"""把项目根目录的 .env 读进 os.environ。

以前只有 ``run.py`` 负责读 .env，于是 ``python -m uvicorn``、容器里的
``uvicorn``、pytest 这些入口都没有 API Key —— 模型调用直接抛异常，任务
一秒内就以"测试未通过"收尾，真正原因被掩盖。现在包一被 import 就加载。
"""

from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def load_env(path: str | Path | None = None, override: bool = False) -> None:
    """读 .env；默认不覆盖进程里已有的环境变量（容器/CI 优先）。

    设 ``AGENT_DOTENV_DISABLE=1`` 可完全跳过：测试要把 MySQL/数据目录等
    环境变量清空来跑临时库，如果这时 .env 又被读回来，测试就会连到真实
    数据库上（2026-10-18 实测：test_database 把行写进了生产 MySQL）。
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    if str(os.environ.get("AGENT_DOTENV_DISABLE", "")).strip() == "1":
        return
    env_path = Path(path) if path else Path(__file__).resolve().parents[3] / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value
