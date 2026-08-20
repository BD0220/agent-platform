"""
FastAPI 服务 — 将多智能体协作平台包装为 API。
启动命令: uvicorn agent_platform.server.api:app --host 0.0.0.0 --port 8000
"""
import json
import os
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..main import run_task
from ..storage.state import load_state, get_state_summary, set_current_task
from ..storage.database import init_db
from ..utils.paths import DATA_DIR, ensure_dirs
from ..utils.safe_print import safe_print
from ..tools import get_tool_definitions
from ..auth.auth import register, login, verify_token
from ..queue.task_queue import get_task_manager

LAST_RESULT_FILE = str(DATA_DIR / "last_result.json")

# SSE 流式端点专用线程池（与任务队列独立，避免阻塞队列线程）
_stream_executor = ThreadPoolExecutor(max_workers=4)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期：初始化目录、数据库，注册 token 回调。"""
    ensure_dirs()
    init_db()
    from ..llm import on_usage
    on_usage(track_token_usage)
    safe_print("[API] 服务启动完成")
    yield
    safe_print("[API] 服务关闭")


app = FastAPI(
    title="多智能体协作平台 API",
    version="1.0.0",
    description="产品经理 / 程序员 / 测试员 三角色协作，主Agent 基于 ReAct + Function Calling 调度。",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class TaskRequest(BaseModel):
    task: str


class AuthRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# 工具 & 认证
# ---------------------------------------------------------------------------
@app.get("/tools", tags=["meta"])
def api_get_tools():
    tools = get_tool_definitions()
    return {"status": "success", "count": len(tools), "tools": tools}


@app.post("/register", tags=["auth"])
def api_register(req: AuthRequest):
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    ok, msg = register(req.username, req.password)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    _, _, token = login(req.username, req.password)
    return {"status": "success", "message": msg, "token": token, "username": req.username}


@app.post("/login", tags=["auth"])
def api_login(req: AuthRequest):
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    ok, msg, token = login(req.username, req.password)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)
    return {"status": "success", "message": msg, "token": token, "username": req.username}


def _require_auth(authorization: str | None) -> str:
    """从 Bearer token 解析用户名，失败抛 401。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="请先登录，缺少认证 Token")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="认证格式错误，应为 Bearer <token>")
    username = verify_token(authorization[7:])
    if username is None:
        raise HTTPException(status_code=401, detail="Token 无效或已过期，请重新登录")
    return username


# ---------------------------------------------------------------------------
# 同步执行
# ---------------------------------------------------------------------------
@app.post("/run", tags=["task"])
def api_run_task(req: TaskRequest, authorization: str = Header(default=None)):
    _require_auth(authorization)
    if not req.task or not req.task.strip():
        raise HTTPException(status_code=400, detail="task 不能为空")
    try:
        result = run_task(req.task)
        with open(LAST_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"任务执行失败: {str(e)}")
    return {
        "status": "success",
        "data": {
            "task": result["task"],
            "task_name": result["task_name"],
            "需求分析": result["功能清单"],
            "代码": result["代码"],
            "测试报告": result["测试报告"],
            "测试状态": result["测试状态"],
            "主Agent调度轮数": result["主Agent调度轮数"],
            "任务完成": result["任务完成"],
            "任务规划": result.get("任务规划", {}),
            "交付目录": result["交付目录"],
        },
    }


# ---------------------------------------------------------------------------
# SSE 流式执行
# ---------------------------------------------------------------------------
@app.get("/run/stream", tags=["task"])
async def api_run_task_stream(
    task: str = Query(..., description="用户需求描述"),
    authorization: str = Header(default=None),
):
    _require_auth(authorization)
    if not task or not task.strip():
        raise HTTPException(status_code=400, detail="task 不能为空")

    task_id = str(uuid.uuid4())[:8]
    result_holder = {"done": False, "data": None, "error": None}

    def run_in_background():
        set_current_task(task_id)
        try:
            result = run_task(task.strip(), task_id=task_id)
            with open(LAST_RESULT_FILE, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            result_holder["data"] = result
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            result_holder["done"] = True

    _stream_executor.submit(run_in_background)

    def _phase_from_state(state: dict) -> tuple[int, str]:
        if not state:
            return 1, "产品经理"
        if "功能清单" in state and "代码" not in state:
            return 2, "程序员"
        if "代码" in state and "测试报告" not in state:
            return 3, "测试员"
        if "测试报告" in state:
            if state.get("测试状态") == "未通过":
                return 4, "程序员"
            return 5, ""
        return 0, ""

    async def event_stream():
        last_state_hash = ""
        await asyncio.sleep(0.5)

        while not result_holder["done"]:
            try:
                state = get_state_summary()
                state_hash = json.dumps(state, sort_keys=True, ensure_ascii=False)
                if state_hash != last_state_hash:
                    last_state_hash = state_hash
                    phase, active_agent = _phase_from_state(state)
                    event_data = json.dumps({
                        "event": "progress",
                        "task_id": task_id,
                        "phase": phase,
                        "active_agent": active_agent,
                        "state": state,
                    }, ensure_ascii=False)
                    yield f"data: {event_data}\n\n"
            except Exception as e:
                safe_print(f"[API] SSE 状态读取异常: {e}")
            await asyncio.sleep(1.0)

        if result_holder["error"]:
            event_data = json.dumps({
                "event": "error", "task_id": task_id,
                "message": result_holder["error"],
            }, ensure_ascii=False)
            yield f"data: {event_data}\n\n"
        else:
            data = result_holder["data"]
            event_data = json.dumps({
                "event": "complete", "task_id": task_id,
                "result": {
                    "task": data.get("task", ""),
                    "task_name": data.get("task_name", ""),
                    "需求分析": data.get("功能清单", ""),
                    "代码": data.get("代码", ""),
                    "测试报告": data.get("测试报告", ""),
                    "测试状态": data.get("测试状态", ""),
                    "主Agent调度轮数": data.get("主Agent调度轮数", 0),
                    "任务完成": data.get("任务完成", False),
                    "任务规划": data.get("任务规划", {}),
                    "交付目录": data.get("交付目录", ""),
                },
            }, ensure_ascii=False)
            yield f"data: {event_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/status", tags=["task"])
