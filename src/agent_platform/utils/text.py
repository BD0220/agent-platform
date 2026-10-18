"""文本处理工具 —— Markdown 清洗、代码提取、HTML 转义。"""
import re


def clean_markdown(text: str) -> str:
    """清理 Agent 输出中的 TOOL_CALL 指令和工具执行结果，保留纯 Markdown。"""
    # 移除 TOOL_CALL 块
    text = re.sub(r'\[TOOL_CALL:.*?\].*?\[/TOOL_CALL\]', '', text, flags=re.DOTALL)
    # 移除单行 TOOL_CALL 指令残留
    text = re.sub(r'\[TOOL_CALL:[^\]]*\]', '', text)
    text = re.sub(r'\[/TOOL_CALL\]', '', text)
    # 移除工具执行结果区块
    idx = text.find("## 工具执行结果")
    if idx != -1:
        text = text[:idx]
    # 移除纯工具确认信息行
    text = re.sub(r'^文件已创建:.*$', '', text, flags=re.MULTILINE)
    # 压缩多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def html_escape(text: str) -> str:
    """转义 HTML 特殊字符，防止内容破坏页面结构。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
