"""内建工具 —— 在 _register_all() 中统一注册。"""
from ..registry import register_tool

from .file_tools import _tool_list_files, _tool_create_file, _tool_read_file, _map_create_file, _map_list_files, _map_read_file
from .python_tools import _tool_run_python_file, _map_run_python
from .dispatch import _tool_dispatch_agent, _map_dispatch_agent
from .web_tools import _web_search, _fetch_url, _map_web_search, _map_fetch_url


def _register_all():
    """注册所有内建工具。"""

    register_tool("list_files",
        "列出当前任务交付目录下的所有文件和文件夹，包括文件大小和修改时间。",
        "目录路径（可选，不填则列出当前任务交付目录）",
        _tool_list_files,
        schema={"type": "function", "function": {"name": "list_files",
            "description": "列出当前任务交付目录下的所有文件和文件夹",
            "parameters": {"type": "object", "properties": {
                "directory": {"type": "string", "description": "要列出的目录路径（可选）"}},
                "required": []}}},
        param_mapper=_map_list_files)

    register_tool("create_file",
        "创建一个新文件，保存到当前任务的交付目录中。调用格式: [TOOL_CALL: create_file: 文件名]\\n文件内容...\\n[/TOOL_CALL]",
        "文件名，文件内容写在 TOOL_CALL 和 /TOOL_CALL 之间",
        _tool_create_file,
        schema={"type": "function", "function": {"name": "create_file",
            "description": "创建一个新文件保存到交付目录",
            "parameters": {"type": "object", "properties": {
                "filename": {"type": "string", "description": "文件名，例如 代码.py"},
                "content": {"type": "string", "description": "文件内容"}},
                "required": ["filename", "content"]}}},
        param_mapper=_map_create_file)

    register_tool("run_python_file",
        "用 subprocess 在安全模式下运行指定的 Python 文件，超时 30 秒。",
        "文件名",
        _tool_run_python_file,
        schema={"type": "function", "function": {"name": "run_python_file",
            "description": "运行指定的 Python 文件并返回输出",
            "parameters": {"type": "object", "properties": {
                "filename": {"type": "string", "description": "要运行的 Python 文件名"}},
                "required": ["filename"]}}},
        param_mapper=_map_run_python)

    register_tool("dispatch_agent",
        "主Agent专用工具：将指定任务分派给子Agent（产品经理/程序员/测试员）执行，返回该子Agent的完整输出。",
        "子Agent名称（产品经理/程序员/测试员），任务描述写在 TOOL_CALL 和 /TOOL_CALL 之间",
        _tool_dispatch_agent,
        schema={"type": "function", "function": {"name": "dispatch_agent",
            "description": "将任务分派给子Agent执行并返回结果",
            "parameters": {"type": "object", "properties": {
                "agent_name": {"type": "string", "enum": ["产品经理", "程序员", "测试员"], "description": "子Agent名称"},
                "task": {"type": "string", "description": "详细任务描述"}},
                "required": ["agent_name", "task"]}}},
        param_mapper=_map_dispatch_agent)

    register_tool("web_search",
        "搜索互联网获取信息（通过 DuckDuckGo）。用于查找最新资料、文档、解决方案等。",
        "搜索关键词",
        _web_search,
        schema={"type": "function", "function": {"name": "web_search",
            "description": "搜索互联网获取信息",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"]}}},
        param_mapper=_map_web_search)

    register_tool("fetch_url",
        "发送 HTTP GET 请求获取指定 URL 的网页内容。",
        "URL 地址",
        _fetch_url,
        schema={"type": "function", "function": {"name": "fetch_url",
            "description": "获取指定 URL 的网页内容",
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "description": "要获取的 URL 地址"}},
                "required": ["url"]}}},
        param_mapper=_map_fetch_url)

    register_tool("read_file",
        "读取交付目录中现有文件的内容。用于审查代码、查看报告等。",
        "文件名",
        _tool_read_file,
        schema={"type": "function", "function": {"name": "read_file",
            "description": "读取交付目录中现有文件的内容",
            "parameters": {"type": "object", "properties": {
                "filename": {"type": "string", "description": "要读取的文件名"}},
                "required": ["filename"]}}},
        param_mapper=_map_read_file)

    register_tool("search_knowledge",
        "搜索知识库获取外部文档中的相关信息。用于查找技术文档、参考资料等。",
        "搜索关键词，collection 指定知识库名（可选，默认 default），top_k 返回数量（可选，默认 5）",
        _tool_search_knowledge,
        schema={"type": "function", "function": {"name": "search_knowledge",
            "description": "搜索知识库获取外部文档信息",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "collection": {"type": "string", "description": "知识库名称，默认 default"},
                "top_k": {"type": "integer", "description": "返回结果数量，默认 5"}},
                "required": ["query"]}}},
        param_mapper=_map_search_knowledge)


def _tool_search_knowledge(params: str, content: str) -> str:
    """搜索知识库工具执行函数。"""
    import json
    try:
        from ...rag.import_docs import search_knowledge
        # params 为 JSON 字符串: {"query": "...", "collection": "...", "top_k": 5}
        try:
            args = json.loads(params) if params else {}
        except (json.JSONDecodeError, TypeError):
            args = {}
        if isinstance(args, str):
            args = {"query": args}
        query = args.get("query", params.strip())
        collection = args.get("collection", "default")
        top_k = args.get("top_k", 5)
        return search_knowledge(query, collection, top_k)
    except Exception as e:
        return f"[search_knowledge] 执行失败: {e}"


def _map_search_knowledge(tool_name: str, arguments: dict) -> tuple[str, str]:
    import json
    return json.dumps(arguments, ensure_ascii=False), ""


_register_all()
