"""短期记忆：Redis 读写，降级 JSON 文件。

Redis 连接参数从环境变量读取（``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_DB``），
Docker Compose 和本地开发均可通过 ``.env`` 配置。
"""
import json
import os
from datetime import datetime

from ..utils.paths import DATA_DIR
from ..utils.safe_print import safe_print

SHORT_TERM_TTL = 86400  # 24 小时

_redis_client = None
_redis_available = None


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

    fallback_file = DATA_DIR / f".session_{session_id}.json"
    payload = dict(data)
    payload["_fallback"] = True
    payload["_expires_at"] = datetime.now().timestamp() + SHORT_TERM_TTL
    try:
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except (UnicodeEncodeError, UnicodeDecodeError):
        with open(fallback_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2)


def load_context(session_id: str) -> dict:
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"session:{session_id}:context")
            if raw:
                return json.loads(raw)
        except Exception as e:
            safe_print(f"[短期记忆] Redis 读取失败: {e}")

    fallback_file = DATA_DIR / f".session_{session_id}.json"
    if fallback_file.exists():
        try:
            with open(fallback_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            expires = data.get("_expires_at", 0)
            if datetime.now().timestamp() > expires:
                fallback_file.unlink(missing_ok=True)
                return {}
            return data
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def delete_context(session_id: str):
    r = _get_redis()
    if r:
        try:
            r.delete(f"session:{session_id}:context")
        except Exception as e:
            safe_print(f"[短期记忆] Redis 删除失败: {e}")
    fallback_file = DATA_DIR / f".session_{session_id}.json"
    fallback_file.unlink(missing_ok=True)