def api_get_status():
    state = load_state()
    if state:
        return {"status": "active", "message": "有任务正在执行中", "data": state}
    if os.path.exists(LAST_RESULT_FILE):
        with open(LAST_RESULT_FILE, "r", encoding="utf-8") as f:
            last_result = json.load(f)
        return {"status": "completed", "message": "最近一次任务已完成", "data": last_result}
    return {"status": "idle", "message": "暂无任务记录", "data": None}


# ---------------------------------------------------------------------------
# 异步任务队列接口
# ---------------------------------------------------------------------------
@app.post("/task", tags=["queue"])
def api_submit_task(req: TaskRequest, authorization: str = Header(default=None)):
    _require_auth(authorization)
    if not req.task or not req.task.strip():
        raise HTTPException(status_code=400, detail="task 不能为空")

    tm = get_task_manager()
    task_id = tm.submit(req.task.strip(), _run_task_with_id, username=_require_auth(authorization))
    return {
        "status": "success",
        "task_id": task_id,
        "message": "任务已提交，请通过 GET /task/{task_id}/status 查询进度",
    }


def _run_task_with_id(user_request: str) -> dict:
    """包装 run_task，让队列线程能传入 task_id（从 ContextVar 获取）。"""
    from ..storage.state import get_current_task_id
    tid = get_current_task_id() or ""
    return run_task(user_request, task_id=tid)


@app.get("/task/{task_id}/status", tags=["queue"])
def api_task_status(task_id: str, authorization: str = Header(default=None)):
    _require_auth(authorization)
    task = get_task_manager().get_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"status": "success", "data": task}


@app.get("/task/{task_id}/result", tags=["queue"])
def api_task_result(task_id: str, authorization: str = Header(default=None)):
    _require_auth(authorization)
    task = get_task_manager().get_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] == "running":
        return {
            "status": "pending",
            "message": "任务仍在执行中",
            "progress": task.get("progress", {}),
        }
    elif task["status"] == "completed":
        return {"status": "success", "data": task.get("result_json", {})}
    else:
        return {"status": "error", "message": f"任务执行失败: {task.get('error', '未知错误')}"}


@app.delete("/task/{task_id}", tags=["queue"])
def api_cancel_task(task_id: str, authorization: str = Header(default=None)):
    _require_auth(authorization)
    ok = get_task_manager().cancel(task_id)
    return {"status": "success" if ok else "failed", "cancelled": ok}


@app.get("/tasks", tags=["queue"])
def api_list_tasks(
    username: str = "",
    status: str = "",
    limit: int = 20,
    authorization: str = Header(default=None),
):
    auth_user = _require_auth(authorization)
    tasks = get_task_manager().list_tasks(
        username=username or auth_user, status=status, limit=limit
    )
    return {"status": "success", "count": len(tasks), "tasks": tasks}


@app.get("/stats", tags=["queue"])
def api_get_stats(authorization: str = Header(default=None)):
    _require_auth(authorization)
    stats = get_task_manager().get_stats()
    stats["active_tasks"] = get_task_manager().active_count()
    return {"status": "success", "data": stats}


# ---------------------------------------------------------------------------
# Token 用量追踪
# ---------------------------------------------------------------------------
_token_usage: dict = {
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_calls": 0,
    "by_model": {},
}


def track_token_usage(model: str, usage: dict):
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    _token_usage["total_prompt_tokens"] += prompt
    _token_usage["total_completion_tokens"] += completion
    _token_usage["total_calls"] += 1
    if model not in _token_usage["by_model"]:
        _token_usage["by_model"][model] = {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        }
    entry = _token_usage["by_model"][model]
    entry["calls"] += 1
    entry["prompt_tokens"] += prompt
    entry["completion_tokens"] += completion


