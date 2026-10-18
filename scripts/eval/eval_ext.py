"""abundance 平台扩展评测：人工反馈消化（打回重做）与可控性（中途终止）。"""
import json
import os
import time
from datetime import datetime

import requests

BASE = "http://127.0.0.1:8000"

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(_HERE, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
OUT = os.path.join(RESULTS_DIR,
                   f"eval_ext_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

session = requests.Session()
session.trust_env = False

TASK = "写一个统计字符串中元音字母个数的函数并测试"


def collect(sid, t0, timeout_s=560, on_handoff=None):
    """收事件直到终态；on_handoff(to_agent) 返回 decision 字符串时执行该决策。"""
    cursor = 0
    final = ""
    result = {}
    decisions = []
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
                to_agent = ev.get("to_agent", "")
                decision = "approve"
                if on_handoff is not None:
                    decision = on_handoff(to_agent, decisions)
                    decisions.append(decision)
                session.post(f"{BASE}/sessions/{sid}/approval",
                             json={"decision": decision, "task": "请补充边界情况的处理说明，例如空字符串输入" if decision == "reject" else ""},
                             timeout=10)
            elif kind == "done":
                final = "done"
                result = ev.get("result", {}) or {}
            elif kind == "error":
                final = "error"
        resp.close()
        if final in ("done", "error"):
            break
        time.sleep(0.5)
    return final, result


def case_feedback_loop():
    """打回重做：第一次交接 reject（带反馈），之后放行 → 应能完成。"""
    r = session.post(f"{BASE}/sessions/start", json={"message": TASK}, timeout=30)
    sid = r.json()["session_id"]
    t0 = time.time()
    state = {"rejected": False}

    def on_handoff(to_agent, decisions):
        if not state["rejected"]:
            state["rejected"] = True
            return "reject"
        return "approve"

    final, result = collect(sid, t0, on_handoff=on_handoff)
    detail = session.get(f"{BASE}/sessions/{sid}", timeout=10).json()["data"]
    return {
        "case": "人工反馈消化（首次交接打回+反馈，后续放行）",
        "rejected_once": state["rejected"],
        "finished": final == "done" and bool(result.get("任务完成")),
        "test_status": result.get("测试状态", "未知"),
        "rounds": result.get("主Agent调度轮数", 0),
        "elapsed_s": round(time.time() - t0, 1),
        "session_status": detail.get("status"),
    }


def case_stop():
    """可控性：首次交接时终止 → 会话应干净停在已终止，不再产出。"""
    r = session.post(f"{BASE}/sessions/start", json={"message": TASK}, timeout=30)
    sid = r.json()["session_id"]
    t0 = time.time()
    state = {"stopped": False}

    def on_handoff(to_agent, decisions):
        if not state["stopped"]:
            state["stopped"] = True
            return "stop"
        return "approve"

    final, result = collect(sid, t0, timeout_s=180, on_handoff=on_handoff)
    time.sleep(3)
    detail = session.get(f"{BASE}/sessions/{sid}", timeout=10).json()["data"]
    status = detail.get("status", "")
    return {
        "case": "可控性（交接闸门处主动终止）",
        "stop_issued": state["stopped"],
        "terminal_clean": status in ("stopped", "终止", "cancelled") and final != "error",
        "session_status": status,
        "elapsed_s": round(time.time() - t0, 1),
    }


def main():
    out = {"cases": []}
    print("[扩展1] 人工反馈消化…", flush=True)
    c1 = case_feedback_loop()
    out["cases"].append(c1)
    print("    -> " + json.dumps(c1, ensure_ascii=False), flush=True)
    print("[扩展2] 中途终止…", flush=True)
    c2 = case_stop()
    out["cases"].append(c2)
    print("    -> " + json.dumps(c2, ensure_ascii=False), flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("EXT DONE", flush=True)


main()
