"""文件操作工具：list_files, create_file, read_file。"""
import os
from datetime import datetime

from ...storage.state import get_current_task_dir
from ...utils.paths import safe_path
from ...utils.safe_print import safe_print


def _get_task_dir() -> str:
    return get_current_task_dir()


def _tool_list_files(params: str, content: str) -> str:
    task_dir = _get_task_dir()
    target_dir = params.strip() if params.strip() else task_dir
    target_dir = os.path.normpath(target_dir)
    if not target_dir.startswith(os.path.normpath(task_dir)):
        target_dir = task_dir

    if not os.path.exists(target_dir):
        return f"目录不存在: {target_dir}"

    try:
        entries = os.listdir(target_dir)
    except PermissionError:
        return f"没有权限访问目录: {target_dir}"

    if not entries:
        return f"目录 '{target_dir}' 为空"

    lines = [f"目录: {target_dir}", f"共 {len(entries)} 个项目:\n"]
    for entry in sorted(entries):
        full = os.path.join(target_dir, entry)
        tag = "[DIR]" if os.path.isdir(full) else "[FILE]"
        size_str = ""
        if os.path.isfile(full):
            size = os.path.getsize(full)
            if size < 1024:
                size_str = f" ({size} B)"
            elif size < 1024 * 1024:
                size_str = f" ({size / 1024:.1f} KB)"
            else:
                size_str = f" ({size / (1024 * 1024):.1f} MB)"
        mtime = datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M")
        lines.append(f"  {tag}  {entry}{size_str}  [{mtime}]")
    return "\n".join(lines)


def _strip_code_fence(content: str) -> str:
    """剥离 LLM 输出中常见的 markdown 代码围栏。

    例如：
        ```python
        print("hello")
        ```
    →
        print("hello")
    """
    import re
    text = content.strip()
    # 匹配开头的 ```lang 或 ``` 以及结尾的 ```
    m = re.match(r"^```[ \t]*[\w+-]*[ \t]*\r?\n(.*?)\r?\n?```[ \t]*$", text, re.DOTALL)
    if m:
        return m.group(1)
    # 只有开头围栏没有结尾围栏（LLM 截断场景）
    m2 = re.match(r"^```[ \t]*[\w+-]*[ \t]*\r?\n", text)
    if m2:
        return text[m2.end():]
    return content


def _tool_create_file(params: str, content: str) -> str:
    if not params.strip():
        return "错误: 请指定文件名"
    if not content.strip():
        return "错误: 请提供文件内容"

    content = _strip_code_fence(content)

    task_dir = _get_task_dir()
    filepath = safe_path(params.strip(), task_dir)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    size = len(content.encode("utf-8"))
    rel_path = os.path.relpath(filepath, os.path.dirname(task_dir))
    safe_print(f"[工具系统] 已创建文件: {rel_path} ({size} 字节)")
    return f"文件已创建: {rel_path} ({size} 字节)"


def _tool_read_file(params: str, content: str) -> str:
    filename = params.strip()
    if not filename:
        return "错误: 请指定文件名"
    task_dir = _get_task_dir()
    filepath = os.path.join(task_dir, os.path.basename(filename))
    if not os.path.exists(filepath):
        return f"文件不存在: {filepath}"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = f.read()
        return f"文件: {filename}\n大小: {len(data)} 字符\n\n{data[:3000]}"
    except Exception as e:
        return f"读取失败: {e}"


def _map_create_file(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("filename", ""), arguments.get("content", "")


def _map_list_files(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("directory", ""), ""


def _map_read_file(tool_name: str, arguments: dict) -> tuple[str, str]:
    return arguments.get("filename", ""), ""
