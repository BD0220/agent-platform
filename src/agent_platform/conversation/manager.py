"""对话管理器 —— 多轮对话逻辑，Agent 可反问用户。"""
from ..utils.safe_print import safe_print
from ..agents.call_agent import call_agent
from ..agents.definitions import SYSTEM_PROMPTS

from .session import get_session_manager, Session

CHAT_SYSTEM_APPEND = (
    "\n\n## 对话模式"
    "\n你正在与用户进行多轮对话。你可以："
    "\n1. 直接回答用户的问题"
    "\n2. 使用工具完成任务（如果有可用工具）"
    "\n3. 反问用户以澄清需求（如果信息不足）"
    "\n请保持对话自然、有帮助性。不要假装使用了工具。"
    "\n当前日期: {date}"
)


def _build_chat_system_prompt(agent: str) -> str:
    from datetime import datetime
    base = SYSTEM_PROMPTS.get(agent, f"你是 {agent}。请帮助用户完成任务。")
    return base + CHAT_SYSTEM_APPEND.format(date=datetime.now().strftime("%Y-%m-%d %H:%M"))


def chat_with_agent(agent: str, user_message: str, session_id: str = None) -> dict:
    """与指定 Agent 进行一轮对话。返回 {session_id, agent, reply}。"""
    mgr = get_session_manager()

    if session_id:
        session = mgr.get(session_id)
        if not session:
            return {"error": f"会话 {session_id} 不存在", "session_id": session_id}
    else:
        session = mgr.create(agent)
        session_id = session.id

    session.add_message("user", user_message)

    # 构建消息
    system_prompt = _build_chat_system_prompt(agent)
    messages = [{"role": "system", "content": system_prompt}]

    # 加入历史（最近 20 条）
    history = session.messages[-20:]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    safe_print(f"[对话] {agent} 会话 {session_id}: 用户消息长度 {len(user_message)} 字")

    try:
        response = call_agent(agent, user_message, messages_override=messages)
    except TypeError:
        # call_agent 可能不接受 messages_override
        response = call_agent(agent, user_message)

    session.add_message("assistant", response)

    return {
        "session_id": session_id,
        "agent": agent,
        "user_message": user_message,
        "reply": response,
        "history_length": len(session.messages),
    }


def stream_chat_with_agent(agent: str, user_message: str, session_id: str = None):
    """流式对话（简化版：先完整调用再 yield 结果）。"""
    result = chat_with_agent(agent, user_message, session_id)
    yield result
