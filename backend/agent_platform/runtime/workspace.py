"""工作空间 —— 把"这次任务产出落到哪个本地目录"变成可选配置。

语义对齐 WorkBuddy 的「选择工作空间」：用户先选一个真实存在的本地文件夹，
之后会话产出的 ``需求分析.md`` / ``代码.py`` / ``测试报告.md`` 就写到它下面。

为什么不直接写到工作空间根目录？
    每个任务的产出文件名是固定的（都叫 ``代码.py``），直接平铺进根目录会
    互相覆盖，第二次任务就把第一次的成果冲掉了。所以约定：
        <工作空间>/<任务名>_<任务ID前8位>/需求分析.md
    既落在用户指定的目录范围内，又保证多任务彼此隔离。

注册表放在 ``data/workspaces.json``，结构：
    {"workspaces": [{"name": "...", "path": "...", "created_at": "..."}]}
"""
import json
import os
import threading
from datetime import datetime

from ..utils.paths import DATA_DIR, DELIVERIES_DIR
from ..utils.safe_print import safe_print

_REGISTRY = DATA_DIR / "workspaces.json"
_lock = threading.RLock()

# 未选工作空间时的兜底：项目根目录的 deliveries/
DEFAULT_ID = "__default__"
DEFAULT_NAME = "默认（deliveries）"


def _read() -> list[dict]:
    if not _REGISTRY.exists():
        return []
    try:
        with open(_REGISTRY, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("workspaces", [])
        return [w for w in items if isinstance(w, dict) and w.get("path")]
    except (json.JSONDecodeError, OSError) as e:
        safe_print(f"[工作空间] 注册表读取失败: {e}")
        return []


def _write(items: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _REGISTRY.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"workspaces": items}, f, ensure_ascii=False, indent=2)
    tmp.replace(_REGISTRY)


def list_workspaces() -> list[dict]:
    """列出全部工作空间。返回值带 ``exists``，方便前端标红失效路径。"""
    with _lock:
        items = _read()

    result = [{
        "id": DEFAULT_ID,
        "name": DEFAULT_NAME,
        "path": str(DELIVERIES_DIR),
        "exists": True,
        "builtin": True,
        "created_at": "",
    }]
    for w in items:
        path = w.get("path", "")
        result.append({
            "id": w.get("id") or "",
            "name": w.get("name", ""),
            "path": path,
            "exists": os.path.isdir(path),
            "builtin": False,
            "created_at": w.get("created_at", ""),
        })
    return result


def add_workspace(name: str, path: str) -> tuple[bool, str, dict | None]:
    """新增工作空间。路径不存在时尝试创建，创建失败则报错。"""
    name = (name or "").strip()
    path = (path or "").strip().strip('"').strip("'")
    if not name:
        return False, "工作空间名称不能为空", None
    if not path:
        return False, "目录路径不能为空", None
    if not os.path.isabs(path):
        return False, "请填写绝对路径，例如 D:\\projects\\demo", None

    path = os.path.normpath(path)
    if not os.path.isdir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            return False, f"目录不可用: {e}", None

    with _lock:
        items = _read()
        for w in items:
            if os.path.normpath(w.get("path", "")) == path:
                return False, f"该目录已注册为「{w.get('name')}」", None
            if w.get("name") == name:
                return False, f"名称「{name}」已被占用", None
        item = {
            "id": f"ws_{datetime.now().strftime('%H%M%S%f')[:10]}",
            "name": name,
            "path": path,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        items.append(item)
        _write(items)

    safe_print(f"[工作空间] 已注册 {name} -> {path}")
    return True, f"已添加工作空间「{name}」", item


def remove_workspace(workspace_id: str) -> tuple[bool, str]:
    """按 id 移除工作空间（只删注册记录，不动磁盘上的文件）。"""
    with _lock:
        items = _read()
        left = [w for w in items if w.get("id") != workspace_id]
        if len(left) == len(items):
            return False, "未找到该工作空间"
        _write(left)
    return True, "已移除"


def resolve_workspace_dir(workspace_id: str) -> tuple[str, str]:
    """把工作空间 id 解析成 (根目录, 显示名)。找不到时退回默认目录。"""
    if not workspace_id or workspace_id == DEFAULT_ID:
        return str(DELIVERIES_DIR), DEFAULT_NAME
    for w in list_workspaces():
        if w["id"] == workspace_id:
            if not w["exists"]:
                safe_print(f"[工作空间] {w['path']} 不存在，退回默认目录")
                return str(DELIVERIES_DIR), DEFAULT_NAME
            return w["path"], w["name"]
    return str(DELIVERIES_DIR), DEFAULT_NAME
