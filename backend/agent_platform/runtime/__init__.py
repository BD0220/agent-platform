"""运行时协作层 —— 会话事件总线、交接审批、工作空间、会话持久化。

这一层是"前端可见的协作过程"与"后端 Agent 执行"之间的边界：

    Agent 执行线程  ──publish──▶  SessionBus  ──SSE──▶  浏览器群聊
                          ▲
                          │
                  ApprovalGate（阻塞等待人工确认）

设计原则：所有状态都按 ``session_id`` 隔离，而 ``session_id`` 直接复用
``storage.state`` 里的 ``ContextVar`` task_id，因此并发会话天然不会串台。
"""
