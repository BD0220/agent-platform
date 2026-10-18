"""上下文预算组装器（升级清单 T3，2026-10-18）。

问题：``max_turns`` 硬截断 = 长任务失忆；有修复环节的任务常在复测前
被轮数上限掐死，最后带着上一轮的"未通过"收场（2026-10-13 复盘的结构性问题）。

做法：每次调 LLM 前检查消息总字符数，超预算时把**早期轮次**压成一行摘要，
首条用户需求、system 提示、工具调用与工具结果**原样保留**（那是证据，不压），
最近 N 条原样保留。确定性压缩，不烧额外 LLM 调用。

字符数是 token 的粗代理（中文 1 字 ≈ 1-1.5 token，代码/JSON 密度更高），
默认 24000 字 ≈ 6-8k token，留足 system + 工具 schema 的空间。
"""
KEEP_RECENT = 8
DEFAULT_BUDGET_CHARS = 48000   # DeepSeek 128K 上下文，24000 字预算太保守：
                               # 995 行 HTML 的 read_file 结果直接撑爆，
                               # 兜底截断把代码砍成头尾各 400 字 → 测试员蒙眼测试
TOOL_RESULT_HARD_LIMIT = 3000  # 二轮兜底：单条工具结果截断上限


def _total_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages)


def assemble_messages(messages: list[dict],
                      budget_chars: int = DEFAULT_BUDGET_CHARS,
                      keep_recent: int = KEEP_RECENT) -> tuple[list[dict], dict]:
    """超预算时压缩早期轮次。返回 (messages, stats)，stats 供可观测使用。

    注意：必须返回**新列表、新字典**，绝不原地改调用方的消息 ——
    上一版直接改 dict，工具结果被截断后污染了调用方循环里复用的
    消息列表，后面每一轮看到的都是残缺证据（实测踩过）。
    """
    if not messages:
        return messages, {"compressed": False, "before": 0, "after": 0}

    total = _total_chars(messages)
    if total <= budget_chars:
        return messages, {"compressed": False, "before": total, "after": total}

    head, tail = messages[:-keep_recent], messages[-keep_recent:]
    collapsed = 0
    kept: list[dict] = []
    for m in head:
        role = m.get("role", "")
        content = str(m.get("content", ""))
        # 系统提示 / 工具调用 / 工具结果 = 证据与指令，不压
        if role in ("system", "tool") or m.get("tool_calls"):
            kept.append(m)
            continue
        gist = content[:60].replace("\n", " ")
        kept.append({"role": role, "content": f"[早期轮次摘要] {gist}…"})
        collapsed += 1

    out = [dict(m) for m in kept + tail]   # 拷贝，杜绝原地污染
    after = _total_chars(out)

    # 二轮兜底：仍有巨型工具结果撑爆预算时，逐条截断（保留头尾，标记截断）
    if after > budget_chars:
        for m in out:
            if m.get("role") != "tool":
                continue
            c = str(m.get("content", ""))
            if len(c) > TOOL_RESULT_HARD_LIMIT:
                m["content"] = (c[:TOOL_RESULT_HARD_LIMIT // 2]
                                + f"\n…（中间省略 {len(c) - TOOL_RESULT_HARD_LIMIT} 字）…\n"
                                + c[-TOOL_RESULT_HARD_LIMIT // 2:])
        after = _total_chars(out)

    stats = {"compressed": True, "before": total, "after": after,
             "collapsed": collapsed, "budget": budget_chars}
    return out, stats
