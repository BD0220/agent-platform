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
        prompt = (
            f"请分析以下用户需求，输出详细的功能清单：\n\n{user_request}\n\n"
            "输出格式要求：使用 Markdown（## 需求概述、## 功能清单 两节，"
            "功能用列表逐条给出，每条含验收标准）。不要输出 JSON。"
        )
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

    # 防退化：某轮输出残缺（只剩标记词/空壳）时，不覆盖状态里更完整的上一版代码
    if len(clean.strip()) >= 50:
        save_state({"代码": clean})
    else:
        safe_print(f"[程序员] 本轮无有效代码（{len(clean.strip())} 字），保留上一版")

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
            f"## 测试报告\n{测试报告}\n\n"
            f"## 当前代码\n{代码}\n\n"
            f"请结合测试报告原文和上述修复要求，输出修改后的完整代码。"
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

    # 防退化：修复轮同理，残缺输出不覆盖上一版
    if len(clean.strip()) >= 50:
        save_state({"代码": clean})
    else:
        safe_print(f"[程序员] 本轮无有效代码（{len(clean.strip())} 字），保留上一版")

    prog_ctx["last_code_length"] = len(clean)
    prog_ctx["fix_count"] = prog_ctx.get("fix_count", 0) + 1
    prog_ctx["call_count"] = prog_ctx.get("call_count", 0) + 1
    prog_ctx["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_context("agent_programmer", prog_ctx)
    return clean


def _judge_test_result(result: str) -> bool:
    """从测试员输出中判断测试是否通过。

    证据优先级（2026-10-13 修订，实测踩过坑）：
      1. 显式结论行 ``测试结论：通过/未通过`` —— 提示词规定的格式，最可信；
      2. **实际运行证据** —— 测试员把 run_python_file 的原始输出贴进报告的代码块，
         里面 ``[通过]``/``[失败]`` 标记是程序真实跑出来的结果；
      3. 正文关键词扫描（已剥离代码块）—— 兜底。

    为什么不能只信关键词扫全文（旧版的坑）：测试员偶尔不写结论行，
    甚至在报告开头写一句自相矛盾的 ``[测试未通过]``，而贴出的原始输出里
    明明是「汇总：全部通过（16/16）」。文字和运行证据打架时，旧版判了未通过，
    与事实相反 —— 现在运行证据优先。
    """
    import re
    text = result or ""
    upper = text.upper()

    # 1) 显式结论行 —— 只看这一行
    explicit = re.findall(
        r"测试结论\s*[:：]\s*\**\s*(通过|未通过|不通过|失败)", text)
    if explicit:
        return explicit[-1] == "通过"

    # 2) 运行证据：代码块里的 [通过]/[失败] 标记
    blocks_text = "\n".join(re.findall(r"```(.*?)```", text, flags=re.DOTALL))
    if blocks_text.strip():
        blocks_upper = blocks_text.upper()
        # 先剔除否定形式（0 FAIL / NO FAIL），再数失败标记
        cleaned = re.sub(r"(0|NO|ZERO)\s+FAIL", "", blocks_upper)
        fail_marks = len(re.findall(r"\[失败\]|\bFAIL\b", cleaned))
        pass_marks = len(re.findall(r"\[通过\]", blocks_text))
        if fail_marks > 0:
            return False
        if pass_marks > 0:
            return True

    # 3) 正文关键词（剥离代码块后的叙述部分）
    body = re.sub(r"```.*?```", "", text, flags=re.DOTALL).upper()
    fail_signals = ["FAIL", "FAILED", "ERROR", "TRACEBACK", "ASSERTIONERROR",
                    "测试失败", "存在失败", "未通过"]
    for sig in fail_signals:
        idx = body.find(sig)
        if idx != -1:
            before = body[max(0, idx - 12):idx]
            if re.search(r"(0\s*|NO\s*|无\s*|没有\s*|ZERO\s*)", before):
                continue
            return False

    pass_signals = ["ALL PASSED", "ALL TESTS PASSED", "所有测试用例通过",
                    "全部通过", "测试通过", "无问题", "0 FAIL"]
    for sig in pass_signals:
        if sig in body:
            return True

    # 4) 兜底：宁可漏报通过，也不把失败的代码说成通过
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
    """主Agent 通过 dispatch_agent 工具调用此函数，将任务分派给指定子Agent。

    这里是"子Agent 真正开工"的地方，因此额外承担两件事：
    1. **协作式取消的检查点** —— 用户点了终止后，下一个交接点立刻退出，
       不会只停在界面上而让模型继续烧 token；
    2. **把工作过程广播到会话事件流** —— 前端群聊里的气泡就是这里发出去的。
    """
    from datetime import datetime
    from ..runtime import bus
    from ..runtime.control import get_control

    # 检查点：进入子Agent 之前确认用户没有要求终止
    get_control().check_stop()

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

    bus.emit(bus.KIND_AGENT, agent=agent_name, phase="working",
             content=f"收到任务，开始处理：\n\n{task[:400]}")

    if agent_name == "产品经理":
        output = product_manager_work(task)
        detail = load_state().get("功能清单", "")
    elif agent_name == "程序员":
        programmer_work(master_task=task)
        detail = load_state().get("代码", "")
        output = f"[程序员已生成代码]\n\n{detail}"
    elif agent_name == "测试员":
        report, passed = tester_work(master_task=task)
        status = "测试通过" if passed else "测试未通过"
        detail = report
        bus.emit(bus.KIND_AGENT, agent=agent_name, phase="done",
                 content=report, status=status)
        return f"[{status}]\n\n{report}"
    else:
        return f"未知Agent: {agent_name}"

    bus.emit(bus.KIND_AGENT, agent=agent_name, phase="done", content=detail)
    return output
