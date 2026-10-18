"""T6 计量 + T7 归因/回归的单元测试（不调 LLM、不起服务）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "eval"))

from agent_platform.runtime.usage import record_llm_call, snapshot, summary  # noqa: E402
from attribution import attribute, attribution_summary, compare  # noqa: E402


def test_usage_record_and_summary():
    record_llm_call("deepseek-flash", {"prompt_tokens": 100, "completion_tokens": 20,
                                       "total_tokens": 120}, 850, tag="chat")
    record_llm_call("deepseek-flash", {"prompt_tokens": 50, "completion_tokens": 10,
                                       "total_tokens": 60}, 400, tag="chat_collect")
    s = summary()
    assert s["calls"] >= 2
    assert s["total_tokens"] >= 180
    assert s["max_latency_ms"] >= 850
    assert "chat" in s["by_tag"]
    assert len(snapshot(10)) >= 2
    print("✓ test_usage_record_and_summary")


def test_usage_empty_summary():
    # summary 结构在无调用时也要健全（不能除零）
    s = summary()
    assert s["calls"] >= 0 and "total_tokens" in s
    print("✓ test_usage_empty_summary")


def test_attribute_rules():
    assert attribute({"finished": True, "task_done": True, "test_status": "通过"}) == "通过"
    assert "模型输出波动" in attribute(
        {"finished": True, "task_done": True, "test_status": "未通过"})
    assert "未完成" in attribute(
        {"finished": True, "task_done": False, "test_status": "未知"})
    assert "运行时错误" in attribute({"finished": False, "task_done": False})
    assert "运行时错误" in attribute(
        {"finished": True, "task_done": True, "error_seen": True})
    print("✓ test_attribute_rules")


def test_attribution_summary_counts():
    rows = [
        {"finished": True, "task_done": True, "test_status": "通过"},
        {"finished": True, "task_done": True, "test_status": "未通过"},
        {"finished": True, "task_done": True, "test_status": "未通过"},
        {"finished": False},
    ]
    out = attribution_summary(rows)
    assert out["通过"] == 1
    assert out["模型输出波动（测试未通过）"] == 2
    assert out["运行时错误/超时"] == 1
    print("✓ test_attribution_summary_counts")


def test_compare_directions():
    """通过率降=回退；轮数/耗时降=改进——方向不同判定不同。"""
    baseline = {"task_completion": "6/6", "test_pass_rate": "6/6",
                "avg_rounds": 7.8, "avg_elapsed_s": 120}
    worse = {"task_completion": "6/6", "test_pass_rate": "5/6",
             "avg_rounds": 9.0, "avg_elapsed_s": 130}
    r = compare(baseline, worse)
    assert r["overall"] == "regressed"
    assert r["deltas"]["test_pass"]["verdict"] == "regressed"
    assert r["deltas"]["avg_rounds"]["verdict"] == "regressed"
    better = {"task_completion": "6/6", "test_pass_rate": "6/6",
              "avg_rounds": 6.0, "avg_elapsed_s": 100}
    r2 = compare(baseline, better)
    assert r2["overall"] == "improved"
    assert r2["deltas"]["avg_rounds"]["verdict"] == "improved"
    r3 = compare(baseline, dict(baseline))
    assert r3["overall"] == "same"
    print("✓ test_compare_directions")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== ALL UPGRADE TESTS PASSED ===")
