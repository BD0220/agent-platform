"""会话化协作 API —— 群聊式工作台的后端。

为什么单开一个 router 而不是往 ``api.py`` 里加？
    ``api.py`` 里的端点是一套"提交任务 → 轮询进度"的旧模型，继续往里塞
    会让它变成杂物间。这里用 ``APIRouter`` 独立承载新模型，
    最后在 ``api.py`` 里 ``include_router`` 两行接进去 ——
    原有端点一个字节都不用改，旧前端（Gradio 旧版 / 命令行）依然能用。

两套模型的区别：
    旧：POST 提交 → 后台跑 → 每秒轮询被截断的 state 摘要（前端无法介入）
    新：会话是主体，Agent 的过程通过 SSE 实时推送；用户可以在
        "任务交接"这个节点上按确认/打回/终止，反向控制 Agent 的推进。
"""
import json
import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..runtime import bus, session_store, workspace
from ..runtime.control import get_control
from ..runtime.bus import (
    KIND_AGENT, KIND_ERROR, KIND_USER, TERMINAL_KINDS,
)
from ..utils.safe_print import safe_print
from . import session_runner

router = APIRouter(tags=["session"])

VALID_AGENTS = ("主Agent", "产品经理", "程序员", "测试员")


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
def _current_user() -> str:
    """单机模式：登录体系已整体移除，所有请求都视为本机用户。

    保留这个函数而不是把用户名到处硬编码，是为了保留一个扩展点：
    哪天真要加回认证，只需要在这里做校验并返回真实用户名。
    """
    return "local"


# ---------------------------------------------------------------------------
# 工作空间
# ---------------------------------------------------------------------------
class WorkspaceRequest(BaseModel):
    name: str
    path: str


@router.get("/workspaces")
def api_list_workspaces():
    _current_user()
    return {"status": "success", "workspaces": workspace.list_workspaces()}


@router.post("/workspaces")
def api_add_workspace(req: WorkspaceRequest):
    _current_user()
    ok, msg, item = workspace.add_workspace(req.name, req.path)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg, "workspace": item}


@router.delete("/workspaces/{workspace_id}")
def api_delete_workspace(workspace_id: str):
    _current_user()
    ok, msg = workspace.remove_workspace(workspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    return {"status": "success", "message": msg}


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
class StartRequest(BaseModel):
    session_id: str = ""
    message: str
    workspace_id: str = ""
    title: str = ""


class ApprovalRequest(BaseModel):
    decision: str
    task: str = ""
    approval_id: str = ""


class MessageRequest(BaseModel):
    message: str
    agent: str = ""


@router.get("/sessions")
def api_list_sessions(
    workspace_id: str = Query("", description="按工作空间过滤"),
    limit: int = Query(60, ge=1, le=500),
):
    _current_user()
    items = session_store.list_sessions(limit=limit, workspace_id=workspace_id)
    b = bus.get_bus()
    for item in items:
        item["status_label"] = session_store.STATUS_LABEL.get(item.get("status", ""), "")
        # 用事件总线的标记而不是执行器的登记集合：前者在发出 done 事件之前
        # 就已经置为 False，所以前端收到 done 再回头查详情时，不会读到
        # "任务已结束但 running 还是 True"这种自相矛盾的中间态。
        item["running"] = b.is_running(item["id"])
    return {"status": "success", "count": len(items), "sessions": items}


@router.post("/sessions/start")
def api_start_session(req: StartRequest):
    """新建（或复用）会话并立刻开始执行任务。

    设计要求：不在"新建会话"时落盘空会话，而是等用户真正发出第一条需求
    才创建记录 —— 否则侧边栏会被一堆点开就没动的空会话塞满。
    """
    username = _current_user()
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="请输入任务需求")

    sid = (req.session_id or "").strip()
    if sid:
        meta = session_store.load(sid)
        if not meta:
            raise HTTPException(status_code=404, detail="会话不存在")
        if session_runner.is_running(sid):
            raise HTTPException(status_code=409, detail="该会话已有任务在执行中")
    else:
        from ..storage.state import extract_task_name
        ws_path, ws_name = workspace.resolve_workspace_dir(req.workspace_id)
        meta = session_store.create(
            title=req.title or extract_task_name(message),
            user_request=message,
            workspace_id=req.workspace_id or workspace.DEFAULT_ID,
            workspace_name=ws_name,
            workspace_path=ws_path,
        )
        sid = meta["id"]

    bus.get_bus().publish(sid, KIND_USER, content=message, agent=username)
    if not session_runner.start(sid, message, username):
        raise HTTPException(status_code=409, detail="该会话已有任务在执行中")

    return {
        "status": "success",
        "session_id": sid,
        "workspace_path": meta.get("workspace_path", ""),
        "workspace_name": meta.get("workspace_name", ""),
    }


