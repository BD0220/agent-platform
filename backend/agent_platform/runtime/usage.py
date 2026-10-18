"""LLM 调用计量（升级清单 T6，可观测性）。

进程内收集每次 LLM 调用的 model / token / 耗时，供 ``/sessions/{id}/trace``
和排障使用。刻意只进内存不落盘：提示词与输出可能含敏感内容，
计量信息（模型/token 数/耗时）足够排障且不碰内容。
"""
import threading
from collections import deque
from datetime import datetime

_lock = threading.Lock()
_calls: deque = deque(maxlen=500)


def record_llm_call(model: str, usage: dict, latency_ms: int, tag: str = "") -> None:
    """记录一次调用。usage 为 OpenAI 兼容格式的 dict（缺字段按 0）。"""
    with _lock:
        _calls.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": model,
            "tag": tag,
            "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
            "completion_tokens": usage.get("completion_tokens", 0) or 0,
            "total_tokens": usage.get("total_tokens", 0) or 0,
            "latency_ms": latency_ms,
        })


def snapshot(limit: int = 100) -> list[dict]:
    with _lock:
        return list(_calls)[-limit:]


def summary() -> dict:
    with _lock:
        calls = list(_calls)
    if not calls:
        return {"calls": 0, "total_tokens": 0, "avg_latency_ms": 0, "max_latency_ms": 0}
    by_tag: dict[str, dict] = {}
    for c in calls:
        bucket = by_tag.setdefault(c["tag"] or "?", {"calls": 0, "tokens": 0})
        bucket["calls"] += 1
        bucket["tokens"] += c["total_tokens"]
    return {
        "calls": len(calls),
        "total_tokens": sum(c["total_tokens"] for c in calls),
        "avg_latency_ms": round(sum(c["latency_ms"] for c in calls) / len(calls)),
        "max_latency_ms": max(c["latency_ms"] for c in calls),
        "by_tag": by_tag,
    }
