"""子 Agent 输出契约（升级清单 T1，2026-10-18）。

背景（已实测踩坑）：程序员某轮输出退化为 "[程序员已生成代码]"，
收尾落盘把磁盘上 19KB 真代码覆盖成一行空壳。根因是子 Agent 之间传自由文本，
平台无法区分"做完了"和"说了句漂亮话"。

设计（参照 MewHelp 第 7 章 evidence_workflow 的证据契约思想）：
每个子 Agent 的最终输出 = 结果信封（status/summary/artifacts/evidence/confidence）
+ 可机器校验的证据槽。平台只认信封：解析失败或校验不过 → 不算完成，
 corrective retry 一次，仍不过则不落盘、不覆盖更完整的旧产出。

信封格式（模型在最终答复末尾追加，单独两行标记夹一行 JSON）：

    [RESULT]
    {"status": "ok", "summary": "...", "artifacts": {"代码": "见上"},
     "evidence": [{"type": "run", "ref": "exit=0", "detail": "16/16 通过"}],
     "confidence": 0.9, "notes": ""}
    [/RESULT]
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

VALID_STATUS = ("ok", "partial", "failed")
RESULT_OPEN = "[RESULT]"
RESULT_CLOSE = "[/RESULT]"

# 每个角色的机械校验规则（不调 LLM，纯规则——T2 审查员的判分依据在这里落地）
ROLE_RULES: dict[str, dict] = {
    "产品经理": {"artifact": "功能清单", "min_len": 60, "need_evidence": False, "need_run": False},
    "程序员":   {"artifact": "代码",     "min_len": 60, "need_evidence": True,  "need_run": False},
    "测试员":   {"artifact": "测试报告", "min_len": 60, "need_evidence": True,  "need_run": True},
}

# 注入三个角色 system prompt 的末尾（平台判定只认这个，模型不写 = 打回）
ENVELOPE_INSTRUCTION = """

