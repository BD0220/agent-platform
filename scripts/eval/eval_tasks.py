"""abundance 平台评测器：N 个真实编程任务跑批，采集完成率/测试结果/轮数/耗时。

结果写入 _platform_eval_result.json + 可读摘要 _platform_eval.txt。
"""
import json
import os
import sys
import time
from datetime import datetime

import requests

BASE = "http://127.0.0.1:8000"
_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(_HERE, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
_STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_JSON = os.path.join(RESULTS_DIR, f"eval_{_STAMP}.json")
OUT_TXT = os.path.join(RESULTS_DIR, f"eval_{_STAMP}.txt")
sys.path.insert(0, _HERE)
from attribution import attribute, attribution_summary, compare  # noqa: E402

os.environ["NO_PROXY"] = "127.0.0.1,localhost"

TASKS = [
    "写一个判断回文字符串的Python函数并运行",
    "写一个冒泡排序函数，对列表 [3,1,4,1,5,9,2,6] 排序并打印结果",
    "写一个计算斐波那契数列第 n 项的函数并测试 n=10",
    "写一个统计一段英文中单词个数的函数并运行验证",
    "写一个求两个整数最大公约数的函数（辗转相除法）并测试",
    "写一个生成九九乘法表并保存到文本文件的脚本",
]

session = requests.Session()
session.trust_env = False


def run_task(task, timeout_s=560):
    r = session.post(f"{BASE}/sessions/start", json={"message": task}, timeout=30)
    if r.status_code != 200:
        return {"task": task, "error": f"start failed {r.status_code}"}
    sid = r.json()["session_id"]
    cursor = 0
    handoffs = 0
    final_kind = ""
    result = {}
    error_seen = False
    t0 = time.time()

    while time.time() - t0 < timeout_s:
        try:
            resp = session.get(f"{BASE}/sessions/{sid}/events",
                               params={"cursor": cursor}, stream=True, timeout=(5, 60))
        except Exception:
            time.sleep(2)
            continue
        cur_kind = ""
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or raw.startswith(":"):
                continue
            if raw.startswith("event:"):
                cur_kind = raw.split(":", 1)[1].strip()
                continue
            if not raw.startswith("data:"):
                continue
            if cur_kind == "end":
                break
            try:
                ev = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(ev.get("seq"), int):
                cursor = ev["seq"] + 1
            kind = ev.get("kind", "")
            if kind == "handoff":
                handoffs += 1
                session.post(f"{BASE}/sessions/{sid}/approval",
                             json={"decision": "approve", "task": ""}, timeout=10)
            elif kind == "done":
                final_kind = "done"
                result = ev.get("result", {}) or {}
            elif kind == "error":
                final_kind = "error"
                error_seen = True
        resp.close()
        if final_kind in ("done", "error"):
            break
        time.sleep(0.5)

    row = {
        "task": task,
        "session": sid,
        "finished": final_kind == "done",
        "task_done": bool(result.get("任务完成")),
        "test_status": result.get("测试状态", "未知"),
        "rounds": result.get("主Agent调度轮数", 0),
        "handoffs": handoffs,
        "elapsed_s": round(time.time() - t0, 1),
        "error_seen": error_seen,
    }
    row["归因"] = attribute(row)
    return row


def _find_previous(current_path: str) -> dict | None:
    """找 results/ 里上一次的跑批结果（排除当前文件），用于回归对比。"""
    try:
        files = sorted(f for f in os.listdir(RESULTS_DIR) if f.endswith(".json"))
    except OSError:
        return None
    files = [f for f in files
             if os.path.abspath(os.path.join(RESULTS_DIR, f)) != os.path.abspath(current_path)]
    if not files:
        return None
    try:
        with open(os.path.join(RESULTS_DIR, files[-1]), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def main():
    rows = []
    for i, task in enumerate(TASKS, 1):
        print(f"[{i}/{len(TASKS)}] {task}", flush=True)
        row = run_task(task)
        rows.append(row)
        print(f"    -> done={row.get('task_done')} test={row.get('test_status')} "
              f"rounds={row.get('rounds')} elapsed={row.get('elapsed_s')}s", flush=True)

    n = len(rows)
    finished = sum(1 for r in rows if r.get("finished") and r.get("task_done"))
    passed = sum(1 for r in rows if r.get("test_status") == "通过")
    avg_rounds = sum(r.get("rounds", 0) for r in rows) / max(n, 1)
    avg_elapsed = sum(r.get("elapsed_s", 0) for r in rows) / max(n, 1)
    summary = {
        "tasks_total": n,
        "task_completion": f"{finished}/{n}",
        "test_pass_rate": f"{passed}/{n}",
        "avg_rounds": round(avg_rounds, 1),
        "avg_elapsed_s": round(avg_elapsed, 1),
        "归因统计": attribution_summary(rows),
        "rows": rows,
    }
    # 回归对比：与上一次跑批同口径比较（通过率降=回退，轮数/耗时降=改进）
    previous = _find_previous(OUT_JSON)
    if previous:
        summary["regression"] = compare(previous, summary)
        print("REGRESSION: " + json.dumps(summary["regression"], ensure_ascii=False), flush=True)
    print("ATTRIBUTION: " + json.dumps(summary["归因统计"], ensure_ascii=False), flush=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2))
    print("SUMMARY: " + json.dumps({k: v for k, v in summary.items() if k != "rows"},
                                   ensure_ascii=False), flush=True)


main()
