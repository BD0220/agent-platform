"""共享状态管理器 —— 双层存储：Redis 优先，JSON 文件降级。按 task_id 隔离，并发安全。

设计要点：
- 使用 ``contextvars.ContextVar`` 持有当前任务 ID，每个工作线程/任务独立，
  彻底消除多线程并发时全局变量串台的问题。
- Redis key 按 ``task_id`` 命名空间隔离（``session:task:<task_id>:state``），
  不同任务互不干扰。
- 所有路径从 ``utils.paths`` 集中获取，支持环境变量覆盖。
"""
import contextvars
import json
import os
import re
import threading
from datetime import datetime

from ..utils.paths import DATA_DIR, DELIVERIES_DIR
from ..utils.safe_print import safe_print

# 全局默认状态文件（仅用于无任务上下文的降级场景）
STATE_FILE = DATA_DIR / "project_state.json"

# 并发隔离：ContextVar 持有当前任务 ID
_current_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_task_id", default=None
)

# 文件锁仅保护文件系统写入的原子性
_file_lock = threading.Lock()


def set_current_task(task_id: str) -> None:
    """设置当前线程/任务的 task_id。每个工作线程在任务开始时调用。"""
    _current_task_id.set(task_id)


def get_current_task_id() -> str | None:
    """获取当前上下文的 task_id。"""
    return _current_task_id.get()


def _session_key() -> str:
    """Redis 命名空间 key：按 task_id 隔离，无任务上下文时使用默认 key。"""
    tid = _current_task_id.get()
    return f"task:{tid}:state" if tid else "project_state"


def _state_file():
    """每个任务独立的 JSON 状态文件，避免并发覆盖。"""
    tid = _current_task_id.get()
    if tid:
        return DATA_DIR / f"state_{tid}.json"
    return STATE_FILE


# ---------------------------------------------------------------------------
# 状态读写
# ---------------------------------------------------------------------------
def save_state(data: dict) -> None:
    """保存状态：优先写入 Redis，降级写入 JSON 文件。按 task_id 隔离。"""
    safe_print(f"[状态管理器] 更新字段: {', '.join(data.keys())}")
    existing = load_state()
    existing.update(data)

    session_key = _session_key()

    # 优先写 Redis
    try:
        from ..memory.short_term import save_context
        save_context(session_key, existing)
        return
    except Exception as e:
        safe_print(f"[状态管理器] Redis 写入失败，降级 JSON: {e}")

    # 降级写 JSON 文件（加锁 + 原子替换）
    sf = _state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock:
        tmp = sf.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            tmp.replace(sf)
        except (UnicodeEncodeError, UnicodeDecodeError):
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=True, indent=2)
            tmp.replace(sf)


def load_state() -> dict:
    """读取当前任务状态：优先 Redis，降级 JSON 文件。"""
    session_key = _session_key()

    try:
        from ..memory.short_term import load_context
        ctx = load_context(session_key)
        if ctx:
            return {k: v for k, v in ctx.items() if not k.startswith("_")}
    except Exception as e:
        safe_print(f"[状态管理器] Redis 读取失败，回退 JSON: {e}")

    sf = _state_file()
    if not sf.exists():
        # 回退读旧格式全局状态（迁移期兼容）
        if STATE_FILE.exists():
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    try:
        with open(sf, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def cleanup_state() -> None:
    """清理当前任务的状态（Redis + JSON 文件）。"""
    session_key = _session_key()
    try:
        from ..memory.short_term import delete_context as del_ctx
        del_ctx(session_key)
    except Exception as e:
        safe_print(f"[状态管理器] Redis 删除失败: {e}")

    sf = _state_file()
    if sf.exists():
        sf.unlink(missing_ok=True)

    _current_task_id.set(None)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def get_state_summary() -> dict:
    """返回状态摘要（每个字段值截断到 200 字）。"""
    state = load_state()
    summary = {}
    for key, value in state.items():
        text = value if isinstance(value, str) else str(value)
        summary[key] = text[:200] + ("..." if len(text) > 200 else "")
    return summary


def extract_task_name(user_request: str) -> str:
    """从用户输入中提取简短任务名，用作文件夹名。"""
    name = user_request.strip()
    prefixes = ["帮我开发一个", "帮我做一个", "帮我写一个", "请帮我", "做一个", "写一个", "帮我", "请"]
    while True:
        matched = False
        for prefix in prefixes:
            if name.startswith(prefix):
                name = name[len(prefix):]
                matched = True
                break
        if not matched:
            break
    name = re.sub(r'[\\/:*?"<>|\n\r]', "", name)
    name = re.sub(r'[，。！？、；：""''（）【】《》]', "_", name)
    name = re.sub(r'\s+', "_", name)
    name = name.strip("_")
    if len(name) > 30:
        name = name[:30]
    return name.strip() or f"任务_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def get_current_task_dir() -> str:
    """获取当前任务的交付目录路径，确保目录存在后返回。

    目录名包含 task_id 短哈希后缀，确保并发任务不会写入同一目录。
    """
    state = load_state()
    task_name = state.get("task_name", "")
    if not task_name:
        user_request = state.get("user_request", "")
        task_name = extract_task_name(user_request) if user_request else f"任务_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 追加 task_id 短后缀避免并发同名任务目录冲突
    tid = _current_task_id.get()
    if tid:
        dir_name = f"{task_name}_{tid[:8]}"
    else:
        dir_name = task_name

    task_dir = os.path.join(DELIVERIES_DIR, dir_name)
    os.makedirs(task_dir, exist_ok=True)
    return task_dir
