"""[TOOL_CALL: ...] 文本协议 —— 解析、流式过滤、展示清洗。

三个用途都在这一个模块，因为它们必须共用同一套标记常量：
    1. ``parse_and_execute_tool_calls``：执行文本协议的工具调用（降级兜底路径）；
    2. ``ToolCallStreamFilter``：把"工具调用块"从流式输出里摘掉 —— 老版本把
       29KB 的 HTML 正文当作对话内容一个字一个字推进前端，界面直接被糊死；
    3. ``strip_tool_blocks`` / ``display_text``：把协议标记与信封从"给人看"的
       文本里去掉。
"""
import re

from ..utils.safe_print import safe_print
from .execution import execute_tool

CALL_OPEN = "[TOOL_CALL:"
CALL_CLOSE = "[/TOOL_CALL]"

# 工具结果区块的边界标记（只存在于"返回值"里，不会发给模型）。
# 有了它，展示层就能精确去掉这些区块，而不是靠 "## xxx 执行结果" 这种
# 模型也可能自己写出来的标题去猜。
RESULT_OPEN = "<!--TOOL_RESULT-->"
RESULT_CLOSE = "<!--/TOOL_RESULT-->"

_TOOL_CALL_PATTERN = re.compile(r'\[TOOL_CALL:\s*(\w+)(?::\s*(.*?))?\]')
_ENVELOPE_PATTERN = re.compile(r'\[RESULT\][\s\S]*?\[/RESULT\]')

# 结果信封（给平台判定的元数据）的标记
ENVELOPE_OPEN = "[RESULT]"
ENVELOPE_CLOSE = "[/RESULT]"

# 流式过滤要吞掉的两类块：(开标记, 闭标记)
_SWALLOW_BLOCKS = ((CALL_OPEN, CALL_CLOSE), (ENVELOPE_OPEN, ENVELOPE_CLOSE))


def parse_and_execute_tool_calls(text: str) -> list[dict]:
    """从文本中提取所有 [TOOL_CALL: ...] 指令并逐一执行。"""
    lines = text.split("\n")
    results = []
    i = 0

    while i < len(lines):
        match = _TOOL_CALL_PATTERN.match(lines[i].strip())
        if match:
            tool_name = match.group(1)
            params = (match.group(2) or "").strip()

            content_lines = []
            i += 1
            while i < len(lines):
                if lines[i].strip() == CALL_CLOSE:
                    break
                content_lines.append(lines[i])
                i += 1
            content_body = "\n".join(content_lines)

            safe_print(f"\n[工具系统] 执行工具: {tool_name}, 参数: {params or '(无)'}")
            exec_result = execute_tool(tool_name, params, content_body)
            preview = exec_result[:200] + ("..." if len(exec_result) > 200 else "")
            safe_print(f"[工具系统] 执行结果: {preview}")

            results.append({"tool": tool_name, "params": params, "result": exec_result})
        i += 1

    return results


def scan_tool_calls(text: str) -> list[dict]:
    """只扫描不执行，返回 [{tool, params}]。

    用于在执行前先把"运行中"上报给前端 —— 否则跑一个几秒的脚本时
    界面上没有任何反馈。
    """
    found = []
    for line in (text or "").split("\n"):
        match = _TOOL_CALL_PATTERN.match(line.strip())
        if match:
            found.append({"tool": match.group(1), "params": (match.group(2) or "").strip()})
    return found


