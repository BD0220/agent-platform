"""短期记忆：Redis 读写，降级 JSON 文件。

Redis 连接参数从环境变量读取（``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_DB``），
Docker Compose 和本地开发均可通过 ``.env`` 配置。
"""
import hashlib
import json
import os
import re
from datetime import datetime

from ..utils.paths import DATA_DIR
from ..utils.safe_print import safe_print

SHORT_TERM_TTL = 86400  # 24 小时

_redis_client = None
_redis_available = None

# Windows 文件名不允许的字符（冒号首当其冲）
_UNSAFE_FILENAME = re.compile(r'[^A-Za-z0-9._\-]')


def _safe_filename(session_id: str) -> str:
    """把会话 key 转成合法的降级文件名。

    会话 key 形如 ``task:<task_id>:state``，里面带冒号 —— 而 Windows 文件名
    不允许冒号，直接拿去拼路径会抛 ``OSError: [Errno 22] Invalid argument``。
    这个异常之前没被捕获，导致整个降级通道形同虚设：每存一次状态就报一次错，
    短期记忆在 Windows 上等于从来没生效过。
    转义会丢信息（``a:b`` 和 ``a_b`` 会撞名），所以补一个短哈希后缀兜底。
    """
    safe = _UNSAFE_FILENAME.sub("_", session_id)[:80]
    digest = hashlib.md5(session_id.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


def _fallback_path(session_id: str):
    return DATA_DIR / f".session_{_safe_filename(session_id)}.json"


def _redis_kwargs() -> dict:
    """从环境变量构建 Redis 连接参数。"""
    return {
        "host": os.environ.get("REDIS_HOST", "localhost"),
        "port": int(os.environ.get("REDIS_PORT", "6379")),
        "db": int(os.environ.get("REDIS_DB", "0")),
        "socket_connect_timeout": float(os.environ.get("REDIS_CONNECT_TIMEOUT", "1")),
        "socket_timeout": float(os.environ.get("REDIS_SOCKET_TIMEOUT", "1")),
        "decode_responses": True,
    }


def _get_redis():
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        try:
            _redis_client.ping()
            return _redis_client
        except Exception:
            _redis_client = None
            _redis_available = False
            return None
    try:
        import redis
        _redis_client = redis.Redis(**_redis_kwargs())
        _redis_client.ping()
        _redis_available = True
        safe_print("[短期记忆] Redis 连接成功")
        return _redis_client
    except Exception as e:
        _redis_available = False
        safe_print(f"[短期记忆] Redis 不可用 ({e})，降级到 JSON 文件")
        return None


def save_context(session_id: str, data: dict):
    r = _get_redis()
    if r:
        try:
            key = f"session:{session_id}:context"
            r.setex(key, SHORT_TERM_TTL, json.dumps(data, ensure_ascii=False))
            return
        except Exception as e:
            safe_print(f"[短期记忆] Redis 写入失败，降级 JSON: {e}")

    fallback_file = _fallback_path(session_id)
    payload = dict(data)
    payload["_fallback"] = True
    payload["_expires_at"] = datetime.now().timestamp() + SHORT_TERM_TTL
    try:
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except (UnicodeEncodeError, UnicodeDecodeError, OSError) as e:
        # 降级通道本身再出错也不能让调用方（状态管理器）挂掉
        safe_print(f"[短期记忆] 降级文件写入失败: {e}")


def load_context(session_id: str) -> dict:
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"session:{session_id}:context")
            if raw:
                return json.loads(raw)
        except Exception as e:
            safe_print(f"[短期记忆] Redis 读取失败: {e}")

    # 必须和 save_context 用同一个路径函数。这里曾经写死过旧式路径，
    # 结果成了"写一个文件、读另一个文件"：save_state 写进新路径，
    # load_state 读旧路径永远读不到，整条状态链路上读到的都是空字典。
    fallback_file = _fallback_path(session_id)
    if fallback_file.exists():
        try:
            with open(fallback_file, encoding="utf-8") as f:
                data = json.load(f)
            expires = data.get("_expires_at", 0)
            if datetime.now().timestamp() > expires:
                fallback_file.unlink(missing_ok=True)
                return {}
            return data
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def delete_context(session_id: str):
    r = _get_redis()
    if r:
        try:
            r.delete(f"session:{session_id}:context")
        except Exception as e:
            safe_print(f"[短期记忆] Redis 删除失败: {e}")
    fallback_file = _fallback_path(session_id)
    fallback_file.unlink(missing_ok=True)