@app.get("/metrics", tags=["meta"])
def api_get_metrics(authorization: str = Header(default=None)):
    _require_auth(authorization)
    return {"status": "success", "data": _token_usage}


# ---------------------------------------------------------------------------
# 对话式交互
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    agent: str = "主Agent"
    message: str


_VALID_AGENTS = {"主Agent", "产品经理", "程序员", "测试员"}


@app.post("/chat/session", tags=["chat"])
def api_create_chat_session(
    agent: str = Query("主Agent", description="Agent 角色名称"),
    authorization: str = Header(default=None),
):
    username = _require_auth(authorization)
    if agent not in _VALID_AGENTS:
        raise HTTPException(status_code=400, detail=f"未知 Agent: {agent}，可选: {sorted(_VALID_AGENTS)}")
    from ..conversation.session import get_session_manager
    session = get_session_manager().create(agent, {"username": username})
    return {
        "status": "success", "session_id": session.id, "agent": agent,
        "message": f"与 {agent} 的对话已开始",
    }


@app.delete("/chat/session/{session_id}", tags=["chat"])
def api_delete_chat_session(session_id: str, authorization: str = Header(default=None)):
    _require_auth(authorization)
    from ..conversation.session import get_session_manager
    ok = get_session_manager().delete(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "success", "message": "会话已结束"}


@app.post("/chat/session/{session_id}/message", tags=["chat"])
def api_chat_message(
    session_id: str,
    req: ChatRequest,
    authorization: str = Header(default=None),
):
    _require_auth(authorization)
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    from ..conversation.manager import chat_with_agent
    result = chat_with_agent(req.agent, req.message.strip(), session_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {
        "status": "success",
        "session_id": result["session_id"],
        "agent": result["agent"],
        "reply": result["reply"],
        "history_length": result["history_length"],
    }


@app.get("/chat/sessions", tags=["chat"])
def api_list_chat_sessions(authorization: str = Header(default=None)):
    _require_auth(authorization)
    from ..conversation.session import get_session_manager
    sessions = get_session_manager().list_sessions()
    return {
        "status": "success", "count": len(sessions),
        "sessions": [
            {"id": s.id, "agent": s.agent, "messages": len(s.messages), "created_at": s.created_at}
            for s in sessions
        ],
    }


# ---------------------------------------------------------------------------
# 知识库导入
# ---------------------------------------------------------------------------
class KBImportRequest(BaseModel):
    text: str = ""
    title: str = "untitled"
    path: str = ""


@app.post("/kb/collections/{name}/upload", tags=["knowledge"])
def api_kb_upload(name: str, req: KBImportRequest, authorization: str = Header(default=None)):
    _require_auth(authorization)
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    from ..rag.import_docs import import_text
    count = import_text(req.text.strip(), req.title.strip() or "untitled", name)
    return {"status": "success", "collection": name, "chunks": count,
            "message": f"已导入 {count} 个文本块"}


@app.post("/kb/collections/{name}/import", tags=["knowledge"])
def api_kb_import(name: str, req: KBImportRequest, authorization: str = Header(default=None)):
    _require_auth(authorization)
    if not req.path.strip():
        raise HTTPException(status_code=400, detail="路径不能为空")
    from ..rag.import_docs import import_file, import_directory
    target = req.path.strip()
    if os.path.isfile(target):
        count = import_file(target, name)
    elif os.path.isdir(target):
        count = import_directory(target, name)
    else:
        raise HTTPException(status_code=404, detail=f"路径不存在: {target}")
    return {"status": "success", "collection": name, "chunks": count,
            "message": f"已导入 {count} 个文本块"}


@app.get("/kb/collections/{name}/search", tags=["knowledge"])
def api_kb_search(
    name: str,
    q: str = Query(..., description="搜索关键词"),
    top_k: int = Query(5, description="返回数量"),
    authorization: str = Header(default=None),
):
    _require_auth(authorization)
    from ..rag.import_docs import search_knowledge
    result = search_knowledge(q.strip(), name, top_k)
    return {"status": "success", "query": q, "collection": name, "result": result}


@app.get("/kb/collections", tags=["knowledge"])
def api_kb_list_collections(authorization: str = Header(default=None)):
    _require_auth(authorization)
    from ..rag.import_docs import list_kb_collections
    collections = list_kb_collections()
    return {"status": "success", "count": len(collections), "collections": collections}


@app.delete("/kb/collections/{name}", tags=["knowledge"])
def api_kb_delete(name: str, authorization: str = Header(default=None)):
    _require_auth(authorization)
    from ..rag.import_docs import delete_kb_collection
    ok = delete_kb_collection(name)
    return {"status": "success" if ok else "error", "deleted": ok}
