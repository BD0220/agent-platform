"""路径与运行时配置集中管理。

所有文件系统路径和运行时目录都从这里统一获取，
支持通过环境变量覆盖，避免在各模块中用 ``__file__`` 硬编码上溯多级。
"""
import os
from pathlib import Path

# 项目根目录（backend/agent_platform/utils/paths.py → 上溯 4 级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# 数据目录：运行时数据库、状态文件、向量库等
DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR", str(PROJECT_ROOT / ".data")))

# 交付物目录：每个任务产出的代码/文档
# 放在项目根目录（而不是 .data/ 里）是有意的：交付物是用户唯一需要
# 直接打开的东西，藏在隐藏目录里用户找不到（2026-10-18 用户原话"那生成
# 的代码哪去了"）。.data/ 只留会话记录、数据库、缓存这些内部文件。
DELIVERIES_DIR = Path(os.environ.get("AGENT_DELIVERIES_DIR", str(PROJECT_ROOT / "deliveries")))

# SQLite 数据库文件
DB_FILE = str(DATA_DIR / "platform.db")

# ChromaDB 持久化目录
CHROMA_DIR = str(DATA_DIR / "chroma_db")

# 日志目录
LOG_DIR = DATA_DIR / "logs"


def ensure_dirs() -> None:
    """确保运行时目录存在。应用启动时调用一次。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DELIVERIES_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def safe_path(filename: str, task_dir: str) -> str:
    """安全检查：确保文件路径在任务目录内，防止路径穿越攻击。

    返回规范化的绝对路径。
    """
    safe_name = os.path.basename(filename.strip())
    if not safe_name:
        raise ValueError("文件名不能为空")
    full_path = os.path.normpath(os.path.join(task_dir, safe_name))
    if not full_path.startswith(os.path.normpath(task_dir)):
        raise ValueError(f"不允许的路径: {filename}")
    return full_path
