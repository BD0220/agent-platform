"""代码清洗工具 —— 从 Agent 输出中提取纯 Python 代码。"""
import re


def extract_code(raw_text: str) -> str:
    """
    多层防御策略提取纯 Python 代码：
    1. 从 [TOOL_CALL: create_file: ...]...[/TOOL_CALL] 块中提取
    2. 剥离工具执行结果区块
    3. 移除残留 TOOL_CALL 指令
    4. 提取 ```python ... ``` 代码块
    5. 启发式定位 Python 代码起始行
    """
    text = raw_text.strip()

    # 第1层：从 create_file 的 TOOL_CALL 块中提取代码
    create_call_match = re.search(
        r'\[TOOL_CALL:\s*create_file\s*:\s*[^\]]*\]\s*\n(.*?)\n\s*\[/TOOL_CALL\]',
        text, re.DOTALL,
    )
    if create_call_match:
        code = create_call_match.group(1).strip()
        if code:
            return code

    # 第2层：剥离工具执行结果区块
    tool_result_start = text.find("## 工具执行结果")
    if tool_result_start != -1:
        text = text[:tool_result_start].strip()

    # 第3层：移除残留的 TOOL_CALL 指令行
    text = re.sub(r'\[TOOL_CALL:[^\]]*\]', '', text)
    text = re.sub(r'\[/TOOL_CALL\]', '', text)
    text = re.sub(r'^文件已创建:.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^工具.*执行.*$', '', text, flags=re.MULTILINE)
    text = text.strip()

    # 第4层：提取 ```python 或 ``` 代码块
    block_start = re.search(r"```(?:python)?\s*\n?", text)
    if block_start:
        remainder = text[block_start.end():]
        block_end = re.search(r"\n?```", remainder)
        if block_end:
            return remainder[:block_end.start()].strip()
        return remainder.strip()

    # 第5层：启发式定位 Python 代码起始行
    _PY_LINE_RE = (
        r"^(?:import\s|from\s|def\s|class\s|"
        r"if\s|for\s|while\s|try\s*:|with\s|"
        r"return\s|yield\s|raise\s|assert\s|"
        r"pass\s*$|break\s*$|continue\s*$|"
        r"#!|#[ ]|@|"
        r"[a-zA-Z_]\w*\s*[=\(:\[])"
    )
    code_start = re.search(_PY_LINE_RE, text, re.MULTILINE)
    if code_start:
        result = text[code_start.start():]
        lines = result.split("\n")
        while lines:
            last = lines[-1].strip()
            if not last:
                break
            if re.match(_PY_LINE_RE + r"|\s+|\)", last):
                break
            chinese_chars = len(re.findall(r"[一-鿿]", last))
            if chinese_chars > 3 and not last.startswith("#"):
                lines.pop()
            else:
                break
        return "\n".join(lines).strip()

    return text