@router.get("/sessions/{session_id}")
def api_get_session(session_id: str):
    """会话详情：元信息 + 全部历史事件（用于打开历史会话时回放）。"""
    _current_user()
    meta = session_store.load(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="会话不存在")
    events = session_store.read_events(session_id)
    return {
        "status": "success",
        "data": {
            **meta,
            "status_label": session_store.STATUS_LABEL.get(meta.get("status", ""), ""),
            "events": events,
            "cursor": len(events),
            "pending": get_control().pending(session_id),
            "running": bus.get_bus().is_running(session_id),
        },
    }


@router.delete("/sessions/{session_id}")
def api_delete_session(session_id: str):
    """删除会话记录。

    若任务还在跑，先置停止标志再删，避免出现"记录没了但 Agent 还在写文件"。
    """
    _current_user()
    if session_runner.is_running(session_id):
        session_runner.stop(session_id)
    get_control().clear(session_id)
    bus.get_bus().clear(session_id)
    if not session_store.delete(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "success", "message": "会话已删除"}


# ---------------------------------------------------------------------------
# 事件流（SSE）
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}/events")
def api_session_events(
    session_id: str,
    cursor: int = Query(0, ge=0, description="从第几条事件开始推送"),
):
    """群聊事件流。

    用游标而不是时间戳做增量：客户端把上一条拿到的 ``next_cursor`` 回传，
    服务端只推之后的新事件。这样即使网络抖动重连，也不会漏消息或重复渲染。
    """
    _current_user()
    if not session_store.load(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")

    def event_stream():
        b = bus.get_bus()
        cur = cursor
        idle = 0
        while True:
            events, cur, running = b.read(session_id, cur, timeout=5.0)
            if events:
                idle = 0
                for ev in events:
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                if any(ev.get("kind") in TERMINAL_KINDS for ev in events):
                    yield 'event: end\ndata: {"reason": "terminal"}\n\n'
                    return
            else:
                idle += 1
                # 两个信号都为假、且连续三次（15 秒）没有任何新消息，才判定会话静止。
                #   running           —— 事件总线上的"任务正在跑"标记
                #   is_running(sid)   —— 会话执行器里的登记（已被 start() 接管，
                #                        包含"已提交但还在线程池排队"的情况）
                # 只看其中一个的话，任务从提交到真正开跑之间的空窗会被误判成静止，
                # 连接被提前关掉 —— 用户看到的现象就是"发了消息，什么都没有发生"。
                if (
                    not running
                    and not session_runner.is_running(session_id)
                    and idle >= 3
                ):
                    yield 'event: end\ndata: {"reason": "idle"}\n\n'
                    return
                yield ": keep-alive\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 人工介入：交接确认 / 终止 / 群聊发言
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}/approval")
def api_get_approval(session_id: str):
    _current_user()
    return {"status": "success", "pending": get_control().pending(session_id)}


