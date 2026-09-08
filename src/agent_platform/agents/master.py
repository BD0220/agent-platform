"""主Agent 多轮调度循环。"""
import copy
import json
import re
from datetime import datetime

from ..llm import get_llm
from ..tools import get_tool_definitions, get_tool_schemas, parse_and_execute_tool_calls, execute_tool_structured
from ..storage.state import save_state, load_state
from ..memory.short_term import load_context, save_context
from ..utils.safe_print import safe_print
from .definitions import SYSTEM_PROMPTS, AGENT_META, AGENT_STEP_MAP


def _extract_plan_summary(assistant_msg: str) -> str:
    plan_match = re.search(
        r'##\s*执行计划\s*\n(.*?)(?=\n##\s|\n\[TOOL_CALL|\Z)',
        assistant_msg, re.DOTALL,
    )
    if plan_match:
        return plan_match.group(1).strip()
    lines = [l.strip() for l in assistant_msg.split("\n") if l.strip() and not l.startswith("[TOOL_CALL")]
    return "\n".join(lines[:8]) if lines else "主Agent 正在分析需求并制定执行计划..."


def master_agent_loop(user_request: str, memory_context: str = "") -> dict:
    """主Agent 多轮调度循环。通过 dispatch_agent 逐步调度子Agent 完成任务。"""
    master_ctx = load_context("agent_master")

    system_prompt = SYSTEM_PROMPTS.get("主Agent", "你是总调度。")
    system_prompt += (
        "\n\n## 执行计划要求"
        "\n在首次分派子Agent之前，你必须先输出一段 ## 执行计划，"
        "\n列出你要拆解成几个步骤、每个步骤分配给哪个子Agent、预期产出是什么。"
        "\n格式示例："
        "\n## 执行计划"
        "\n1. 产品经理：分析需求，输出功能清单"
        "\n2. 程序员：编写代码实现所有功能"
        "\n3. 测试员：运行测试并生成报告"

        "\n\n## 工具使用说明"
        "\n你可以使用 Function Calling 调用以下工具。"
        "\n备选方案：你也可以使用文本格式 [TOOL_CALL: 工具名: 参数] 来调用工具。"
    )

    tool_schemas = get_tool_schemas()
    tools_text = get_tool_definitions()
    if tools_text:
        lines = ["", "### 可使用文本格式调用（备选）"]
        for t in tools_text:
            lines.append(f"- **{t['name']}**: {t['description']}")
        system_prompt += "\n" + "\n".join(lines)

    user_content = (
        f"## 用户需求\n{user_request}\n\n"
        f"请先输出 ## 执行计划，然后通过 dispatch_agent 工具逐步调度子Agent 完成任务。"
        f"每个子Agent 完成后请审查其输出，达标则推进下一步，不达标则退回重做。"
        f"全部完成后输出 DONE。"
    )
    if memory_context:
        user_content = memory_context + "\n\n" + user_content

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    task_plan = {"summary": "", "steps": [], "current_step": 0}
    _agent_dispatch_count: dict[str, int] = {}
    _last_dispatched_agent: str = ""

    MAX_TURNS = 15
    all_outputs = []
    turn = 0

    for turn in range(1, MAX_TURNS + 1):
        safe_print(f"\n{'=' * 60}")
        safe_print(f"  [主Agent] 第 {turn} 轮对话")
        safe_print(f"{'=' * 60}")

        llm = get_llm()
        chat_result = llm.chat(messages=messages, tools=tool_schemas if tool_schemas else None)
        msg = chat_result
        assistant_msg = msg.content or ""
        all_outputs.append(assistant_msg)

        preview = assistant_msg[:300] + ("..." if len(assistant_msg) > 300 else "")
        safe_print(f"[主Agent] 输出预览:\n{preview}")

        if turn == 1 and not task_plan["summary"]:
            task_plan["summary"] = _extract_plan_summary(assistant_msg)
            safe_print(f"\n[主Agent规划] 摘要:\n{task_plan['summary'][:300]}")

        # 检测分派的 Agent
        dispatch_matches = []
        current_dispatched = set()

        if msg.tool_calls:
            for tc in msg.tool_calls:
                if tc["function"]["name"] == "dispatch_agent":
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        agent = args.get("agent_name", "").strip()
                    except (json.JSONDecodeError, TypeError):
                        agent = ""
                    if agent in AGENT_STEP_MAP:
                        dispatch_matches.append(agent)

        if not dispatch_matches:
            text_matches = re.findall(
                r'\[TOOL_CALL:\s*dispatch_agent\s*:\s*([^\]]*)\]', assistant_msg)
            for agent_name in text_matches:
                agent_name = agent_name.strip()
                if agent_name in AGENT_STEP_MAP:
                    dispatch_matches.append(agent_name)

        for agent_name in dispatch_matches:
            current_dispatched.add(agent_name)
            count = _agent_dispatch_count.get(agent_name, 0)
            step_info = AGENT_STEP_MAP[agent_name]

            if count > 0:
                status = "退回重做"
                safe_print(f"[主Agent规划] {agent_name} 被退回重做 (第{count + 1}次分派)")
                for s in reversed(task_plan["steps"]):
                    if s["agent"] == agent_name and s["status"] == "执行中":
                        s["status"] = "退回重做"; break
            else:
                status = "执行中"
                safe_print(f"[主Agent规划] {agent_name} 开始执行")

            task_plan["steps"].append({
                "name": step_info["name"], "agent": agent_name, "icon": step_info["icon"],
                "description": f"第{count + 1}次分派 {agent_name}", "status": status,
            })
            task_plan["current_step"] = len(task_plan["steps"])
            _agent_dispatch_count[agent_name] = count + 1

        if current_dispatched and _last_dispatched_agent and _last_dispatched_agent not in current_dispatched:
            for s in reversed(task_plan["steps"]):
                if s["agent"] == _last_dispatched_agent and s["status"] == "执行中":
                    s["status"] = "已完成"
                    safe_print(f"[主Agent规划] {_last_dispatched_agent} → 已完成"); break
        if current_dispatched:
            _last_dispatched_agent = next(iter(current_dispatched))

        save_state({"任务规划": copy.deepcopy(task_plan)})

        has_tool_call = bool(msg.tool_calls) or "[TOOL_CALL:" in assistant_msg

        if has_tool_call:
            safe_print("[主Agent] 检测到工具调用，开始执行...")

            if msg.tool_calls:
                assistant_dict = {"role": "assistant", "content": msg.content}
                assistant_dict["tool_calls"] = msg.tool_calls
                messages.append(assistant_dict)

                for tc in msg.tool_calls:
                    func_name = tc["function"]["name"]
                    try:
                        arguments = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    exec_result = execute_tool_structured(func_name, arguments)
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": exec_result,
                    })
                    safe_print(f"[主Agent] FC {func_name} → {exec_result[:150]}...")
            else:
                tool_results = parse_and_execute_tool_calls(assistant_msg)
                messages.append({"role": "assistant", "content": assistant_msg})
                results_text = "\n\n".join(
                    f"## {tr['tool']} 执行结果\n{tr['result']}" for tr in tool_results
                ) if tool_results else "工具调用未产生结果。"
                messages.append({"role": "user", "content": results_text})

            safe_print("[主Agent] 工具结果已反馈，继续下一轮对话")
        else:
            safe_print("[主Agent] 无工具调用，对话结束")
            for s in task_plan["steps"]:
                if s["status"] == "执行中":
                    s["status"] = "已完成"
            save_state({"任务规划": copy.deepcopy(task_plan)})
            break
    else:
        safe_print(f"[主Agent] 已达最大轮数 {MAX_TURNS}，强制结束")
        for s in task_plan["steps"]:
            if s["status"] == "执行中":
                s["status"] = "已完成"
        save_state({"任务规划": copy.deepcopy(task_plan)})

    master_output = "\n\n".join(all_outputs)

    master_ctx["last_task"] = user_request[:200]
    master_ctx["last_turns"] = turn
    master_ctx["last_completed"] = "DONE" in master_output.upper() or "任务完成" in master_output
    master_ctx["task_count"] = master_ctx.get("task_count", 0) + 1
    master_ctx["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_context("agent_master", master_ctx)

    return {
        "master_output": master_output,
        "turns": turn,
        "任务完成": "DONE" in master_output.upper() or "任务完成" in master_output,
        "任务规划": task_plan,
    }
