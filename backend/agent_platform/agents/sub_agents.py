"""子Agent 工作函数：产品经理、程序员、测试员。"""
import hashlib
import os
from datetime import datetime

from ..memory.short_term import load_context, save_context
from ..storage.state import load_state, save_state
from ..utils.safe_print import safe_print
from .call_agent import call_agent
from .contract import ENVELOPE_INSTRUCTION, build_result, parse_envelope, validate_result
from .extractor import extract_code


def product_manager_work(user_request: str = None, progress=None):
    pm_ctx = load_context("agent_pm")
    if user_request is None:
        state = load_state()
        user_request = state.get("user_request", "")

    from ..llm import get_llm
    from .definitions import PM_OUTPUT_SCHEMA, SYSTEM_PROMPTS

    llm = get_llm()
    system_prompt = SYSTEM_PROMPTS.get("产品经理", "你是一位产品经理。")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请分析以下用户需求，输出结构化 JSON：\n\n{user_request}"},
    ]

    # 结构化 JSON 是一次性长生成（实测 60~90 秒）。这里把流式片段只用来
    # 数"已经写了多少字"，让界面上的状态条真的在动 —— 不用来看内容本身。
    def _count(text: str) -> None:
        if progress is not None:
            progress.add(len(text or ""))

    structured = llm.chat_structured(messages, output_schema=PM_OUTPUT_SCHEMA,
                                     temperature=0.3, on_delta=_count)

    if "_parse_error" in structured:
        safe_print(f"[产品经理] JSON 解析失败，回退到自由文本模式: {structured.get('_parse_error')}")
        prompt = (
            f"请分析以下用户需求，输出详细的功能清单：\n\n{user_request}\n\n"
            "输出格式要求：使用 Markdown（## 需求概述、## 功能清单 两节，"
            "功能用列表逐条给出，每条含验收标准）。不要输出 JSON。"
            + ENVELOPE_INSTRUCTION
        )
        result = call_agent("产品经理", prompt)
        envelope = parse_envelope(result, "产品经理")
        problems = validate_result(envelope, "产品经理", {"功能清单": result})
        if problems:
            safe_print(f"[产品经理] 契约校验未过: {problems}")
    else:
        result = _format_requirements_as_markdown(structured)
        envelope = build_result(
            "产品经理", artifacts={"功能清单": result},
            summary=f"{len(structured.get('requirements', []))} 条需求",
            confidence=0.85)
        problems = validate_result(envelope, "产品经理", {"功能清单": result})
        safe_print(f"[产品经理] 结构化输出：{len(structured.get('requirements', []))} 条需求")

    save_state({"user_request": user_request, "功能清单": result,
                "契约_产品经理": envelope.to_dict()})

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

    # 工具轮次是最贵的开销之一（每轮一次完整模型往返）。这里把"怎么写"
    # 讲清楚，把往返压到最少：一次写全 → 跑一次 → 报错才改。
    _ONESHOT = (
        "\n\n## 执行纪律（务必遵守）\n"
        "1. 用 **一次** create_file 写出**完整**可运行的代码文件，不要分多次写、不要先写一半再补。\n"
        "2. 写完后用 run_python_file 运行**一次**，确认能跑通并输出结果。\n"
        "3. 只有运行确实报错时才修改并重跑；能跑通就立刻给出最终答复，不要反复改写。"
    )

    if master_task:
        prompt = (
            f"## 主Agent分派的任务\n{master_task}\n\n"
            f"请根据上述任务要求输出完整可运行的 Python 代码实现。"
            f"如果任务要求中包含'运行'、'执行'、'跑一下'等要求，"
            f"你必须在用 create_file 创建代码文件后，立即调用 [TOOL_CALL: run_python_file: 代码.py] 来运行代码。"
            + _ONESHOT
        )
    else:
        prompt = (
            f"## 用户原始需求\n{user_request}\n\n"
            f"## 功能清单\n{功能清单}\n\n"
            f"请输出完整可运行的 Python 代码实现。"
            f"如果用户原始需求中包含'运行'、'执行'、'跑一下'等要求，"
            f"你必须在用 create_file 创建代码文件后，立即调用 [TOOL_CALL: run_python_file: 代码.py] 来运行代码。"
            + _ONESHOT
        )
    raw_result = call_agent("程序员", prompt)

    clean = extract_code(raw_result)
    envelope = parse_envelope(raw_result, "程序员")
    problems = validate_result(envelope, "程序员", {"代码": clean})

    # 契约不过 → 带具体原因 corrective retry 一次（T1：平台说清哪不过，模型才有机会改对）
    if problems:
        safe_print(f"[程序员] 契约校验未过: {problems}，带原因重试一次")
        retry_prompt = (
            prompt
            + "\n\n【平台校验未通过，原因】" + "；".join(problems)
            + "\n请修正后重新输出。务必在最终答复末尾追加合规的 [RESULT] 结果信封；"
              "如果你是用 create_file 把代码写入 代码.py 的，请在 evidence 里补一条 "
              "type=file、ref=代码.py 的证据。"
        )
        raw_result = call_agent("程序员", retry_prompt)
        clean = extract_code(raw_result)
        envelope = parse_envelope(raw_result, "程序员")
        problems = validate_result(envelope, "程序员", {"代码": clean})

    if clean.strip() and not problems:
        save_state({"代码": clean, "契约_程序员": envelope.to_dict()})
    else:
        # 防退化（2026-10-13 实测坑）：残缺输出不覆盖状态里更完整的上一版代码
        safe_print(f"[程序员] 最终校验未过（{problems or ['空输出']}），保留上一版代码")
        save_state({"契约_程序员": envelope.to_dict()})

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
    envelope = parse_envelope(raw_result, "程序员")
    problems = validate_result(envelope, "程序员", {"代码": clean})

    if problems:
        safe_print(f"[程序员-修复轮] 契约校验未过: {problems}，带原因重试一次")
        retry_prompt = (
            prompt
            + "\n\n【平台校验未通过，原因】" + "；".join(problems)
            + "\n请修正后重新输出完整代码，并在答复末尾追加合规的 [RESULT] 结果信封。"
        )
        raw_result = call_agent("程序员", retry_prompt)
        clean = extract_code(raw_result)
        envelope = parse_envelope(raw_result, "程序员")
        problems = validate_result(envelope, "程序员", {"代码": clean})

    if clean.strip() and not problems:
        save_state({"代码": clean, "契约_程序员": envelope.to_dict()})
    else:
        # 防退化：修复轮同理，残缺输出不覆盖上一版
        safe_print(f"[程序员-修复轮] 最终校验未过（{problems or ['空输出']}），保留上一版代码")
        save_state({"契约_程序员": envelope.to_dict()})

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

    # ── 同一版代码不重复测 ────────────────────────────────
    # 痛点（2026-10-18）："每次都测老多遍，一个非常简单的任务他都要做十分钟"。
    # 主Agent 的审查会反复把测试员叫回来，但代码根本没变 —— 每叫一次就是
    # 一次完整 LLM 轮次 + 一遍真实执行。这里按代码指纹缓存上一次结论：代码
    # 没变就直接复用，只有代码变了才真的重测。
    code_hash = hashlib.sha1((代码 or "").encode("utf-8")).hexdigest()[:16]
    if (test_ctx.get("last_code_hash") == code_hash
            and test_ctx.get("last_report")
            and test_ctx.get("last_result") == "通过"):
        cached = test_ctx["last_report"]
        safe_print(f"[测试员] 代码未变化（{code_hash}），复用上次测试结论")
        save_state({"测试报告": cached, "测试状态": "通过"})
        bus_note = "（代码与上次一致，复用上次通过的测试结论，未重复执行）"
        return f"{cached}\n\n{bus_note}", True

    # ── 测试轮次上限：防止在"未通过 → 修 → 再测"里无限烧时间 ──
    max_rounds = int(os.environ.get("AGENT_MAX_TEST_ROUNDS", "2"))
    if test_ctx.get("test_count", 0) >= max_rounds:
        last = test_ctx.get("last_report") or "（没有可复用的测试报告）"
        safe_print(f"[测试员] 已达测试轮次上限 {max_rounds}，不再重复测试")
        return (f"[已达测试轮次上限 {max_rounds} 次，不再重复测试]\n\n"
                f"上一版代码的测试结论如下，请据此收尾：\n\n{last}"), False

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

    envelope = parse_envelope(result, "测试员")
    problems = validate_result(envelope, "测试员", {"测试报告": result})
    if problems:
        safe_print(f"[测试员] 契约校验未过: {problems}")

    passed = _judge_test_result(result)
    save_state({"测试报告": result, "测试状态": "通过" if passed else "未通过",
                "契约_测试员": envelope.to_dict()})

    test_ctx["last_result"] = "通过" if passed else "未通过"
    test_ctx["last_code_hash"] = code_hash
    test_ctx["last_report"] = result
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
    from ..runtime.progress import track

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
        # 进度心跳：产品经理的结构化调用是最长的一段静默，必须让界面有反应
        with track(agent_name, "分析需求、生成需求清单") as prog:
            output = product_manager_work(task, progress=prog)
        detail = load_state().get("功能清单", "")
        display = detail
    elif agent_name == "程序员":
        with track(agent_name, "写代码并自测"):
            programmer_work(master_task=task)
        detail = load_state().get("代码", "")
        output = f"[程序员已生成代码]\n\n{detail}"
        display = _code_bubble(detail)
    elif agent_name == "测试员":
        with track(agent_name, "运行测试并判定结果"):
            report, passed = tester_work(master_task=task)
        status = "测试通过" if passed else "测试未通过"
        bus.emit(bus.KIND_AGENT, agent=agent_name, phase="done",
                 content=_display(report), status=status)
        return f"[{status}]\n\n{report}"
    else:
        return f"未知Agent: {agent_name}"

    bus.emit(bus.KIND_AGENT, agent=agent_name, phase="done", content=display)
    return output


def _display(text: str) -> str:
    """展示版本：去掉工具调用块、工具结果区块和 [RESULT] 信封。

    逻辑层（extract_code / 契约校验 / 测试结论判定）继续用原始文本，
    只有发给前端的内容才清洗 —— 两者不能互相污染。
    """
    from ..tools import display_text
    cleaned = display_text(text)
    return cleaned or "（本轮没有文字结论，详见上方工具卡片与交付物）"


def _code_bubble(code: str) -> str:
    """程序员的产出改成"一行摘要 + 折叠代码块"。

    老版本把整篇代码原样塞进气泡，29KB 的 HTML 直接把时间线糊死；
    包进围栏后前端会渲染成带复制按钮、默认折叠的代码块。
    """
    text = (code or "").strip()
    if not text:
        return "（本轮没有产出代码，详见上方工具卡片）"
    lines = text.splitlines()
    return (f"**代码已写入交付目录** · {len(lines)} 行\n\n"
            f"```\n{text}\n```")
