"""T1 结构化输出契约的单元测试（不调 LLM，纯规则）。

覆盖重点：2026-10-13 实测坑的回归——退化输出（"[程序员已生成代码]"）
必须判不过，不能落盘覆盖真代码；测试员声称测过但无运行证据必须被抓出
（计算器任务"文字骗过判定"的机制防线）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from agent_platform.agents.contract import (  # noqa: E402
    AgentResult, Evidence, build_result, parse_envelope, validate_result,
)

_LONG = "需求内容详述。" * 20  # 140+ 字，过 min_len


def _wrap(payload: str) -> str:
    return f"前面是说明文字\n[RESULT]\n{payload}\n[/RESULT]\n后面没有了"


def test_parse_ok():
    text = _wrap('{"status":"ok","summary":"写了计算器","artifacts":{"代码":"见上"},'
                 '"evidence":[{"type":"run","ref":"exit=0","detail":"16/16 通过"}],'
                 '"confidence":0.9}')
    r = parse_envelope(text, "程序员")
    assert r.ok and r.status == "ok" and r.confidence == 0.9
    assert len(r.evidence) == 1 and r.evidence[0].type == "run"
    assert r.evidence[0].ref == "exit=0"
    print("✓ test_parse_ok")


def test_missing_envelope():
    r = parse_envelope("[程序员已生成代码]", "程序员")
    assert not r.ok
    assert r.parse_error == "missing_envelope"
    print("✓ test_missing_envelope")


def test_invalid_json_and_status():
    r = parse_envelope(_wrap("{not json"), "程序员")
    assert r.parse_error.startswith("invalid_json")
    r2 = parse_envelope(_wrap('{"status":"done"}'), "程序员")
    assert r2.parse_error.startswith("invalid_status")
    r3 = parse_envelope("", "程序员")
    assert r3.parse_error == "empty_output"
    print("✓ test_invalid_json_and_status")


def test_degenerate_output_not_ok():
    """10-13 实测坑回归：退化输出必须判不过，不能覆盖真代码。"""
    r = parse_envelope("[程序员已生成代码]", "程序员")
    problems = validate_result(r, "程序员", {"代码": "[程序员已生成代码]"})
    assert problems, "退化输出必须被契约拦下"
    print("✓ test_degenerate_output_not_ok")


def test_tester_requires_run_evidence():
    """声称测过但无运行证据 → 不过（计算器任务'文字骗过判定'的机制防线）。"""
    text = _wrap('{"status":"ok","summary":"测了","artifacts":{"测试报告":"全部通过"},'
                 '"evidence":[],"confidence":0.9}')
    r = parse_envelope(text, "测试员")
    problems = validate_result(r, "测试员", {"测试报告": "全部通过 16/16"})
    assert any("运行证据" in p for p in problems), problems
    print("✓ test_tester_requires_run_evidence")


def test_tester_with_run_evidence_passes():
    text = _wrap('{"status":"ok","summary":"测了","artifacts":{"测试报告":"全部通过"},'
                 '"evidence":[{"type":"run","ref":"exit=0","detail":"16/16"}],'
                 '"confidence":0.9}')
    r = parse_envelope(text, "测试员")
    report = "测试报告：对照功能清单逐条验证，全部通过（16/16）。" + _LONG
    assert validate_result(r, "测试员", {"测试报告": report}) == []
    print("✓ test_tester_with_run_evidence_passes")


def test_programmer_file_evidence_exempts_length():
    """代码只经 create_file 落盘、最终答复是纯说明时，file 证据替代正文长度检查。"""
    text = _wrap('{"status":"ok","summary":"已写入 代码.py","artifacts":{"代码":"见上"},'
                 '"evidence":[{"type":"file","ref":"代码.py"}],"confidence":0.8}')
    r = parse_envelope(text, "程序员")
    assert validate_result(r, "程序员", {"代码": "见上"}) == []
    print("✓ test_programmer_file_evidence_exempts_length")


def test_programmer_without_evidence_flagged():
    text = _wrap('{"status":"ok","summary":"写好了","artifacts":{"代码":"见上"},'
                 '"evidence":[],"confidence":0.9}')
    r = parse_envelope(text, "程序员")
    problems = validate_result(r, "程序员", {"代码": "x = 1\n" * 40})
    assert any("证据" in p for p in problems), problems
    print("✓ test_programmer_without_evidence_flagged")


def test_low_confidence_flagged():
    text = _wrap('{"status":"ok","summary":"x","artifacts":{"代码":"见上"},'
                 '"evidence":[{"type":"file","ref":"代码.py"}],"confidence":0.1}')
    r = parse_envelope(text, "程序员")
    problems = validate_result(r, "程序员", {"代码": "x = 1\n" * 40})
    assert any("置信度" in p for p in problems), problems
    print("✓ test_low_confidence_flagged")


def test_partial_status_not_ok():
    text = _wrap('{"status":"partial","summary":"只做了一半","artifacts":{},'
                 '"evidence":[],"confidence":0.5}')
    r = parse_envelope(text, "程序员")
    assert not r.ok
    problems = validate_result(r, "程序员", {"代码": ""})
    assert any("partial" in p for p in problems), problems
    print("✓ test_partial_status_not_ok")


def test_build_result_roundtrip():
    r = build_result("产品经理", artifacts={"功能清单": "## 需求概述\n" + _LONG},
                     summary="3 条需求", confidence=0.85)
    d = r.to_dict()
    assert d["status"] == "ok" and d["role"] == "产品经理" and d["confidence"] == 0.85
    assert validate_result(r, "产品经理", {"功能清单": "## 需求概述\n" + _LONG}) == []
    print("✓ test_build_result_roundtrip")


def test_envelope_instruction_in_prompts():
    """程序员/测试员的提示词必须带上信封说明（平台只认信封）。"""
    from agent_platform.agents.definitions import SYSTEM_PROMPTS
    assert "[RESULT]" in SYSTEM_PROMPTS["程序员"]
    assert "[RESULT]" in SYSTEM_PROMPTS["测试员"]
    print("✓ test_envelope_instruction_in_prompts")


def test_to_dict_truncates_artifacts():
    r = build_result("程序员", artifacts={"代码": "y" * 500}, summary="s")
    d = r.to_dict()
    assert len(d["artifacts"]["代码"]) < 300, "状态里的产出必须截断，防止状态文件膨胀"
    print("✓ test_to_dict_truncates_artifacts")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== ALL CONTRACT TESTS PASSED ===")