【结果信封 —— 平台只认这个，格式不能改】
完成工作后，在最终答复的末尾追加一个结果信封（单独两行标记，中间一行 JSON）：
[RESULT]
{"status": "ok", "summary": "一句话说明你做了什么", "artifacts": {"<产出名>": "产出概要或'见上文'"}, "evidence": [{"type": "run|file|tool", "ref": "退出码/文件名/工具名", "detail": "关键结果"}], "confidence": 0.9, "notes": ""}
[/RESULT]
规则：status 只能是 ok/partial/failed；evidence 至少一条，**运行过代码就必须有
type=run 的证据并填退出码**；confidence 填 0-1 表示你对产物的把握。
没有信封或格式不对 = 平台判定你没完成，会被打回重做。"""


@dataclass
class Evidence:
    """一条可机器校验的证据。type: run/file/tool/memory。"""

    type: str = ""
    ref: str = ""      # "exit=0" / "代码.py" / 记忆 id
    detail: str = ""


@dataclass
class AgentResult:
    role: str = ""
    status: str = "partial"
    summary: str = ""
    artifacts: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""
    parse_error: str = ""
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and not self.parse_error

    def to_dict(self) -> dict:
        """落进会话状态的精简版（原文截断，避免状态文件被一篇代码撑爆）。"""
        return {
            "role": self.role,
            "status": self.status,
            "summary": self.summary,
            "artifacts": {
                k: (v[:200] + "…" if len(v) > 200 else v)
                for k, v in self.artifacts.items()
            },
            "evidence": [
                e if isinstance(e, dict) else {"type": e.type, "ref": e.ref, "detail": e.detail}
                for e in self.evidence
            ],
            "confidence": self.confidence,
            "notes": self.notes,
            "parse_error": self.parse_error,
        }


def parse_envelope(text: str, role: str = "") -> AgentResult:
    """从模型输出中解析结果信封。任何失败都不抛异常，降级为 partial + parse_error。"""
    result = AgentResult(role=role, raw=text or "")
    if not text or not text.strip():
        result.parse_error = "empty_output"
        return result

    block = _extract_block(text)
    if block is None:
        result.parse_error = "missing_envelope"
        return result
    try:
        data = json.loads(block)
    except json.JSONDecodeError as e:
        result.parse_error = f"invalid_json: {e.msg}"
        return result
    if not isinstance(data, dict):
        result.parse_error = "envelope_not_object"
        return result

    status = str(data.get("status", "")).strip()
    if status not in VALID_STATUS:
        result.parse_error = f"invalid_status: {status!r}"
        return result

    result.status = status
    result.summary = str(data.get("summary", ""))[:500]
    arts = data.get("artifacts")
    if isinstance(arts, dict):
        result.artifacts = {str(k): str(v) for k, v in arts.items()}
    ev = data.get("evidence")
    if isinstance(ev, list):
        for item in ev:
            if isinstance(item, dict):
                result.evidence.append(Evidence(
                    type=str(item.get("type", "")),
                    ref=str(item.get("ref", "")),
                    detail=str(item.get("detail", "")),
                ))
    try:
        result.confidence = max(0.0, min(1.0, float(data.get("confidence", 0) or 0)))
    except (TypeError, ValueError):
        result.confidence = 0.0
    result.notes = str(data.get("notes", ""))[:500]
    return result


def _extract_block(text: str) -> str | None:
    """优先取 [RESULT]...[RESULT/] 块；没有标记但整体是 JSON 时也接受。"""
    if RESULT_OPEN in text and RESULT_CLOSE in text:
        start = text.index(RESULT_OPEN) + len(RESULT_OPEN)
        end = text.index(RESULT_CLOSE, start)
        return text[start:end].strip()
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    return None


def validate_result(result: AgentResult, role: str,
                    actual_artifacts: dict | None = None) -> list[str]:
    """机械校验（不调 LLM）。返回问题列表，空列表 = 通过。

    ``actual_artifacts`` 是真实提取的产出内容（如 extract_code 的结果）——
    信封里的 artifacts 只是概要，长度检查要对真实内容做。
    """
    problems: list[str] = []
    if result.parse_error:
        problems.append(f"信封解析失败: {result.parse_error}")
        return problems

    rules = ROLE_RULES.get(role)
    if rules is None:
        return problems
    if result.status != "ok":
        problems.append(f"自报状态 {result.status}")
        return problems

    source = actual_artifacts if actual_artifacts else result.artifacts
    artifact = str(source.get(rules["artifact"], ""))
    if len(artifact.strip()) < rules["min_len"]:
        # 程序员可能只通过 create_file 落盘、最终答复是纯说明，
        # 此时信封里的 file/run 证据可以替代正文长度检查
        file_backed = role == "程序员" and any(
            e.type in ("file", "run") for e in result.evidence)
        if not file_backed:
            problems.append(
                f"{rules['artifact']} 缺失或过短（{len(artifact.strip())} < {rules['min_len']}）")

    if rules["need_evidence"] and not result.evidence:
        problems.append("未提供任何证据")
    if rules["need_run"]:
        if not any(e.type == "run" for e in result.evidence):
            problems.append("声称完成测试但无运行证据（evidence 需含 type=run 及退出码）")
    if result.confidence < 0.3:
        problems.append(f"置信度过低（{result.confidence}）")
    return problems


def build_result(role: str, status: str = "ok", artifacts: dict | None = None,
                 evidence: list | None = None, summary: str = "",
                 confidence: float = 0.8, notes: str = "") -> AgentResult:
    """程序侧直接构造信封（不走模型解析），如产品经理的结构化 JSON 路径。"""
    items = []
    for e in (evidence or []):
        items.append(Evidence(**e) if isinstance(e, dict) else e)
    return AgentResult(role=role, status=status, artifacts=artifacts or {},
                       evidence=items, summary=summary,
                       confidence=confidence, notes=notes)
