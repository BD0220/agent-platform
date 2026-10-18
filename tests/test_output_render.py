"""输出可读性改造的回归测试。

覆盖三块新逻辑：
  1. ``ToolCallStreamFilter``：流式过滤工具调用块（含任意位置切断的片段）；
  2. ``display_text``：展示层清洗（工具块 / 工具结果 / 结果信封）；
  3. ``runtime.tool_events``：工具卡片上报的精简与"不外泄参数正文"。

这些是"聊天区被 3 万字符代码糊死"的根因所在，必须锁住行为。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from agent_platform.runtime.tool_events import (  # noqa: E402
    preview_of,
    summarize_args,
)
from agent_platform.tools.parser import (  # noqa: E402
    ToolCallStreamFilter,
    display_text,
    scan_tool_calls,
    strip_tool_blocks,
    strip_tool_results,
    wrap_tool_result,
)

PAYLOAD = "<html>\n" + ("x" * 400) + "\n</html>"
TEXT = f"先说明需求。\n[TOOL_CALL: create_file: index.html]\n{PAYLOAD}\n[/TOOL_CALL]\n收尾：已完成。"


def _feed_in_chunks(chunks):
    guard = ToolCallStreamFilter()
    out = ""
    for chunk in chunks:
        out += guard.feed(chunk)
    out += guard.flush()
    return out, guard


def test_stream_filter_drops_tool_block():
    visible, guard = _feed_in_chunks([TEXT])
    assert "[TOOL_CALL:" not in visible
    assert "<html>" not in visible
    assert "先说明需求。" in visible
    assert "收尾：已完成。" in visible
    assert guard.dropped_chars > 0


def test_stream_filter_survives_split_markers():
    """标记被流式片段从中间切断时，不能漏出半截 [TOOL_C... 。"""
    for size in (1, 2, 3, 5, 7, 11):
        chunks = [TEXT[i:i + size] for i in range(0, len(TEXT), size)]
        visible, _ = _feed_in_chunks(chunks)
        assert "[TOOL" not in visible, f"chunk={size} 泄漏了半截标记"
        assert "<html>" not in visible, f"chunk={size} 泄漏了工具参数"
        assert "先说明需求。" in visible
        assert "收尾：已完成。" in visible


def test_stream_filter_drops_unclosed_block():
    visible, guard = _feed_in_chunks(["好的，开始。\n[TOOL_CALL: create_file: a.py]\nprint(1)"])
    assert visible == "好的，开始。\n"
    assert guard.dropped_chars > 0


def test_stream_filter_keeps_plain_text():
    text = "这是一段普通输出，包含 [方括号] 和 ```代码``` 但不是工具调用。"
    visible, guard = _feed_in_chunks([text])
    assert visible == text
    assert guard.dropped_chars == 0


def test_strip_tool_blocks_keeps_prose():
    cleaned = strip_tool_blocks(TEXT)
    assert "[TOOL_CALL:" not in cleaned
    assert "<html>" not in cleaned
    assert "收尾：已完成。" in cleaned


def test_strip_tool_results_roundtrip():
    raw = "正文\n" + wrap_tool_result("## run_python_file 执行结果\nexit=0")
    assert "exit=0" in raw
    cleaned = strip_tool_results(raw)
    assert "exit=0" not in cleaned
    assert "正文" in cleaned


def test_display_text_removes_every_protocol_trace():
    raw = (TEXT
           + wrap_tool_result("## run_python_file 执行结果\nexit=0")
           + "\n[RESULT]\n{\"status\": \"ok\", \"confidence\": 0.9}\n[/RESULT]")
    cleaned = display_text(raw)
    assert "[TOOL_CALL:" not in cleaned
    assert "<html>" not in cleaned
    assert "exit=0" not in cleaned
    assert "[RESULT]" not in cleaned
    assert "status" not in cleaned
    assert "先说明需求。" in cleaned
    assert "收尾：已完成。" in cleaned


def test_display_text_keeps_code_fences_written_by_model():
    """模型自己在正文里写的代码块要保留 —— 那是给人读的证据。"""
    raw = "测试报告：\n```\n[通过] 16/16\n```\n测试结论：通过"
    cleaned = display_text(raw)
    assert "```" in cleaned
    assert "16/16" in cleaned
    assert "测试结论：通过" in cleaned


def test_scan_tool_calls():
    found = scan_tool_calls(TEXT)
    assert found == [{"tool": "create_file", "params": "index.html"}]


def test_preview_is_truncated_and_marked():
    long_result = "y" * 5000
    preview, truncated = preview_of(long_result)
    assert truncated is True
    assert len(preview) <= 701        # 700 + 省略号
    assert preview.endswith("…")

    short_preview, short_truncated = preview_of("ok")
    assert short_preview == "ok"
    assert short_truncated is False


def test_summarize_args_never_leaks_file_body():
    """create_file 的参数就是整篇文件正文，不能拿它当"目标"上报。"""
    body = "<html>" + ("z" * 20000) + "</html>"
    target = summarize_args("create_file", {"filename": "index.html", "content": body})
    assert target == "index.html"
    assert "z" * 100 not in target


def test_summarize_args_falls_back_to_params_line():
    target = summarize_args("run_python_file", None, "代码.py\nprint(1)")
    assert target == "代码.py"
