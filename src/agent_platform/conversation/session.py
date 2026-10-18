"""会话管理器 —— 内存存储，支持多 Agent 多轮对话。"""
import threading
import uuid
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    id: str
    agent: str
    messages: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        self.last_active = time.time()


class SessionManager:
    """内存会话存储。"""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, agent: str, metadata: dict = None) -> Session:
        sid = str(uuid.uuid4())[:12]
        session = Session(id=sid, agent=agent, metadata=metadata or {})
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_sessions(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: s.last_active, reverse=True)

    def cleanup_expired(self, max_age_seconds: int = 3600):
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_active > max_age_seconds]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def start_auto_cleanup(self, interval_seconds: int = 600, max_age_seconds: int = 3600):
        """启动后台线程定期清理过期会话。"""
        def _loop():
            while True:
                time.sleep(interval_seconds)
                try:
                    n = self.cleanup_expired(max_age_seconds)
                    if n > 0:
                        from ..utils.safe_print import safe_print
                        safe_print(f"[会话] 自动清理 {n} 个过期会话")
                except Exception as e:
                    from ..utils.safe_print import safe_print
                    safe_print(f"[会话] 自动清理异常: {e}")
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    @property
    def count(self) -> int:
        return len(self._sessions)


_session_manager = SessionManager()
_session_manager.start_auto_cleanup()


def get_session_manager() -> SessionManager:
    return _session_manager
