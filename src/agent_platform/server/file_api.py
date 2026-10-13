"""文件浏览 API —— 左中两栏（文件树 + 代码查看器）的后端。

安全边界（这个模块唯一的难点）
    界面能让用户点开本地文件，所以"用户传什么路径就读什么文件"是绝对不能接受的 ——
    那等于把整个磁盘暴露出去（``?path=C:\\Windows\\System32\\config\\SAM``）。
    规则：**只允许读已登记工作空间目录以内的东西**，其他一律 403。
    校验用 ``os.path.realpath`` 之后再比前缀，这样 ``..\\..`` 和
    指向外面的符号链接都会在解析后被拦下来。
"""
import os

from fastapi import APIRouter, HTTPException

from ..runtime import workspace
from ..utils.paths import DELIVERIES_DIR

router = APIRouter(tags=["files"])

# 这些目录进去只会拖慢遍历、还容易被当成"源码"看，直接不展开
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", ".idea", ".vscode", ".vs", "dist", "build", "out",
    ".next", ".nuxt", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", "site-packages", ".cache", "target",
}
SKIP_SUFFIX = (".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
               ".zip", ".7z", ".rar", ".tar", ".gz", ".whl", ".iso")
MAX_BYTES = 400_000          # 单个文件最多读 400 KB
BINARY_SNIFF = 8192          # 前 8 KB 里出现 \x00 就当成二进制

# gr.Code 的 language 是一份**白名单**（Literal），传它不认识的值会报错，
# 所以这里只允许映射到白名单里的成员：
#   python c cpp markdown latex json html css javascript jinja2
#   typescript yaml dockerfile shell r sql
# 没有对应高亮的语言一律落到 markdown —— CodeMirror 的 markdown 模式是
# 逐字显示的，不会把代码当富文本渲染，比硬套一个错误的语言安全。
LANG_BY_EXT = {
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".json": "json", ".jsonl": "json", ".ipynb": "json",
    ".html": "html", ".htm": "html", ".vue": "html", ".svelte": "html",
    ".css": "css", ".scss": "css", ".less": "css",
    ".md": "markdown", ".markdown": "markdown", ".rst": "markdown",
    ".yml": "yaml", ".yaml": "yaml",
    ".sql": "sql",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".bat": "shell", ".cmd": "shell", ".ps1": "shell",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cxx": "cpp",
    ".tex": "latex",
    ".r": "r",
    ".j2": "jinja2", ".jinja": "jinja2", ".jinja2": "jinja2",
}
# 兜底：不在表里的后缀都用这个（逐字显示，带行号）
_SAFE_LANG = "markdown"


def _lang_of(name: str) -> str:
    lower = name.lower()
    if lower == "dockerfile":
        return "dockerfile"
    return LANG_BY_EXT.get(os.path.splitext(lower)[1], _SAFE_LANG)


def _allowed_roots() -> list[str]:
    """可以浏览的根目录：默认交付目录 + 全部已登记且存在的工作空间。"""
    roots = {str(DELIVERIES_DIR)}
    for w in workspace.list_workspaces():
        if w.get("exists") and w.get("path"):
            roots.add(w["path"])
    return [os.path.realpath(r) for r in roots]


def _safe_path(raw: str, must_exist: bool = True) -> str:
    """把用户传来的路径解析成绝对路径，并确认它落在允许的根目录里。"""
    if not raw or not raw.strip():
        raise HTTPException(status_code=400, detail="缺少路径参数")
    real = os.path.realpath(os.path.abspath(raw.strip().strip('"').strip("'")))
    for root in _allowed_roots():
        if real == root or real.startswith(root + os.sep):
            if must_exist and not os.path.exists(real):
                raise HTTPException(status_code=404, detail="路径不存在")
            return real
    raise HTTPException(status_code=403, detail="该路径不在任何已登记的工作空间内")


@router.get("/workspaces/roots")
def api_workspace_roots():
    """文件树用的根目录列表（带 exists 标记，失效的标红）。"""
    items = workspace.list_workspaces()
    return {"status": "success", "roots": items}


@router.get("/files/tree")
def api_tree(path: str = "", depth: int = 4, max_nodes: int = 1500):
    """返回一个目录的嵌套树。

    加了两个闸门（``depth`` / ``max_nodes``）：工作空间里放着一个
    几万文件的目录时，不限制会把响应撑到几十兆、浏览器直接卡死。
    """
    root = _safe_path(path)
    if not os.path.isdir(root):
        raise HTTPException(status_code=400, detail="该路径不是目录")

    budget = {"left": max(1, min(max_nodes, 20000))}
    truncated = {"hit": False}

    def walk(cur: str, level: int) -> list[dict]:
        if budget["left"] <= 0:
            truncated["hit"] = True
            return []
        try:
            entries = sorted(
                os.scandir(cur),
                key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
            )
        except OSError:
            return []

        nodes = []
        for e in entries:
            if budget["left"] <= 0:
                truncated["hit"] = True
                break
            name = e.name
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if name in SKIP_DIRS or name.startswith("."):
                    continue
            else:
                if name.lower().endswith(SKIP_SUFFIX):
                    continue

            budget["left"] -= 1
            node = {"name": name, "path": e.path, "dir": is_dir}
            if is_dir:
                node["children"] = walk(e.path, level + 1) if level < depth else []
                if level >= depth:
                    node["lazy"] = True
            else:
                try:
                    node["size"] = e.stat(follow_symlinks=False).st_size
                except OSError:
                    node["size"] = 0
            nodes.append(node)
        return nodes

    tree = walk(root, 0)
    return {
        "status": "success",
        "root": root,
        "name": os.path.basename(root) or root,
        "depth": depth,
        "tree": tree,
        "truncated": truncated["hit"],
    }


@router.get("/files/content")
def api_content(path: str = ""):
    """读一个文本文件的内容。二进制文件不报错，回一个 ``binary`` 标记让界面说清楚。"""
    real = _safe_path(path)
    if os.path.isdir(real):
        raise HTTPException(status_code=400, detail="这是一个目录")

    size = os.path.getsize(real)
    with open(real, "rb") as f:
        head = f.read(BINARY_SNIFF)
        if b"\x00" in head:
            return {"status": "success", "path": real,
                    "name": os.path.basename(real), "binary": True,
                    "size": size, "text": ""}
        rest = f.read(max(0, MAX_BYTES - len(head)))

    raw = head + rest
    truncated = size > len(raw)
    text = raw.decode("utf-8", errors="replace")
    return {
        "status": "success",
        "path": real,
        "name": os.path.basename(real),
        "binary": False,
        "size": size,
        "truncated": truncated,
        "lines": text.count("\n") + 1,
        "language": _lang_of(os.path.basename(real)),
        "text": text,
    }
