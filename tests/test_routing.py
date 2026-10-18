"""任务路由（是否跳过产品经理）测试。"""
import os
import sys

sys.path.insert(0, "backend")

from agent_platform.utils.routing import needs_product_manager  # noqa: E402


def _clear():
    os.environ.pop("AGENT_SKIP_PM", None)
    os.environ.pop("AGENT_SIMPLE_TASK_CHARS", None)


def test_simple_task_skips_pm():
    _clear()
    assert needs_product_manager("做个计算器") is False
    assert needs_product_manager("写一个判断回文的函数并运行") is False
    assert needs_product_manager("九九乘法表") is False


def test_doc_intent_keeps_pm():
    _clear()
    assert needs_product_manager("做一个需求文档") is True
    assert needs_product_manager("设计一个系统架构") is True
    assert needs_product_manager("写一份调研报告") is True


def test_long_request_keeps_pm():
    _clear()
    assert needs_product_manager("帮我写一个" + "很长的需求描述" * 10) is True


def test_empty_request_keeps_pm():
    _clear()
    assert needs_product_manager("   ") is True


def test_env_override():
    _clear()
    os.environ["AGENT_SKIP_PM"] = "1"
    assert needs_product_manager("做个需求文档系统") is False
    os.environ["AGENT_SKIP_PM"] = "0"
    assert needs_product_manager("做个计算器") is True
    _clear()
