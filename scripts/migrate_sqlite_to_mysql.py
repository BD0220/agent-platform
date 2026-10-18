"""一次性迁移：把旧 SQLite（data/platform.db）里的 tasks / memories 搬进 MySQL。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\migrate_sqlite_to_mysql.py

幂等：按主键 INSERT ... ON DUPLICATE KEY UPDATE，重复跑不会产生重复数据。
跑完后旧 platform.db 保留在原地不动，确认无误后可自行删除。
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(ROOT)

# .env 里的 MYSQL_* 先装进环境变量
def load_env_file(root: str):
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.split(" #")[0].strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file(ROOT)

from agent_platform.storage.database import (  # noqa: E402
    Memory,
    Task,
    _get_engine,
    _upsert,
    init_db,
)
from sqlalchemy import func, select  # noqa: E402

SQLITE_FILE = os.path.join(ROOT, "data", "platform.db")


def rows(conn, table):
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
    except sqlite3.OperationalError as e:
        print(f"[migrate] skip {table}: {e}")
        return []


def upsert_all(engine, model, items):
    n = 0
    for item in items:
        values = {c.name: item.get(c.name) for c in model.__table__.columns}
        values = {k: v for k, v in values.items() if v is not None or k == "id"}
        _upsert(engine, model, values)
        n += 1
    return n


def main():
    if not os.path.exists(SQLITE_FILE):
        print(f"[迁移] 找不到 {SQLITE_FILE}，无事可做")
        return
    init_db()
    engine = _get_engine()
    src = sqlite3.connect(SQLITE_FILE)
    src.row_factory = sqlite3.Row

    tasks = rows(src, "tasks")
    memories = rows(src, "memories")
    print(f"[migrate] sqlite tasks={len(tasks)} memories={len(memories)}")

    print(f"[migrate] tasks upserted: {upsert_all(engine, Task, tasks)}")
    print(f"[migrate] memories upserted: {upsert_all(engine, Memory, memories)}")

    # 校验
    with engine.connect() as conn:
        t = conn.execute(select(func.count()).select_from(Task)).scalar()
        m = conn.execute(select(func.count()).select_from(Memory)).scalar()
    print(f"[migrate] mysql now tasks={t} memories={m}")
    print("[migrate] done")


if __name__ == "__main__":
    main()