@router.post("/sessions/{session_id}/approval")
def api_resolve_approval(
    session_id: str,
    req: ApprovalRequest,
):
    """处置一次交接确认：放行 / 打回重做 / 终止。

    ``task`` 字段是"可选改写"：
      - 放行时，若填了内容，就用它替换主Agent 原本交给下游的任务描述；
      - 打回时，它承载用户的修改意见，会拼进重做指令。
    """
    _current_user()
    if req.decision not in ("approve", "reject", "stop"):
        raise HTTPException(status_code=400, detail="decision 只能是 approve / reject / stop")

    ctl = get_control()
    if req.approval_id:
        ok = ctl.resolve(req.approval_id, req.decision, req.task or None)
    else:
        ok = bool(ctl.resolve_session(session_id, req.decision, req.task or None))
    if not ok:
        raise HTTPException(status_code=404, detail="没有待确认的交接项（可能已超时自动放行）")
    return {"status": "success", "decision": req.decision}


@router.post("/sessions/{session_id}/stop")
def api_stop_session(session_id: str):
    _current_user()
    if not session_store.load(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    stopped = session_runner.stop(session_id)
    if not stopped:
        raise HTTPException(status_code=409, detail="该会话当前没有正在执行的任务")
    return {"status": "success", "stopped": True}


@router.post("/sessions/{session_id}/message")
def api_session_message(
    session_id: str,
    req: MessageRequest,
):
    """群聊发言。

    在输入框里 ``@产品经理 这样写行不行`` 就会私聊对应 Agent；
    不 @ 任何人时默认由主Agent 接话。回复走事件流异步推回来，
    所以这里立刻返回，不让 HTTP 请求卡在一次 LLM 调用上。
    """
    username = _current_user()
    text = (req.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if not session_store.load(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")

    agent = (req.agent or "").strip() or _parse_mention(text)
    bus.get_bus().publish(session_id, KIND_USER, content=text,
                          agent=username, mention=agent)
    threading.Thread(
        target=_reply_in_background, args=(session_id, agent, text), daemon=True
    ).start()
    return {"status": "success", "agent": agent}


def _parse_mention(text: str) -> str:
    """从 ``@xxx`` 里解析目标 Agent，解析不到就交给主Agent。"""
    for name in VALID_AGENTS:
        if f"@{name}" in text:
            return name
    return "主Agent"


def _ensure_chat_session(session_id: str, agent: str) -> str:
    """为「会话 × Agent」取得一个多轮对话上下文。

    对话上下文本身存在内存里并会自动过期，所以每次都要校验一遍；
    过期了就重建，历史消息丢了也没关系 —— 真正需要长期保留的是
    事件流（点开历史会话能看到完整对话），那个已经落盘了。
    """
    from ..conversation.session import get_session_manager

    meta = session_store.load(session_id) or {}
    chats = dict(meta.get("chat_sessions") or {})
    mgr = get_session_manager()

    conv_id = chats.get(agent, "")
    if conv_id and mgr.get(conv_id) is not None:
        return conv_id

    conv = mgr.create(agent, {"parent_session": session_id})
    chats[agent] = conv.id
    session_store.update(session_id, chat_sessions=chats)
    return conv.id


def _reply_in_background(session_id: str, agent: str, text: str) -> None:
    b = bus.get_bus()
    b.publish(session_id, KIND_AGENT, agent=agent, phase="working",
              content="正在思考…")
    try:
        from ..conversation.manager import chat_with_agent
        conv_id = _ensure_chat_session(session_id, agent)
        result = chat_with_agent(agent, text, conv_id)
        reply = result.get("reply") or result.get("error") or "（没有返回内容）"
        b.publish(session_id, KIND_AGENT, agent=agent, phase="done", content=reply)
    except Exception as e:
        safe_print(f"[会话] {agent} 回复失败: {e}")
        b.publish(session_id, KIND_ERROR, message=f"{agent} 回复失败: {e}")
