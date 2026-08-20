"""经验提取：从已完成任务中自动提炼成功/失败经验。"""
import hashlib
import json
import re
from datetime import datetime

from ..storage.database import db_save_memory, db_get_all_memories
from ..utils.safe_print import safe_print
from .long_term import _add_to_chroma

TYPE_KEYWORDS = {
    "计算器": ["计算器", "计算", "calculator", "加减乘除", "四则运算"],
    "GUI应用": ["GUI", "界面", "窗口", "图形", "tkinter", "pyqt", "ui", "按钮", "输入框"],
    "数据处理": ["数据", "csv", "excel", "json", "pandas", "分析", "统计", "图表", "可视化"],
    "爬虫": ["爬虫", "爬取", "抓取", "scrap", "网页", "requests", "beautifulsoup"],
    "API服务": ["api", "接口", "fastapi", "flask", "服务", "后端", "rest"],
    "自动化脚本": ["自动化", "脚本", "自动", "批处理", "定时", "监控", "日志"],
    "游戏": ["游戏", "game", "pygame", "猜", "棋", "贪吃蛇", "迷宫"],
    "工具": ["工具", "tool", "转换", "生成", "解析", "格式化"],
    "测试": ["测试", "test", "单元测试", "pytest", "unittest"],
}


def classify_task(user_request: str) -> list[str]:
    tags = []
    for category, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in user_request.lower():
                tags.append(category)
                break
    return tags if tags else ["通用"]


def _generate_id() -> str:
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = hashlib.md5(now.encode()).hexdigest()[:6]
    return f"exp_{now}_{suffix}"


def _extract_key_info(state: dict) -> str:
    parts = []
    requirement = state.get("功能清单", "")
    if requirement:
        parts.append(f"功能清单: {requirement[:500]}")
    test_report = state.get("测试报告", "")
    if test_report:
        parts.append(f"测试报告: {test_report[:500]}")
    parts.append(f"测试结果: {state.get('测试状态', '')}")
    plan = state.get("任务规划", {})
    if plan and plan.get("steps"):
        steps_summary = ", ".join(f"{s['name']}({s['status']})" for s in plan["steps"])
        parts.append(f"执行步骤: {steps_summary}")
    return "\n".join(parts)


def _llm_summarize(key_info: str, user_request: str, tags: list[str]) -> dict:
    try:
        from ..llm.factory import get_llm
        llm = get_llm()

        prompt = (
            "你是一个任务复盘专家。请根据以下已完成任务的信息，提炼经验教训。\n\n"
            f"## 用户原始需求\n{user_request}\n\n"
            f"## 任务执行信息\n{key_info}\n\n"
            "请用 JSON 格式回复（只输出 JSON，不要其他文字），包含以下字段：\n"
            '{\n  "successes": ["成功点1", "成功点2", ...],\n'
            '  "lessons": ["教训1", "教训2", ...],\n'
            '  "improvements": ["改进建议1", "改进建议2", ...],\n'
            '  "quality_score": 1-10 的整数,\n  "one_line_summary": "一句话总结"\n}\n'
        )

        result = llm.chat(temperature=0.5, messages=[
            {"role": "system", "content": "你是一个专业的任务复盘专家。只输出 JSON。"},
            {"role": "user", "content": prompt},
        ])

        result_text = result.content.strip()
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            parsed = json.loads(json_match.group())
            return {
                "successes": parsed.get("successes", []),
                "lessons": parsed.get("lessons", []),
                "improvements": parsed.get("improvements", []),
                "quality_score": parsed.get("quality_score", 5),
                "one_line_summary": parsed.get("one_line_summary", ""),
            }
    except Exception as e:
        safe_print(f"[记忆系统] LLM 总结失败，使用规则兜底: {e}")

    return {
        "successes": ["任务已按流程执行完成"],
        "lessons": [],
        "improvements": ["建议增加更多边界测试"],
        "quality_score": 7 if "测试通过" in key_info else 5,
        "one_line_summary": f"完成了任务: {user_request[:80]}",
    }


def extract_experience(user_request: str, state: dict) -> dict:
    tags = classify_task(user_request)
    key_info = _extract_key_info(state)
    safe_print(f"\n[记忆系统] ========== 正在沉淀经验 ==========")
    safe_print(f"[记忆系统] 任务类型: {tags}")
    summary = _llm_summarize(key_info, user_request, tags)

    experience = {
        "id": _generate_id(),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task_type": tags[0] if tags else "通用",
        "tags": tags,
        "task_description": user_request[:200],
        "successes": summary.get("successes", []),
        "lessons": summary.get("lessons", []),
        "improvements": summary.get("improvements", []),
        "quality_score": summary.get("quality_score", 5),
        "one_line_summary": summary.get("one_line_summary", ""),
    }
    safe_print(f"[记忆系统] 经验沉淀完成: {experience['one_line_summary'][:80]}")
    return experience


def save_memory(experience: dict):
    db_save_memory(experience)
    _add_to_chroma(experience)
    safe_print(f"[记忆系统] 经验已保存 (ID: {experience['id']})")


def auto_extract_and_save(user_request: str, state: dict):
    try:
        experience = extract_experience(user_request, state)
        save_memory(experience)
    except Exception as e:
        safe_print(f"[记忆系统] 自动沉淀失败: {e}")
