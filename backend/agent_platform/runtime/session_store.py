"""会话持久化 —— 让"左侧的历史会话"是真的能点开回放的。

存储形态（每个会话两个文件，放在 ``data/sessions/``）：
    <id>.meta.json   会话元信息：标题、工作空间、状态、时间
    <id>.jsonl       事件流，一行一条 JSON

为什么事件流用 JSONL 而不是一个大 JSON 数组？
    群聊每产生一条消息就要落盘一次。JSON 数组必须整体读-改-写（O(n)），
    而 JSONL 只做一次 append（O(1)），并且天然"写坏了也只坏最后一行"。
    这是日志系统的常规做法，代价是读取时要逐行解析 —— 会话量级下完全可忽略。

状态机（status）：
    running → waiting（等人工确认）→ running → ... → done
                                            ↘ error / stopped
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime

from ..utils.paths import DATA_DIR
from ..utils.safe_print import safe_print

SESSIONS_DIR = DATA_DIR / "sessions"
_lock = threading.RLock()

# 本进程内已被删除的会话 id。
# 为什么需要：删除时任务可能还在跑（终止是协作式的），它下一次
# append/update 会把 .jsonl / .meta.json 重新创建出来 —— 表现就是
# "历史会话删不掉，刷新又回来了"（2026-10-19 实测）。这里做成墓碑，
# 删除之后任何写入都直接丢弃。
_deleted: set[str] = set()

# session_id -> 工作空间目录。交付目录在任务执行期间会被反复查询，
# 每次都去读一遍 JSON 文件没有必要，所以在这里缓存一层。
_ws_cache: dict[str, str] = {}

STATUS_RUNNING = "running"
STATUS_WAITING = "waiting"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_STOPPED = "stopped"

STATUS_LABEL = {
    STATUS_RUNNING: "进行中",
    STATUS_WAITING: "待确认",
    STATUS_DONE: "已完成",
    STATUS_ERROR: "失败",
    STATUS_STOPPED: "已终止",
}


def _dir() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _meta_path(session_id: str) -> str:
    return str(SESSIONS_DIR / f"{session_id}.meta.json")


def _log_path(session_id: str) -> str:
    return str(SESSIONS_DIR / f"{session_id}.jsonl")


def _valid_id(session_id: str) -> bool:
    """防止路径穿越：只允许我们自己生成的 id 形态。"""
    return bool(session_id) and all(
        c.isalnum() or c in "-_" for c in session_id
    ) and len(session_id) <= 64


# ---------------------------------------------------------------------------
# 元信息
# ---------------------------------------------------------------------------
def create(title: str, user_request: str, workspace_id: str = "",
           workspace_name: str = "", workspace_path: str = "") -> dict:
    """新建一个会话，返回元信息。"""
    _dir()
    sid = f"s_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    meta = {
        "id": sid,
        "title": (title or user_request or "新会话").strip()[:60],
        "user_request": user_request,
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "workspace_path": workspace_path,
        "status": STATUS_RUNNING,
        "message_count": 0,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": time.time(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_meta(meta)
    _ws_cache[sid] = workspace_path or ""
    safe_print(f"[会话] 新建 {sid}: {meta['title']}")
    return meta


def _save_meta(meta: dict) -> None:
    if meta.get("id") in _deleted:
        # 已删除的会话不允许被"写回来"：删除瞬间任务可能还在跑，
        # 它下一次 update/append 会把文件重新创建出来 —— 用户看到的就是"删了又回来"。
        return
    with _lock:
        _dir()
        path = _meta_path(meta["id"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def load(session_id: str) -> dict | None:
    if not _valid_id(session_id):
        return None
    path = _meta_path(session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def update(session_id: str, **fields) -> dict | None:
    """更新元信息字段。"""
    meta = load(session_id)
    if not meta:
        return None
    meta.update(fields)
    meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_meta(meta)
    return meta


def list_sessions(limit: int = 50, workspace_id: str = "") -> list[dict]:
    """按创建时间倒序列出会话（跳过流水号式的临时目录）。"""
    _dir()
    items: list[dict] = []
    try:
        names = os.listdir(SESSIONS_DIR)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".meta.json"):
            continue
        sid = name[: -len(".meta.json")]
        meta = load(sid)
        if not meta:
            continue
        if workspace_id and meta.get("workspace_id") != workspace_id:
            continue
        # 事件条数以磁盘上的 JSONL 行为准，覆盖掉 meta 里那个恒为 0 的字段
        meta["message_count"] = count_events(sid)
        items.append(meta)
    items.sort(key=lambda m: m.get("created_ts", 0), reverse=True)
    return items[:limit]


def reconcile_stale(is_alive=None) -> int:
    """把上次进程遗留的"进行中 / 待确认"会话收尾成"已终止"。

    会话状态写在 meta 文件里会一直留着，但真正在跑的 Agent 只活在内存中 ——
    服务一重启，这些会话就绝无可能再有任何进展。不收拾的话，侧边栏会一直
    挂着"待确认"，用户点进去等半天也等不到 Agent 的下一句话，只会以为卡死了。

    Args:
        is_alive: 可选回调，返回 True 表示该会话确实还有任务在跑（同进程内重启
            router 的场景），这类会话会被跳过。
    """
    n = 0
    for meta in list_sessions(limit=10000):
        if meta.get("status") not in (STATUS_RUNNING, STATUS_WAITING):
            continue
        sid = meta["id"]
        if is_alive is not None and is_alive(sid):
            continue
        update(sid, status=STATUS_STOPPED)
        n += 1
    if n:
        safe_print(f"[会话] 已把 {n} 条重启前遗留的会话标记为已终止")
    return n


def delete(session_id: str) -> bool:
    if not _valid_id(session_id):
        return False
    # 先立墓碑再删文件：中间这段时间里任务线程仍然可能写盘，
    # 没有墓碑的话刚删掉的文件会被重新创建出来。
    _deleted.add(session_id)
    _ws_cache.pop(session_id, None)
    removed = False
    for path in (_meta_path(session_id), _log_path(session_id)):
        if not os.path.exists(path):
            continue
        # Windows 上文件可能还被正在收尾的线程占着，给两次重试的机会
        for attempt in range(3):
            try:
                os.remove(path)
                removed = True
                break
            except OSError as e:
                if attempt == 2:
                    safe_print(f"[会话] 删除失败 {path}: {e}")
                else:
                    time.sleep(0.05 * (attempt + 1))
    return removed


# ---------------------------------------------------------------------------
# 事件流
# ---------------------------------------------------------------------------
def append(session_id: str, event: dict) -> None:
    """把一条事件追加到会话日志。写失败不影响主流程。"""
    if not _valid_id(session_id) or session_id in _deleted:
        return
    try:
        with _lock:
            _dir()
            with open(_log_path(session_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        safe_print(f"[会话] 事件落盘失败: {e}")


def read_events(session_id: str, limit: int = 500) -> list[dict]:
    """读取会话的全部事件（用于打开历史会话时回放）。"""
    if not _valid_id(session_id):
        return []
    path = _log_path(session_id)
    if not os.path.exists(path):
        return []
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    # 最后一行可能因进程被杀而写坏，跳过即可
                    continue
    except OSError:
        return []
    return events[-limit:]


def count_events(session_id: str) -> int:
    """统计会话的事件条数（侧边栏"共 N 条"用）。

    直接数 JSONL 的行数，而不是维护 meta 里的递增计数器：计数器要在每条
    事件之后重写一次 meta 文件，既贵又容易漏（进程被杀就丢一段）。
    行数是磁盘上的真实值，永远不会对不上。
    """
    if not _valid_id(session_id):
        return 0
    path = _log_path(session_id)
    if not os.path.exists(path):
        return 0
    n = 0
    try:
        with open(path, "rb") as f:
            for _ in f:
                n += 1
    except OSError:
        return 0
    return n


def get_workspace_path(session_id: str) -> str:
    """取会话绑定的工作空间目录（空串表示用默认交付目录）。

    ``storage.state.get_current_task_dir`` 会在每个 Agent 落文件时调用它，
    所以走内存缓存，不重复读盘。
    """
    if not _valid_id(session_id):
        return ""
    if session_id in _ws_cache:
        return _ws_cache[session_id]
    meta = load(session_id)
    path = (meta or {}).get("workspace_path", "") or ""
    if path and not os.path.isdir(path):
        safe_print(f"[会话] 工作空间 {path} 已不存在，本次任务退回默认目录")
        path = ""
    _ws_cache[session_id] = path
    return path
