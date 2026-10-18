"""SQLite 数据库层 — 管理 tasks, users, memories 三张核心表。

- 使用线程局部连接（``threading.local``），每个工作线程持有独立连接。
- 数据库路径从 ``utils.paths.DB_FILE`` 获取，支持环境变量 ``AGENT_DATA_DIR`` 覆盖。
- ``init_db()`` 幂等，可安全重复调用；不再在模块 import 时自动执行。
"""
import json
import sqlite3
import threading
from datetime import datetime

from ..utils.paths import DB_FILE, ensure_dirs
from ..utils.safe_print import safe_print

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        ensure_dirs()
        _local.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """初始化数据库表。幂等，可安全重复调用。"""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                username TEXT,
                user_request TEXT NOT NULL,
                task_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                progress TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                task_type TEXT,
                tags TEXT,
                task_description TEXT,
                one_line_summary TEXT,
                successes TEXT,
                lessons TEXT,
                improvements TEXT,
                quality_score INTEGER DEFAULT 5,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_username ON tasks(username);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_memories_task_type ON memories(task_type);
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
        """)
        conn.commit()
        _initialized = True
        safe_print("[数据库] 初始化完成")


def db_create_user(username: str, password_hash: str) -> tuple[bool, str]:
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        conn.commit()
        return True, f"用户 '{username}' 创建成功"
    except sqlite3.IntegrityError:
        return False, f"用户名 '{username}' 已存在"


def db_get_user(username: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def db_get_all_users() -> list[str]:
    conn = _get_conn()
    return [r["username"] for r in conn.execute("SELECT username FROM users ORDER BY username").fetchall()]


def db_create_task(task_id: str, user_request: str, task_name: str = "", username: str = ""):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO tasks (id, username, user_request, task_name, status) VALUES (?, ?, ?, ?, 'running')",
        (task_id, username, user_request, task_name),
    )
    conn.commit()


def db_update_task_progress(task_id: str, progress: dict):
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET progress = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
        (json.dumps(progress, ensure_ascii=False), task_id),
    )
    conn.commit()


def db_complete_task(task_id: str, result: dict):
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET status = 'completed', result_json = ?, "
        "updated_at = datetime('now', 'localtime') WHERE id = ?",
        (json.dumps(result, ensure_ascii=False), task_id),
    )
    conn.commit()


def db_fail_task(task_id: str, error: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET status = 'failed', error = ?, "
        "updated_at = datetime('now', 'localtime') WHERE id = ?",
        (error, task_id),
    )
    conn.commit()


def db_get_task(task_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    task = dict(row)
    for field in ("progress", "result_json"):
        if task.get(field):
            try:
                task[field] = json.loads(task[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return task


def db_list_tasks(username: str = "", status: str = "", limit: int = 20) -> list[dict]:
    conn = _get_conn()
    query = ("SELECT id, username, user_request, task_name, status, created_at, updated_at "
             "FROM tasks WHERE 1=1")
    params = []
    if username:
        query += " AND username = ?"
        params.append(username)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def db_save_memory(experience: dict):
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO memories (id, task_type, tags, task_description, one_line_summary,
           successes, lessons, improvements, quality_score, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (experience["id"], experience.get("task_type", ""),
         json.dumps(experience.get("tags", []), ensure_ascii=False),
         experience.get("task_description", ""), experience.get("one_line_summary", ""),
         json.dumps(experience.get("successes", []), ensure_ascii=False),
         json.dumps(experience.get("lessons", []), ensure_ascii=False),
         json.dumps(experience.get("improvements", []), ensure_ascii=False),
         experience.get("quality_score", 5),
         experience.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
    )
    conn.commit()


def db_search_memories(keyword: str = "", task_type: str = "", limit: int = 20) -> list[dict]:
    conn = _get_conn()
    query = "SELECT * FROM memories WHERE 1=1"
    params = []
    if keyword:
        query += " AND (task_description LIKE ? OR one_line_summary LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if task_type:
        query += " AND task_type = ?"
        params.append(task_type)
    query += " ORDER BY quality_score DESC, created_at DESC LIMIT ?"
    params.append(limit)
    results = []
    for r in conn.execute(query, params).fetchall():
        mem = dict(r)
        for field in ("tags", "successes", "lessons", "improvements"):
            if mem.get(field):
                try:
                    mem[field] = json.loads(mem[field])
                except (json.JSONDecodeError, TypeError):
                    mem[field] = []
        results.append(mem)
    return results


def db_count_memories() -> int:
    row = _get_conn().execute("SELECT COUNT(*) as cnt FROM memories").fetchone()
    return row["cnt"] if row else 0


def db_get_all_memories() -> list[dict]:
    return db_search_memories(limit=10000)


def db_get_stats() -> dict:
    conn = _get_conn()
    return {
        "total_tasks": conn.execute("SELECT COUNT(*) as cnt FROM tasks").fetchone()["cnt"],
        "completed_tasks": conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'completed'").fetchone()["cnt"],
        "failed_tasks": conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'failed'").fetchone()["cnt"],
        "total_users": conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"],
        "total_memories": conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()["cnt"],
    }
