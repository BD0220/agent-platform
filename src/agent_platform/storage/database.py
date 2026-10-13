"""数据库层 —— MySQL（SQLAlchemy + PyMySQL），tasks / memories 两张表。

2026-10-13 从 SQLite 迁移到 MySQL：
- 连接参数从环境变量 ``MYSQL_*`` 读取（run.py 启动时会把项目根目录的 ``.env``
  装进环境变量）；没配 ``MYSQL_*`` 时兜底回 SQLite（``data/platform.db``），
  本地零配置也能跑，但主路径是 MySQL。
- 旧版的 users 表随登录体系一起删除 —— 单机自用没有"多用户"这个概念。
- ``init_db()`` 幂等；所有写操作走短事务（``engine.begin()``），天然并发安全，
  不再需要旧版那种手写的 30 秒 timeout / WAL 折腾。
"""
import json
import os
from datetime import datetime
from urllib.parse import quote_plus

from sqlalchemy import (
    String, Text, create_engine, insert, select, update, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..utils.paths import DATA_DIR, ensure_dirs
from ..utils.safe_print import safe_print

_now = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731


class Base(DeclarativeBase):
    pass


class Task(Base):
    """一次任务/会话的登记与结果。id 直接复用 session_id。"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    user_request: Mapped[str] = mapped_column(Text)
    task_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    progress: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(19), default=_now, index=True)
    updated_at: Mapped[str] = mapped_column(String(19), default=_now, onupdate=_now)


class Memory(Base):
    """任务完成后沉淀的经验（RAG 长期记忆的结构化部分）。"""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), default="", index=True)
    tags: Mapped[str] = mapped_column(Text, default="")
    task_description: Mapped[str] = mapped_column(Text, default="")
    one_line_summary: Mapped[str] = mapped_column(Text, default="")
    successes: Mapped[str] = mapped_column(Text, default="")
    lessons: Mapped[str] = mapped_column(Text, default="")
    improvements: Mapped[str] = mapped_column(Text, default="")
    quality_score: Mapped[int] = mapped_column(default=5)
    created_at: Mapped[str] = mapped_column(String(19), default=_now, index=True)


def _build_url() -> str:
    user = os.getenv("MYSQL_USER", "")
    if not user:
        # 未配置 MySQL：兜底 SQLite，保证本地零配置可用
        ensure_dirs()
        safe_print("[数据库] 未配置 MYSQL_*，使用 SQLite 兜底（data/platform.db）")
        return "sqlite:///" + str(DATA_DIR / "platform.db")
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    pw = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
    db = os.getenv("MYSQL_DATABASE", "abundance")
    return f"mysql+pymysql://{quote_plus(user)}:{pw}@{host}:{port}/{db}?charset=utf8mb4"


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            _build_url(),
            pool_pre_ping=True,      # MySQL 8 小时不互动会断连，取连接前先 ping
            pool_recycle=3600,
            future=True,
        )
    return _engine


def init_db():
    """建表。幂等，可安全重复调用。"""
    Base.metadata.create_all(_get_engine())
    safe_print("[数据库] 初始化完成")


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------
def db_create_task(task_id: str, user_request: str, task_name: str = "", username: str = ""):
    with _get_engine().begin() as conn:
        conn.execute(insert(Task).values(
            id=task_id, username=username, user_request=user_request,
            task_name=task_name, status="running",
        ))


def db_update_task_progress(task_id: str, progress: dict):
    with _get_engine().begin() as conn:
        conn.execute(update(Task).where(Task.id == task_id).values(
            progress=json.dumps(progress, ensure_ascii=False), updated_at=_now(),
        ))


def db_complete_task(task_id: str, result: dict):
    with _get_engine().begin() as conn:
        conn.execute(update(Task).where(Task.id == task_id).values(
            status="completed",
            result_json=json.dumps(result, ensure_ascii=False),
            updated_at=_now(),
        ))


def db_fail_task(task_id: str, error: str):
    with _get_engine().begin() as conn:
        conn.execute(update(Task).where(Task.id == task_id).values(
            status="failed", error=error, updated_at=_now(),
        ))


def db_get_task(task_id: str) -> dict | None:
    with _get_engine().connect() as conn:
        row = conn.execute(select(Task).where(Task.id == task_id)).first()
    if not row:
        return None
    task = {
        "id": row.id, "username": row.username, "user_request": row.user_request,
        "task_name": row.task_name, "status": row.status,
        "progress": row.progress, "result_json": row.result_json,
        "error": row.error, "created_at": row.created_at, "updated_at": row.updated_at,
    }
    for field in ("progress", "result_json"):
        if task.get(field):
            try:
                task[field] = json.loads(task[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return task


def db_list_tasks(username: str = "", status: str = "", limit: int = 20) -> list[dict]:
    stmt = select(Task.id, Task.username, Task.user_request, Task.task_name,
                  Task.status, Task.created_at, Task.updated_at)
    if username:
        stmt = stmt.where(Task.username == username)
    if status:
        stmt = stmt.where(Task.status == status)
    stmt = stmt.order_by(Task.created_at.desc()).limit(limit)
    with _get_engine().connect() as conn:
        rows = conn.execute(stmt).all()
    keys = ("id", "username", "user_request", "task_name",
            "status", "created_at", "updated_at")
    return [dict(zip(keys, r)) for r in rows]


# ---------------------------------------------------------------------------
# memories
# ---------------------------------------------------------------------------
def _upsert(engine, model, values: dict):
    """跨方言 upsert：MySQL 用 ON DUPLICATE KEY，SQLite 用 ON CONFLICT。

    迁移脚本和 db_save_memory 共用，保证"写 MySQL 主路径 + SQLite 兜底"
    两条路行为一致。
    """
    update_fields = {k: v for k, v in values.items() if k != "id"}
    if engine.dialect.name == "mysql":
        from sqlalchemy.dialects.mysql import insert as dialect_insert

        stmt = dialect_insert(model).values(**values)
        stmt = stmt.on_duplicate_key_update(**update_fields)
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        stmt = dialect_insert(model).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[model.__table__.primary_key.columns[0]], set_=update_fields
        )
    with engine.begin() as conn:
        conn.execute(stmt)


def db_save_memory(experience: dict):
    values = dict(
        id=experience["id"],
        task_type=experience.get("task_type", ""),
        tags=json.dumps(experience.get("tags", []), ensure_ascii=False),
        task_description=experience.get("task_description", ""),
        one_line_summary=experience.get("one_line_summary", ""),
        successes=json.dumps(experience.get("successes", []), ensure_ascii=False),
        lessons=json.dumps(experience.get("lessons", []), ensure_ascii=False),
        improvements=json.dumps(experience.get("improvements", []), ensure_ascii=False),
        quality_score=experience.get("quality_score", 5),
        created_at=experience.get("created_at", _now()),
    )
    _upsert(_get_engine(), Memory, values)


def db_search_memories(keyword: str = "", task_type: str = "", limit: int = 20) -> list[dict]:
    stmt = select(Memory)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Memory.task_description.like(like) | Memory.one_line_summary.like(like))
    if task_type:
        stmt = stmt.where(Memory.task_type == task_type)
    stmt = stmt.order_by(Memory.quality_score.desc(), Memory.created_at.desc()).limit(limit)
    with _get_engine().connect() as conn:
        rows = conn.execute(stmt).all()
    results = []
    for row in rows:
        mem = dict(row._mapping)
        for field in ("tags", "successes", "lessons", "improvements"):
            if mem.get(field):
                try:
                    mem[field] = json.loads(mem[field])
                except (json.JSONDecodeError, TypeError):
                    mem[field] = []
        results.append(mem)
    return results


def db_count_memories() -> int:
    with _get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(Memory)).scalar() or 0


def db_get_all_memories() -> list[dict]:
    return db_search_memories(limit=10000)


def db_get_stats() -> dict:
    with _get_engine().connect() as conn:
        return {
            "total_tasks": conn.execute(select(func.count()).select_from(Task)).scalar() or 0,
            "completed_tasks": conn.execute(
                select(func.count()).select_from(Task).where(Task.status == "completed")
            ).scalar() or 0,
            "failed_tasks": conn.execute(
                select(func.count()).select_from(Task).where(Task.status == "failed")
            ).scalar() or 0,
            "total_memories": conn.execute(select(func.count()).select_from(Memory)).scalar() or 0,
        }
