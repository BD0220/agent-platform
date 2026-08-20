"""子Agent 工作函数：产品经理、程序员、测试员。"""
from datetime import datetime

from ..storage.state import save_state, load_state
from ..memory.short_term import save_context, load_context
from ..utils.safe_print import safe_print
from .call_agent import call_agent
from .extractor import extract_code


def product_manager_work(user_request: str = None):
    pm_ctx = load_context("agent_pm")
    if user_request is None:
        state = load_state()
        user_request = state.get("user_request", "")

    from ..llm import get_llm
    from .definitions import SYSTEM_PROMPTS, PM_OUTPUT_SCHEMA

    llm = get_llm()
    system_prompt = SYSTEM_PROMPTS.get("产品经理", "你是一位产品经理。")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请分析以下用户需求，输出结构化 JSON：\n\n{user_request}"},
    ]

    structured = llm.chat_structured(messages, output_schema=PM_OUTPUT_SCHEMA, temperature=0.3)

    if "_parse_error" in structured:
        safe_print(f"[产品经理] JSON 解析失败，回退到自由文本模式: {structured.get('_parse_error')}")
        prompt = f"请分析以下用户需求，输出详细的功能清单：\n\n{user_request}"
        result = call_agent("产品经理", prompt)
    else:
        result = _format_requirements_as_markdown(structured)
        safe_print(f"[产品经理] 结构化输出：{len(structured.get('requirements', []))} 条需求")

    save_state({"user_request": user_request, "功能清单": result})

    pm_ctx["last_task"] = user_request[:200]
    pm_ctx["last_output"] = result[:500]
    pm_ctx["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_context("agent_pm", pm_ctx)
    return result


def _format_requirements_as_markdown(data: dict) -> str:
    """将结构化 JSON 需求转为 Markdown 格式，兼容下游 Agent。"""
    lines = [f"## 需求概述\n{data.get('task_overview', '')}\n"]
    reqs = data.get("requirements", [])
    if reqs:
        lines.append("## 功能清单")
        for r in reqs:
            rid = r.get("id", "-")
            title = r.get("title", "")
            desc = r.get("description", "")
            priority = r.get("priority", "P2")
            lines.append(f"- **[{rid}] {title}** ({priority}): {desc}")
            ac = r.get("acceptance_criteria", [])
            if ac:
                for a in ac:
                    lines.append(f"  - 验收: {a}")
    return "\n".join(lines)


def programmer_work(master_task: str = None):
    prog_ctx = load_context("agent_programmer")
    state = load_state()
    功能清单 = state.get("功能清单", "无功能清单")
    user_request = state.get("user_request", "")

    if master_task:
        prompt = (
            f"## 主Agent分派的任务\n{master_task}\n\n"
            f"请根据上述任务要求输出完整可运行的 Python 代码实现。"
            f"如果任务要求中包含'运行'、'执行'、'跑一下'等要求，"
            f"你必须在用 create_file 创建代码文件后，立即调用 [TOOL_CALL: run_python_file: 代码.py] 来运行代码。"
        )
    else:
        prompt = (
            f"## 用户原始需求\n{user_request}\n\n"
            f"## 功能清单\n{功能清单}\n\n"
            f"请输出完整可运行的 Python 代码实现。"
            f"如果用户原始需求中包含'运行'、'执行'、'跑一下'等要求，"
            f"你必须在用 create_file 创建代码文件后，立即调用 [TOOL_CALL: run_python_file: 代码.py] 来运行代码。"
        )
    raw_result = call_agent("程序员", prompt)

    clean = extract_code(raw_result)
    if clean != raw_result:
        safe_print("[程序员] 已自动清洗输出，剥离非代码内容")

    save_state({"代码": clean})

    prog_ctx["last_code_length"] = len(clean)
    prog_ctx["call_count"] = prog_ctx.get("call_count", 0) + 1
    prog_ctx["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_context("agent_programmer", prog_ctx)
    return clean


def programmer_fix_work(master_task: str = None):
    prog_ctx = load_context("agent_programmer")
    state = load_state()
    代码 = state.get("代码", "")
    测试报告 = state.get("测试报告", "")
    功能清单 = state.get("功能清单", "")
    user_request = state.get("user_request", "")

    if master_task:
        prompt = (
            f"## 主Agent分派的修复任务\n{master_task}\n\n"
            f"## 当前代码\n{代码}\n\n请根据上述修复要求输出修改后的完整代码。"
        )
    else:
        prompt = (
            f"以下代码的测试报告指出了问题，请根据测试报告修复代码中的所有问题。\n\n"
            f"## 用户原始需求\n{user_request}\n\n## 原始功能清单（参考）\n{功能清单}\n\n"
            f"## 当前代码\n{代码}\n\n## 测试报告\n{测试报告}\n\n"
            f"请输出修复后的完整代码。"
            f"如果用户原始需求中包含'运行'、'执行'、'跑一下'等要求，"
            f"你必须在用 create_file 创建代码文件后，立即调用 [TOOL_CALL: run_python_file: 代码.py] 来运行代码。"
        )
    raw_result = call_agent("程序员", prompt)

    clean = extract_code(raw_result)
    if clean != raw_result:
        safe_print("[程序员] 已自动清洗输出，剥离非代码内容")

    save_state({"代码": clean})

    prog_ctx["last_code_length"] = len(clean)
    prog_ctx["fix_count"] = prog_ctx.get("fix_count", 0) + 1
    prog_ctx["call_count"] = prog_ctx.get("call_count", 0) + 1
    prog_ctx["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_context("agent_programmer", prog_ctx)
    return clean


def _judge_test_result(result: str) -> bool:
    """从测试员输出中智能判断测试是否通过。

    判定优先级：
    1. 运行结果中出现 PASS / 全部通过 / 所有测试用例通过 → 通过
    2. 出现 FAIL / 失败 / Error / Traceback → 未通过
    3. 明确出现"测试通过"或"无问题" → 通过
    4. 兜底 → 未通过（避免漏报）
    """
    import re
    text = result.upper()

    # 明确失败信号（优先级最高）
    fail_signals = ["FAIL", "FAILED", "ERROR", "TRACEBACK", "ASSERTIONERROR", "测试失败", "存在失败", "未通过"]
    # 但要排除"0 failed"这种否定形式
    for sig in fail_signals:
        if sig in text:
            # 检查是否被否定（如 "0 failed"、"没有失败"）
            idx = text.find(sig)
            before = text[max(0, idx - 12):idx]
            if re.search(r"(0\s*|NO\s*|无\s*|没有\s*|ZERO\s*)", before):
                continue
            return False

    # 明确通过信号
    pass_signals = ["ALL PASSED", "ALL TESTS PASSED", "所有测试用例通过", "全部通过", "测试通过", "无问题", "0 FAIL"]
    for sig in pass_signals:
        if sig in text:
            return True

    # 检查 PASS 计数：有 PASS 行且无 FAIL 行
    pass_count = len(re.findall(r"\bPASS\b", text))
    fail_count = len(re.findall(r"\bFAIL\b", text))
    if pass_count > 0 and fail_count == 0:
        return True

    # 兜底
    return False


def tester_work(master_task: str = None):
    test_ctx = load_context("agent_tester")
    state = load_state()
    功能清单 = state.get("功能清单", "无功能清单")
    代码 = state.get("代码", "无代码")
    user_request = state.get("user_request", "")

    if master_task:
        prompt = (
            f"## 主Agent分派的测试任务\n{master_task}\n\n"
            f"## 参考：功能清单\n{功能清单}\n\n## 当前代码\n{代码}\n\n"
            f"请根据上述任务要求审查代码。如果需要运行验证，使用 "
            f"[TOOL_CALL: run_python_file: 代码.py] 来实际运行，根据运行结果编写测试报告。"
        )
    else:
        prompt = (
            f"请审查以下代码是否完整实现了功能清单中的所有要求，并查找潜在问题。\n\n"
            f"## 用户原始需求\n{user_request}\n\n## 功能清单\n{功能清单}\n\n## 代码\n{代码}"
            f"\n\n如果用户原始需求中包含'运行'、'执行'、'跑一下'等要求，"
            f"你必须调用 [TOOL_CALL: run_python_file: 代码.py] 来实际运行代码，"
            f"根据运行结果来编写测试报告。"
        )
    result = call_agent("测试员", prompt)

    passed = _judge_test_result(result)
    save_state({"测试报告": result, "测试状态": "通过" if passed else "未通过"})

    test_ctx["last_result"] = "通过" if passed else "未通过"
    test_ctx["test_count"] = test_ctx.get("test_count", 0) + 1
    test_ctx["pass_count"] = test_ctx.get("pass_count", 0) + (1 if passed else 0)
    test_ctx["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_context("agent_tester", test_ctx)

    safe_print(f"[测试员] 测试结果: {'[通过]' if passed else '[未通过] 需要修复'}")
    return result, passed


def dispatch_sub_agent(agent_name: str, task: str) -> str:
    """主Agent 通过 dispatch_agent 工具调用此函数，将任务分派给指定子Agent。"""
    from datetime import datetime
    safe_print(f"\n[主Agent调度] 分派任务给 {agent_name}，任务: {task[:100]}...")

    master_ctx = load_context("agent_master")
    dispatch_log = master_ctx.get("dispatch_log", [])
    dispatch_log.append({
        "agent": agent_name, "task": task[:200],
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    master_ctx["dispatch_log"] = dispatch_log[-20:]
    master_ctx["dispatch_count"] = master_ctx.get("dispatch_count", 0) + 1
    save_context("agent_master", master_ctx)

    if agent_name == "产品经理":
        return product_manager_work(task)
    elif agent_name == "程序员":
        programmer_work(master_task=task)
        return f"[程序员已生成代码]\n\n{load_state().get('代码', '')}"
    elif agent_name == "测试员":
        report, passed = tester_work(master_task=task)
        status = "测试通过" if passed else "测试未通过"
        return f"[{status}]\n\n{report}"

    return f"未知Agent: {agent_name}"
