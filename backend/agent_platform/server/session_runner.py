"""会话执行器 —— 在后台线程里跑一个会话任务，全过程接到事件总线上。

跟原来的 ``/run/stream`` 有什么不同？
    原来那个是"前端每秒轮询一份被截断的 state 摘要"，本质上是把进度条
    拼凑出来的 —— 前端不知道谁说了什么，也无法介入。
    这里改成 **事件驱动**：agent 干活时往总线上发消息，SSE 端点只负责
    按游标把新消息推给浏览器。前端因此能拿到真实的群聊时间线，
    也能通过审批接口反向控制 Agent 的推进节奏。

线程与生命周期：
    每条会话一个后台线程，线程内把 ``session_id`` 塞进 ContextVar，
    之后整条调用链（dispatch / sub_agents / master）都能用
    ``bus.current_session_id()`` 拿到它，不需要层层传参。
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..runtime import bus, session_store
from ..runtime.bus import KIND_AGENT, KIND_DELIVERABLE, KIND_DONE, KIND_ERROR, KIND_SYSTEM
from ..runtime.control import TaskStopped, get_control
from ..storage.state import cleanup_state, extract_task_name, set_current_task
from ..utils.safe_print import safe_print

# 每个会话占一个 worker。闸门（等用户确认）会**阻塞**这个 worker，
# 所以池子太小的话，几个停在确认页的会话就能把整个服务顶死：
# 4 个 worker + 每次最多挂 30 分钟 = 第 5 个会话永远排队。
# 默认放到 8（`docs/改动记录.md` 里早就写了 4→8，代码一直没跟上），
# 可用 AGENT_SESSION_WORKERS 覆盖。
SESSION_WORKERS = int(os.environ.get("AGENT_SESSION_WORKERS", "8"))
_executor = ThreadPoolExecutor(max_workers=SESSION_WORKERS, thread_name_prefix="session-run")
_running: set[str] = set()
_lock = threading.Lock()

# 交付物的固定文件名 —— 与 conclusion_node 落盘时用的名字保持一致
DELIVERABLE_NAMES = ("需求分析.md", "代码.py", "测试报告.md")


def is_running(session_id: str) -> bool:
    with _lock:
        return session_id in _running


def start(session_id: str, user_request: str, username: str = "") -> bool:
    """启动一个会话任务。已在运行则拒绝，避免同一会话被重复触发。

    ``mark_running(True)`` 刻意放在这里**同步**执行，而不是等 ``_run`` 里再置位。
    因为任务提交到真正开跑之间可能隔着好几秒（线程池排队、BM25 初始化、
    第一次 LLM 调用），而 SSE 端点的判断逻辑是"没有新事件 且 未标记 running"
    就认为会话已静止、主动断开连接。如果等 ``_run`` 才置位，这个空窗里
    前端刚连上的流会被立刻关掉，用户看到的现象就是"发了消息，什么都没发生"。
    """
    with _lock:
        if session_id in _running:
            return False
        _running.add(session_id)

    bus.get_bus().mark_running(session_id, True)
    session_store.update(session_id, status=session_store.STATUS_RUNNING)
    _executor.submit(_run, session_id, user_request, username)
    return True


def stop(session_id: str) -> bool:
    """请求终止：置停止标志 + 唤醒正在等待确认的闸门。

    真正的中断发生在 Agent 的下一个检查点（交接处 / 每轮循环开头），
    所以这是"协作式取消"而不是强杀线程 —— 状态文件与已产出的交付物
    都能保持完整。
    """
    if not is_running(session_id):
        return False
    get_control().request_stop(session_id)
    bus.get_bus().publish(session_id, KIND_SYSTEM,
                          content="已收到终止指令，正在停止…", stopping=True)
    return True


# ---------------------------------------------------------------------------
# 内部执行
# ---------------------------------------------------------------------------
def _reset_agent_contexts() -> None:
    """清掉上一条会话残留的 Agent 上下文。

    ``agent_master`` / ``agent_pm`` 这几个 key 是**全局的**，不带会话隔离。
    不清的话新会话会读到上一个会话的 dispatch_log（表现为交接链错乱，
    例如"测试员 → 产品经理"）和统计计数。
    """
    try:
        from ..memory.short_term import delete_context
    except Exception:
        return
    for key in ("agent_master", "agent_pm", "agent_programmer", "agent_tester"):
        try:
            delete_context(key)
        except Exception:
            pass


def _run(session_id: str, user_request: str, username: str) -> None:
    ctl = get_control()
    b = bus.get_bus()

    ctl.clear(session_id)
    _reset_agent_contexts()
    set_current_task(session_id)
    # running 标记已经在 start() 里同步置位了，这里不重复置位 ——
    # 置位越早，"前端刚连上流就被判定为空闲而断开"的窗口越小。
    b.publish(session_id, KIND_SYSTEM, content=f"任务已开始\n\n**需求**：{user_request}")

    _register_in_db(session_id, user_request, username)

    final_status = session_store.STATUS_DONE
    try:
        from ..main import run_task
        result = run_task(user_request)

        # 顺序很关键：先把状态落成"已完成"、把运行标记摘掉，再发结束事件。
        # 反过来的话，前端一收到 done 就去查详情，拿到的还是旧的 running，
        # 会看到"任务结束了但状态还在跑"这种自相矛盾的画面。
        b.mark_running(session_id, False)
        session_store.update(session_id, status=session_store.STATUS_DONE)
        _emit_deliverables(session_id, result)
        _finish_db(session_id, result)

    except TaskStopped:
        final_status = session_store.STATUS_STOPPED
        b.mark_running(session_id, False)
        session_store.update(session_id, status=final_status)
        b.publish(session_id, KIND_SYSTEM,
                  content="任务已终止。已完成部分的产出仍保留在交付目录。")
        b.publish(session_id, KIND_DONE, stopped=True, result={})

    except Exception as e:
        final_status = session_store.STATUS_ERROR
        b.mark_running(session_id, False)
        session_store.update(session_id, status=final_status)
        safe_print(f"[会话执行器] {session_id} 执行失败: {e}")
        b.publish(session_id, KIND_ERROR, message=str(e))
        try:
            from ..storage.database import db_fail_task
            db_fail_task(session_id, str(e))
        except Exception:
            pass

    finally:
        # 幂等兜底：异常路径下也可能没走到上面那几行
        b.mark_running(session_id, False)
        session_store.update(session_id, status=final_status)
        with _lock:
            _running.discard(session_id)
        # 放在最后：cleanup_state 会清掉 ContextVar，之后 bus.emit 就找不到会话了
        cleanup_state()


def _retry(fn, attempts: int = 3, delay: float = 0.4):
    """重试一个 sqlite 写入操作，专治瞬时 "database is locked"。

     进程被强杀时会在磁盘上留下未合并的 WAL，下一个连接首次访问需要先做恢复，
     这个瞬间的排他锁可能来不及等到就报错。任务表登记属于 best-effort ——
     失败了不该影响正在跑的会话，但多试两次通常就过去了。
    """
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if "locked" not in str(e).lower() or i == attempts - 1:
                break
            time.sleep(delay * (i + 1))
    raise last


def _register_in_db(session_id: str, user_request: str, username: str) -> None:
    """把会话同步进任务表，让"统计"页面也能反映新会话。

    复用 session_id 作为任务主键，两套记录天然一一对应，不需要额外映射。
    """
    try:
        from ..storage.database import db_create_task, init_db
        _retry(init_db)
        _retry(lambda: db_create_task(
            session_id, user_request, extract_task_name(user_request), username))
    except Exception as e:
        safe_print(f"[会话执行器] 任务表登记失败: {e}")


def _finish_db(session_id: str, result: dict) -> None:
    try:
        from ..storage.database import db_complete_task
        _retry(lambda: db_complete_task(session_id, result))
    except Exception as e:
        safe_print(f"[会话执行器] 任务表更新失败: {e}")


def _read_deliverable(delivery_dir: str, name: str, limit: int = 20000) -> str:
    if not delivery_dir:
        return ""
    path = os.path.join(delivery_dir, name)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _emit_deliverables(session_id: str, result: dict) -> None:
    """把最终产出作为一条交付物事件发出（三个文件捆一条，避免刷屏）。

    产出内容**优先从交付目录读盘**而不是从 result 取：任务跑完后
    ``conclusion_node`` 已经把短期状态清掉了，result 里的产出字段在某些
    路径下会是空的；而磁盘上的文件是实打实写下去的，最可靠。
    """
    delivery_dir = result.get("交付目录", "") or ""

    files = []
    for name in DELIVERABLE_NAMES:
        content = _read_deliverable(delivery_dir, name)
        if content.strip():
            files.append({"name": name, "content": content})

    if not files:
        # 目录读不到时退回 result 字段（兼容未落盘的执行路径）
        for name, key in (("需求分析.md", "功能清单"),
                          ("代码.py", "代码"),
                          ("测试报告.md", "测试报告")):
            content = str(result.get(key, "") or "")
            if content.strip():
                files.append({"name": name, "content": content})

    b = bus.get_bus()
    b.publish(session_id, KIND_DELIVERABLE, files=files, delivery_dir=delivery_dir)

    # 收尾总结：结构化 + 说实话 —— 测试没通过就明说，不要伪装成"全部完成"
    turns = result.get("主Agent调度轮数", 0)
    test_state = result.get("测试状态") or "未知"
    passed = test_state == "通过"
    # 模型调用失败也会走到收尾：那不是"测试没通过"，更不是"调度上限"，得如实说。
    error = str(result.get("错误") or "").strip()
    if error:
        head = "⚠️ 任务结束（模型调用失败）"
    else:
        head = "✅ 任务完成" if passed else "⚠️ 任务结束（测试未通过）"
    lines = [
        f"**{head}**",
        f"- 调度轮数：{turns} 轮",
    ]
    plan = result.get("任务规划") or {}
    plan_text = plan.get("raw", "") if isinstance(plan, dict) else str(plan)
    if plan_text:
        lines.append(f"- 计划：{plan_text[:60]}{'…' if len(plan_text) > 60 else ''}")
    if error:
        lines.append(f"- 失败原因：{error[:300]}")
        lines.append("- 处理建议：确认 `.env` 里的 `DEEPSEEK_API_KEY` / `ABUNDANCE_BASE_URL` "
                     "有效、能访问模型服务，然后重试。")
    lines.extend([
        f"- 测试结果：{test_state}"
        + ("" if passed or error else "（已达调度上限，未能在本轮内全部修复）"),
        f"- 交付物：{('、'.join(f['name'] for f in files)) if files else '（无有效产出）'}",
        f"- 位置：`{delivery_dir}`" if delivery_dir else "- 位置：默认交付目录",
    ])
    evidence = result.get("证据清单") or []
    if evidence:
        lines.append("- 证据链：")
        lines.extend(f"  - {e}" for e in evidence[:6])
    reviews = result.get("审查次数", 0)
    if reviews:
        lines.append(f"- 独立审查：{reviews} 次")
    if not passed and not error:
        lines.append("\n可以在对话框里继续提修改要求（例如\"把 X 问题修掉\"），"
                     "主Agent 会安排程序员修复后重新测试。")

    b.publish(session_id, KIND_AGENT, agent="主Agent", phase="done",
              content="\n".join(lines))

    b.publish(session_id, KIND_DONE,
              result={
                  "task": result.get("task", ""),
                  "task_name": result.get("task_name", ""),
                  "测试状态": result.get("测试状态", ""),
                  "主Agent调度轮数": result.get("主Agent调度轮数", 0),
                  "任务完成": result.get("任务完成", False),
                  "交付目录": delivery_dir,
              },
              stopped=False)
