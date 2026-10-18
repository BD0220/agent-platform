"""T2 独立审查员 + T3 上下文预算的单元测试（不调 LLM）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from agent_platform.agents.context import assemble_messages  # noqa: E402
from agent_platform.agents.reviewer import _parse_verdict, mechanical_check  # noqa: E402


def test_verdict_parse_pass():
    v = _parse_verdict('{"verdict": "pass", "findings": [], "summary": "OK"}')
    assert v["verdict"] == "pass"
    print("✓ test_verdict_parse_pass")


def test_verdict_parse_unparsable_passes():
    """审查输出无法解析时放行（审查方失灵不许把用户卡在反复打回里）。"""
    v = _parse_verdict("我觉得挺好的，没有 JSON")
    assert v["verdict"] == "pass"
    v2 = _parse_verdict('{"verdict": "pass", broken')
    assert v2["verdict"] == "pass"
    print("✓ test_verdict_parse_unparsable_passes")


def test_verdict_parse_missing_verdict_field():
    v = _parse_verdict('{"findings": []}')
    assert v["verdict"] == "reject"
    print("✓ test_verdict_parse_missing_verdict_field")


def test_context_under_budget_untouched():
    msgs = [{"role": "system", "content": "s" * 100},
            {"role": "user", "content": "u" * 100}]
    out, stats = assemble_messages(msgs, budget_chars=10000)
    assert out == msgs and not stats["compressed"]
    print("✓ test_context_under_budget_untouched")


def test_context_over_budget_collapses_early_keeps_recent():
    msgs = [{"role": "system", "content": "系统提示"}] \
        + [{"role": "user", "content": f"第{i}轮讨论内容" * 30} for i in range(20)]
    out, stats = assemble_messages(msgs, budget_chars=2000, keep_recent=4)
    assert stats["compressed"] and stats["collapsed"] > 0
    assert stats["after"] < stats["before"]
    # 系统提示与最近 4 条必须原样保留
    assert out[0]["content"] == "系统提示"
    assert out[-4:] == msgs[-4:]
    print("✓ test_context_over_budget_collapses_early_keeps_recent")


def test_context_keeps_tool_results_verbatim():
    """工具结果是证据，不压。"""
    big_tool = {"role": "tool", "content": "运行输出" * 200, "name": "run_python_file"}
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "u" * 500}] + [big_tool] \
        + [{"role": "user", "content": f"轮次{i}" + "x" * 300} for i in range(10)]
    out, stats = assemble_messages(msgs, budget_chars=1500, keep_recent=2)
    assert any(m.get("role") == "tool" and m["content"] == big_tool["content"]
               for m in out), "工具结果必须原样保留"
    print("✓ test_context_keeps_tool_results_verbatim")


def test_mechanical_check_no_state_passes():
    """无会话状态时（CLI 场景）机械检查不误伤。"""
    problems = mechanical_check()
    assert problems == []
    print("✓ test_mechanical_check_no_state_passes")


def test_simple_task_skips_review():
    """简单任务直接放行，不进 LLM 审查（2026-10-18："写个 hello 页面被打回两次"）。"""
    from agent_platform.workflow.nodes import review_node

    os.environ.pop("AGENT_REVIEW", None)
    out = review_node({"user_request": "写个前端页面就写个hello", "review_count": 0})
    assert out["review_verdict"] == "pass"
    assert out.get("review_skipped") is True
    print("✓ test_simple_task_skips_review")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== ALL REVIEWER/CONTEXT TESTS PASSED ===")
