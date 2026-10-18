"""独立审查员（升级清单 T2，2026-10-18）。

解决"主 Agent 自己审自己"：生成者与审查者共享同一份对话上下文，
"知道想写什么"就觉得"写对了"（计算器任务 19 轮的根源之一）。
审查员只看三件交付物 + T1 的契约记录，**刻意不给对话历史** ——
独立上下文是这道设计的全部意义。

判分两级（参照 MewHelp 第 7 章 quality_gate 的"阈值不过不放行"）：
  1. 机械契约检查（不调 LLM）：T1 的 validate_result 不过 → 直接打回；
  2. LLM 审查：查需求覆盖度、证据一致性、交付物之间是否自相矛盾。
任一级不过 → reject + 具体原因，反馈回主 Agent 退回重做。
冲突时以运行证据为准，不让模型自行选一个（ch7 的原话思想）。
"""
import json
import re

from ..utils.safe_print import safe_print

REVIEWER_SYSTEM = """你是独立的质量审查员，不参与开发，只对交付物做验收。
你只能看到：功能清单、代码、测试报告，以及各角色的自报契约（状态/证据/置信度）。
你看不到主Agent 和子Agent 的对话过程 —— 这是刻意设计：审查必须独立于执行。

请输出严格的 JSON（不要任何额外文字）：
{"verdict": "pass" 或 "reject", "findings": [{"type": "问题类型", "detail": "具体问题", "evidence": "依据（哪条需求/哪段输出）"}], "summary": "一句话结论"}

判定标准：
1. 代码是否覆盖功能清单的每一条需求（逐条对，缺哪条列哪条，引用需求编号）；
2. 测试报告是否有运行证据（退出码/用例数），结论与证据是否一致；
3. 交付物之间是否自相矛盾（如需求要 A、代码做 B）；
4. 报告文字与运行证据冲突时，以运行证据为准。

宁可严格不可放水：任一条需求无证据覆盖 → reject。"""


def mechanical_check() -> list[str]:
    """机械校验：读 T1 契约记录，不过就列出来。不调 LLM，零成本。"""
    from ..storage.state import load_state
    from .contract import AgentResult, Evidence, validate_result

    state = load_state()
    artifacts = {
        "功能清单": state.get("功能清单", ""),
        "代码": state.get("代码", ""),
        "测试报告": state.get("测试报告", ""),
    }
    problems: list[str] = []
    for role in ("产品经理", "程序员", "测试员"):
        rec = state.get(f"契约_{role}")
        if not rec:
            continue  # 该角色本轮没出过场，不苛责
        r = AgentResult(role=role, status=rec.get("status", "partial"),
                        confidence=rec.get("confidence", 0))
        r.evidence = [Evidence(**e) if isinstance(e, dict) else e
                      for e in rec.get("evidence", [])]
        problems.extend(f"[{role}] {p}"
                        for p in validate_result(r, role, artifacts))
    return problems


def _truncate(text: str, limit: int = 3000) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "\n…（过长已截断）"


def llm_review() -> dict:
    """LLM 审查：独立上下文，只看交付物。"""
    from ..llm import get_llm
    from ..storage.state import load_state

    state = load_state()
    功能清单 = state.get("功能清单", "")
    代码 = state.get("代码", "")
    测试报告 = state.get("测试报告", "")

    if not (功能清单.strip() or 代码.strip()):
        return {"verdict": "reject",
                "findings": [{"type": "empty", "detail": "没有任何交付物", "evidence": ""}],
                "summary": "无交付物可审"}

    contract_brief = []
    for role in ("产品经理", "程序员", "测试员"):
        rec = state.get(f"契约_{role}")
        if rec:
            contract_brief.append(
                f"- {role}: status={rec.get('status')}, "
                f"confidence={rec.get('confidence')}, "
                f"证据={[e.get('type') + ':' + e.get('ref') for e in rec.get('evidence', [])]}")

    prompt = (
        "## 功能清单\n" + _truncate(功能清单) + "\n\n"
        "## 代码\n" + _truncate(代码) + "\n\n"
        "## 测试报告\n" + _truncate(测试报告) + "\n\n"
        "## 各角色自报契约\n" + ("\n".join(contract_brief) or "（无）") + "\n\n"
        "请按判定标准审查，输出 JSON。"
    )
    try:
        llm = get_llm()
        resp = llm.chat(messages=[
            {"role": "system", "content": REVIEWER_SYSTEM},
            {"role": "user", "content": prompt},
        ], temperature=0.2)
        return _parse_verdict(resp.content or "")
    except Exception as e:
        safe_print(f"[审查员] LLM 审查失败: {e}")
        return {"verdict": "reject",
                "findings": [{"type": "review_error", "detail": str(e), "evidence": ""}],
                "summary": "审查调用失败，按不过处理"}


def _parse_verdict(text: str) -> dict:
    """从审查输出解析 JSON 结论；解析失败按不过处理（审查方失灵时不许放行）。"""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return {"verdict": "reject",
                "findings": [{"type": "unparsable", "detail": "审查输出不是 JSON", "evidence": text[:200]}],
                "summary": "审查输出无法解析，按不过处理"}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"verdict": "reject",
                "findings": [{"type": "unparsable", "detail": "审查输出 JSON 损坏", "evidence": text[:200]}],
                "summary": "审查输出无法解析，按不过处理"}
    verdict = "pass" if data.get("verdict") == "pass" else "reject"
    findings = data.get("findings") if isinstance(data.get("findings"), list) else []
    return {"verdict": verdict, "findings": findings,
            "summary": str(data.get("summary", ""))[:300]}


def review_task(use_llm: bool = True) -> dict:
    """完整审查：先机械（不过直接打回），再 LLM。"""
    problems = mechanical_check()
    if problems:
        safe_print(f"[审查员] 机械契约不过: {problems}")
        return {"verdict": "reject", "mode": "mechanical", "problems": problems,
                "findings": [], "summary": "；".join(problems[:3])}
    if not use_llm:
        return {"verdict": "pass", "mode": "mechanical",
                "problems": [], "findings": [], "summary": "机械校验通过"}
    result = llm_review()
    result["mode"] = "llm"
    result["problems"] = problems
    return result
