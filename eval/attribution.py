"""评测失败归因 + 回归对比（升级清单 T7）。

参照 MewHelp 第 7 章 quality_gate 的思路：评测不只给总分，还要回答
"这一轮为什么没过"——失败归因让跑批结果可行动；同口径回归对比让
"系统有没有在改进"有数据可讲（通过率/轮数/耗时三项）。
"""

def attribute(row: dict) -> str:
    """给单个任务的跑批结果归因（确定性规则，不调 LLM）。"""
    if row.get("error_seen") or not row.get("finished"):
        return "运行时错误/超时"
    if not row.get("task_done"):
        return "未完成（轮数耗尽或主Agent未收尾）"
    if row.get("test_status") != "通过":
        return "模型输出波动（测试未通过）"
    return "通过"


def _count(value: str) -> int:
    """'5/6' → 5；解析不了按 0。"""
    try:
        return int(str(value).split("/")[0])
    except (ValueError, IndexError):
        return 0


def compare(baseline: dict, candidate: dict) -> dict:
    """同口径对比两次跑批，返回各指标 delta 与总体判定。

    轮数/耗时下降是改进，通过率下降是回退——方向不同，判定也不同。
    """
    deltas = {}

    def _add(name, b, c, lower_is_better=False):
        delta = round(c - b, 2)
        if delta == 0:
            verdict = "same"
        elif (delta < 0) == lower_is_better:
            verdict = "improved"
        else:
            verdict = "regressed"
        deltas[name] = {"baseline": b, "candidate": c,
                        "delta": delta, "verdict": verdict}

    _add("task_completion", _count(baseline.get("task_completion", "0/0")),
         _count(candidate.get("task_completion", "0/0")))
    _add("test_pass", _count(baseline.get("test_pass_rate", "0/0")),
         _count(candidate.get("test_pass_rate", "0/0")))
    _add("avg_rounds", baseline.get("avg_rounds", 0), candidate.get("avg_rounds", 0),
         lower_is_better=True)
    _add("avg_elapsed_s", baseline.get("avg_elapsed_s", 0),
         candidate.get("avg_elapsed_s", 0), lower_is_better=True)

    verdicts = [d["verdict"] for d in deltas.values()]
    overall = ("regressed" if "regressed" in verdicts
               else "improved" if "improved" in verdicts else "same")
    return {"overall": overall, "deltas": deltas}


def attribution_summary(rows: list[dict]) -> dict:
    """把一批任务的归因聚合成计数，跑批报告直接可用。"""
    out: dict[str, int] = {}
    for r in rows:
        key = r.get("归因") or attribute(r)
        out[key] = out.get(key, 0) + 1
    return out