class ToolCallStreamFilter:
    """从 LLM 流式片段里剔除"不该给人看"的块，只把正文吐出去。

    要吞掉的两类：
        ``[TOOL_CALL: ...] ... [/TOOL_CALL]`` —— 工具指令 **和它的参数正文**
            （create_file 的参数就是整篇文件，老版本把它当对话内容推给前端，
            29KB 的 HTML 一个字一个字往外蹦）；
        ``[RESULT] ... [/RESULT]`` —— 子 Agent 交给平台的结果信封，
            是 status/evidence/confidence 之类的元数据，不是给人读的正文。

    为什么不能按行过滤：流式片段会在任意位置切断，``[TOOL_C`` / ``ALL: xxx]``
    可能分两条到达。所以未消费的尾部按标记长度留一个"安全尾巴"。

    约定：
        feed(text) -> 可以直接推给前端的文本（可能是空串）
        flush()    -> 本轮结束时调用；未闭合的块整段丢弃
    """

    def __init__(self) -> None:
        self._hold = ""
        self._close = ""          # 空串 = 不在任何块里
        self._dropped_chars = 0   # 被摘掉的字符数（诊断用：确实发生过过滤）

    @property
    def dropped_chars(self) -> int:
        return self._dropped_chars

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self._hold += text
        out: list[str] = []

        while self._hold:
            if self._close:
                end = self._hold.find(self._close)
                if end == -1:
                    # 还没闭合：只留可能是半个闭合标记的尾巴，其余丢弃
                    keep = len(self._close) - 1
                    if len(self._hold) > keep:
                        self._dropped_chars += len(self._hold) - keep
                        self._hold = self._hold[-keep:]
                    break
                self._dropped_chars += end + len(self._close)
                self._hold = self._hold[end + len(self._close):]
                self._close = ""
                continue

            # 找最早出现的开标记
            hit_pos = -1
            hit_open = ""
            hit_close = ""
            for opener, closer in _SWALLOW_BLOCKS:
                pos = self._hold.find(opener)
                if pos != -1 and (hit_pos == -1 or pos < hit_pos):
                    hit_pos, hit_open, hit_close = pos, opener, closer
            if hit_pos == -1:
                # 可能是半个标记，尾巴留着等下一片
                keep = max(len(opener) for opener, _ in _SWALLOW_BLOCKS) - 1
                if len(self._hold) > keep:
                    cut = len(self._hold) - keep
                    out.append(self._hold[:cut])
                    self._hold = self._hold[cut:]
                break

            if hit_pos > 0:
                out.append(self._hold[:hit_pos])
            self._dropped_chars += hit_pos
            self._hold = self._hold[hit_pos + len(hit_open):]
            self._close = hit_close

        return "".join(out)

    def flush(self) -> str:
        """本轮结束：未闭合的块丢弃；否则把剩余尾巴吐出来。"""
        if self._close:
            self._dropped_chars += len(self._hold)
            self._hold = ""
            self._close = ""
            return ""
        rest, self._hold = self._hold, ""
        return rest


def strip_tool_blocks(text: str) -> str:
    """去掉文本里的工具调用块（含块内正文），用于展示。

    未闭合的块从标记处一直丢到末尾 —— 那本来就是没写完的工具参数。
    """
    source = text or ""
    out: list[str] = []
    pos = 0
    while True:
        start = source.find(CALL_OPEN, pos)
        if start == -1:
            out.append(source[pos:])
            break
        out.append(source[pos:start])
        end = source.find(CALL_CLOSE, start)
        if end == -1:
            break
        pos = end + len(CALL_CLOSE)
    return "".join(out)


def strip_envelope(text: str) -> str:
    """去掉结果信封（给平台看的元数据，不是给人读的正文）。"""
    return _ENVELOPE_PATTERN.sub("", text or "")


def wrap_tool_result(text: str) -> str:
    """给工具结果区块加上边界标记。

    返回值里必须保留工具结果（``extract_code`` 等下游逻辑要用），
    但展示时必须去掉。用显式标记而不是推断标题，才不会误伤模型正文。
    """
    return f"\n\n{RESULT_OPEN}\n{text}\n{RESULT_CLOSE}"


def strip_tool_results(text: str) -> str:
    """去掉被 ``wrap_tool_result`` 包起来的工具结果区块。"""
    pattern = re.escape(RESULT_OPEN) + r"[\s\S]*?" + re.escape(RESULT_CLOSE)
    return re.sub(pattern, "", text or "")


def display_text(text: str) -> str:
    """展示专用清洗：去工具块 + 去工具结果 + 去信封 + 收敛空行。"""
    cleaned = strip_envelope(strip_tool_results(strip_tool_blocks(text or "")))
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
