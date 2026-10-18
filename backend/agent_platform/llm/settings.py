"""运行时设置 —— 让"换模型 / 填密钥"在界面上就能做完，不必改 .env 再重启。

存哪、为什么
    ``data/settings.json``。之所以不写回 .env：.env 是"部署配置"，
    手改容易改坏、还牵扯编码问题；而模型选择是**运行期偏好**，
    用户在界面上点一下就换，属于应用数据，写进 data/ 才合理。
    这个文件含 API Key，已在 .gitignore 里排除，绝不能被提交。

密钥的读取优先级
    界面上填的  >  环境变量（.env）  >  没有
    这样既照顾"点开就能用"（.env 里已经有 key 的老用户），
    又允许临时覆盖（比如想试试别人的 key）。

对外只暴露掩码
    ``masked_keys()`` 返回 ``sk-1****cdef`` 这种形态。前端永远拿不到明文，
    也就不会被日志、截图、浏览器历史顺手带走。
"""
import json
import os
import threading

from ..utils.paths import DATA_DIR
from ..utils.safe_print import safe_print

SETTINGS_FILE = DATA_DIR / "settings.json"
_lock = threading.Lock()

_DEFAULT = {
    "active_model": "",          # 空表示"还没选过"，由 models.default_model_id() 兜底
    "keys": {},                  # {vendor_id: api_key}
    "custom": {                  # 自定义端点的参数
        "base_url": "",
        "model": "",
    },
}


def _load() -> dict:
    if not SETTINGS_FILE.exists():
        return dict(_DEFAULT)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        safe_print(f"[设置] 读取失败，使用默认值: {e}")
        return dict(_DEFAULT)
    merged = dict(_DEFAULT)
    merged.update(data or {})
    merged["keys"] = dict(merged.get("keys") or {})
    merged["custom"] = {**_DEFAULT["custom"], **(merged.get("custom") or {})}
    return merged


def _save(data: dict) -> None:
    with _lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(SETTINGS_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)


# ---------------------------------------------------------------------------
# 当前模型
# ---------------------------------------------------------------------------
def get_active_model_id() -> str:
    from .models import default_model_id

    mid = (_load().get("active_model") or "").strip()
    return mid or default_model_id()


def set_active_model(model_id: str) -> None:
    data = _load()
    data["active_model"] = (model_id or "").strip()
    _save(data)
    safe_print(f"[设置] 当前模型切换为: {data['active_model'] or '(默认)'}")


# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------
def resolve_key(vendor_id: str) -> str:
    """取某厂商可用的 key：界面填的优先，其次环境变量。"""
    from .models import VENDORS

    if not vendor_id:
        return ""
    entered = (_load().get("keys") or {}).get(vendor_id, "")
    if entered:
        return entered.strip()
    env_name = (VENDORS.get(vendor_id) or {}).get("key_env", "")
    return (os.environ.get(env_name, "") if env_name else "").strip()


def set_key(vendor_id: str, api_key: str) -> None:
    data = _load()
    keys = dict(data.get("keys") or {})
    api_key = (api_key or "").strip()
    if api_key:
        keys[vendor_id] = api_key
    else:
        keys.pop(vendor_id, None)      # 传空串 = 清除（回落到环境变量）
    data["keys"] = keys
    _save(data)
    safe_print(f"[设置] 已更新 {vendor_id} 的 API Key（{'已设置' if api_key else '已清除'}）")


def mask(value: str) -> str:
    """把 key 变成 ``sk-1****cdef``，够用来确认"是不是这一把"。"""
    if not value:
        return ""
    if len(value) <= 10:
        return value[:2] + "****"
    return f"{value[:4]}****{value[-4:]}"


def masked_keys() -> dict:
    """给界面看的 key 状态：掩码 + 来源，永远不含明文。"""
    from .models import CUSTOM_ID, VENDORS

    keys = _load().get("keys") or {}
    out = {}
    for vid, meta in VENDORS.items():
        entered = keys.get(vid, "")
        env_name = meta.get("key_env", "")
        env_val = os.environ.get(env_name, "") if env_name else ""
        if entered:
            out[vid] = {"configured": True, "source": "界面", "masked": mask(entered)}
        elif env_val:
            out[vid] = {"configured": True, "source": "环境变量",
                        "masked": mask(env_val)}
        else:
            out[vid] = {"configured": False, "source": "", "masked": ""}

    # 自定义端点没有对应的环境变量，只看界面填了什么
    custom = keys.get(CUSTOM_ID, "")
    out[CUSTOM_ID] = {
        "configured": bool(custom), "source": "界面" if custom else "",
        "masked": mask(custom),
    }
    return out


# ---------------------------------------------------------------------------
# 自定义端点
# ---------------------------------------------------------------------------
def get_custom() -> dict:
    return _load().get("custom") or {"base_url": "", "model": ""}


def set_custom(base_url: str, model: str) -> None:
    data = _load()
    data["custom"] = {
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
    }
    _save(data)
