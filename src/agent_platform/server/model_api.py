"""模型管理 API —— 界面上那个「模型」切换器与密钥面板的后端。

为什么单独一个 router
    和 ``session_api.py`` 同样的理由：不动 ``api.py`` 里的旧端点，
    用 ``include_router`` 挂上去，旧前端一个字节都不用改。

安全约定
    密钥**只进不出**：这个模块所有响应里都不含明文 key，
    只返回 ``sk-1****cdef`` 这种掩码 + "来源是界面还是环境变量"。
"""
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..llm import settings as llm_settings
from ..llm.factory import current_model_info, reset_llm, switch_model
from ..llm.models import CUSTOM_ID, VENDORS, get_model, list_models, vendor_of
from ..utils.safe_print import safe_print

router = APIRouter(tags=["model"])


class SwitchRequest(BaseModel):
    model_id: str


class KeyRequest(BaseModel):
    vendor: str
    api_key: str = ""


class CustomRequest(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""


def _payload() -> dict:
    """界面需要的全部模型状态，一处拼好。"""
    keys = llm_settings.masked_keys()
    models = []
    for m in list_models():
        item = dict(m)
        item["key_configured"] = bool((keys.get(m["vendor"]) or {}).get("configured"))
        models.append(item)

    vendors = []
    for vid, v in VENDORS.items():
        vendors.append({
            **v,
            "key_status": keys.get(vid) or {"configured": False, "source": "", "masked": ""},
        })

    custom = llm_settings.get_custom()
    return {
        "status": "success",
        "models": models,
        "vendors": vendors,
        "custom": {**custom, "key_status": keys.get(CUSTOM_ID)},
        "current": current_model_info(),
    }


@router.get("/models")
def api_list_models():
    return _payload()


@router.post("/model")
def api_switch_model(req: SwitchRequest):
    if not get_model(req.model_id):
        raise HTTPException(status_code=404, detail=f"未知模型: {req.model_id}")
    result = switch_model(req.model_id)
    safe_print(f"[模型] 切换 -> {req.model_id}: {result.get('message')}")
    return {**_payload(), "message": result.get("message", ""), "ok": result.get("ok", False)}


@router.post("/models/key")
def api_set_key(req: KeyRequest):
    """保存某厂商的 API Key。传空串表示清除（回落到 .env 里的环境变量）。"""
    if req.vendor != CUSTOM_ID and req.vendor not in VENDORS:
        raise HTTPException(status_code=404, detail=f"未知厂商: {req.vendor}")
    llm_settings.set_key(req.vendor, req.api_key)
    reset_llm()
    return _payload()


@router.post("/models/custom")
def api_set_custom(req: CustomRequest):
    """保存自定义端点的 base_url / model / key，并切到自定义模型。"""
    if not req.base_url.strip() or not req.model.strip():
        raise HTTPException(status_code=400, detail="base_url 与 model 都不能为空")
    llm_settings.set_custom(req.base_url, req.model)
    if req.api_key.strip():
        llm_settings.set_key(CUSTOM_ID, req.api_key)
    reset_llm()
    switch_model(CUSTOM_ID)
    return _payload()


@router.post("/models/test")
def api_test_model():
    """拿当前模型打一次最小请求，确认 key / base_url / 模型名都对得上。

    这是最实用的一步：配置类问题（key 错、模型名错、base_url 少个 /v1）
    用肉眼看不出来，但一次真实调用立刻就能定位。
    """
    info = current_model_info()
    started = time.time()
    try:
        from ..llm import get_llm

        provider = get_llm()
        result = provider.chat(
            messages=[{"role": "user", "content": "ping，只回复 pong"}],
            tools=None, temperature=0,
        )
        elapsed = int((time.time() - started) * 1000)
        text = (result.content or "").strip().replace("\n", " ")[:80]
        usage = result.usage or {}
        return {
            "status": "success", "ok": True, "latency_ms": elapsed,
            "model": provider.model_name, "provider": provider.provider_name,
            "reply": text, "usage": usage,
            "message": f"连接成功（{elapsed} ms）：{text or '(空回复)'}",
        }
    except Exception as e:
        elapsed = int((time.time() - started) * 1000)
        msg = str(e)
        hint = ""
        low = msg.lower()
        if "401" in low or "invalid" in low and "key" in low or "authentication" in low:
            hint = "看起来是 API Key 不对或没权限，去厂商控制台确认一下。"
        elif "model" in low and ("not" in low or "不存在" in msg):
            hint = "模型名可能写错了，注意区分厂商自己的命名（如 qwen-max 与 qwen3-max）。"
        elif "connect" in low or "timeout" in low or "getaddrinfo" in low:
            hint = "网络连不上，检查 base_url 以及是否需要代理。"
        elif "404" in low:
            hint = "base_url 路径可能不对，兼容端点通常要带 /v1 结尾。"
        safe_print(f"[模型] 连通性测试失败（{info.get('model_id')}）: {msg[:200]}")
        return {
            "status": "success", "ok": False, "latency_ms": elapsed,
            "model": info.get("model", ""), "provider": info.get("provider", ""),
            "message": f"连接失败（{elapsed} ms）：{msg[:200]}"
                       + (f"\n\n提示：{hint}" if hint else ""),
        }
