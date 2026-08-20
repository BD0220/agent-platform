"""用户认证模块 —— SQLite 存储 + bcrypt 密码哈希 + Token 会话管理。"""
import json
import secrets
import time

import bcrypt

from ..storage.database import db_create_user, db_get_user, db_get_all_users
from ..utils.paths import DATA_DIR
from ..utils.safe_print import safe_print

_TOKEN_FILE = DATA_DIR / "tokens.json"
_active_tokens: dict[str, dict] = {}
TOKEN_EXPIRE_SECONDS = 86400  # 24 小时


def _load_tokens():
    """从文件恢复 token（进程重启后保留登录态）。"""
    if _TOKEN_FILE.exists():
        try:
            with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for token, info in data.items():
                if now - info.get("created_at", 0) <= TOKEN_EXPIRE_SECONDS:
                    _active_tokens[token] = info
        except (json.JSONDecodeError, IOError):
            pass


def _save_tokens():
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(_active_tokens, f, ensure_ascii=False, indent=2)
    except (OSError, IOError):
        pass


def register(username: str, password: str) -> tuple[bool, str]:
    username = username.strip()
    if not username or len(username) < 2:
        return False, "用户名至少需要 2 个字符"
    if not password or len(password) < 4:
        return False, "密码至少需要 4 个字符"
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    ok, msg = db_create_user(username, hashed)
    if ok:
        safe_print(f"[认证] 新用户注册: {username}")
    return ok, msg


def login(username: str, password: str) -> tuple[bool, str, str | None]:
    username = username.strip()
    user_data = db_get_user(username)
    if not user_data:
        return False, f"用户 '{username}' 不存在", None
    stored_hash = user_data["password_hash"].encode("utf-8")
    if not bcrypt.checkpw(password.encode("utf-8"), stored_hash):
        return False, "密码错误", None
    token = secrets.token_hex(32)
    _active_tokens[token] = {"username": username, "created_at": time.time()}
    _save_tokens()
    safe_print(f"[认证] 用户登录: {username}")
    return True, f"登录成功，欢迎回来 {username}！", token


def verify_token(token: str) -> str | None:
    if not token or token not in _active_tokens:
        return None
    token_data = _active_tokens[token]
    if time.time() - token_data["created_at"] > TOKEN_EXPIRE_SECONDS:
        del _active_tokens[token]
        return None
    return token_data["username"]


def logout(token: str) -> bool:
    if token in _active_tokens:
        del _active_tokens[token]
        _save_tokens()
        return True
    return False


_load_tokens()


def get_all_users() -> list[str]:
    return db_get_all_users()
