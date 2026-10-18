"""模型注册表 —— 界面上那个「模型」下拉框的全部数据来源。

为什么不给每家厂商写一个 Provider 类？
    这几家的 API 全是 OpenAI 兼容协议，"换模型"本质上只是换三个值：
    ``base_url`` / ``api_key`` / ``model``。一家一个类是纯重复劳动，
    新增厂商的成本从"写一个类 + 注册 + 测试"降到"加一条数据"。

价格与擅长领域的定位
    纯描述性元数据，只用来在界面上帮人做判断，**不参与任何计算逻辑** ——
    所以厂商调价时只需要改这里的数据，业务代码一行都不用动。

    价格这东西变化很快，写进代码就有过期的风险，因此：
      · 每条都带 ``price_note``，明确写出价格口径和取数时间；
      · 拿不准的一律留 ``None``，界面显示"以官网为准"，绝不编一个数字上去；
      · ``price_src`` 放官方定价页，方便用户自己核对。

    参考事实（2026-09 核对）：
      · DeepSeek 2026-07 已下线 ``deepseek-r1``（调用报 400），
        ``deepseek-chat`` / ``deepseek-reasoner`` 这两个名字现在都指向 V4 Flash；
      · DeepSeek 2026-08-16 起改为高峰 / 低谷双价，工作日 09:00-12:00、
        14:00-18:00（北京时间）按 2 倍计费。
"""

# 币种统一用人民币，方便横向比较；美元计价的按当期汇率折算并注明
_CURRENCY = "¥"
_UNIT = "元 / 百万 tokens"


def _vendor(vid, name, base_url, key_env, console, pricing_url, howto, note=""):
    return {
        "id": vid,
        "name": name,
        "base_url": base_url,
        "key_env": key_env,
        "console": console,
        "pricing_url": pricing_url,
        "howto": howto,
        "note": note,
    }


VENDORS = {
    v["id"]: v for v in [
        _vendor(
            "deepseek", "深度求索 DeepSeek",
            "https://api.deepseek.com", "DEEPSEEK_API_KEY",
            "https://platform.deepseek.com",
            "https://api-docs.deepseek.com/quick_start/pricing",
            "注册 → 左侧 API keys → 新建，key 只显示一次；充值后按量扣费。",
            "新账号通常有体验额度（以官网为准）。工作日晚高峰按 2 倍计费。",
        ),
        _vendor(
            "dashscope", "阿里云百炼（通义千问）",
            "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY",
            "https://bailian.console.aliyun.com",
            "https://help.aliyun.com/zh/model-studio/model-pricing",
            "阿里云账号 → 开通百炼 → API-KEY 管理页创建；北京地域多数模型有免费额度。",
            "走 compatible-mode 兼容端点，就能用 OpenAI SDK 直连。",
        ),
        _vendor(
            "zhipu", "智谱 GLM",
            "https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY",
            "https://open.bigmodel.cn",
            "https://docs.z.ai/guides/overview/pricing",
            "注册后在「API Keys」创建。",
            "GLM 系列有免费档（Flash），适合开发阶段反复调试验证。",
        ),
        _vendor(
            "moonshot", "月之暗面 Kimi",
            "https://api.moonshot.cn/v1", "MOONSHOT_API_KEY",
            "https://platform.moonshot.cn",
            "https://platform.kimi.com/docs/pricing/chat",
            "注册后在「API Key 管理」创建。",
            "K2 系列是长上下文 + 代码方向的强项。",
        ),
        _vendor(
            "tencent", "腾讯混元 Hunyuan",
            "https://api.hunyuan.cloud.tencent.com/v1", "HUNYUAN_API_KEY",
            "https://console.cloud.tencent.com/hunyuan",
            "https://cloud.tencent.com/document/product/1729/104753",
            "腾讯云控制台 → 混元大模型 →「API Key 管理」→ 新建，"
            "拿到 sk- 开头的 Key 直接填进来即可（不需要 SecretId/SecretKey 签名）。",
            "⚠ 两个坑：① 接口默认只有 5 个并发（主子账号共享），"
            "Agent 多轮并行时容易撞限流；② 腾讯正在把混元迁到 TokenHub，"
            "新开通模型服务建议直接去 TokenHub 控制台。",
        ),
    ]
}


def _m(mid, label, vendor, model, context, p_in, p_out, price_note,
       tags, strengths, weakness, best_for, price_src="", recommend=False):
    return {
        "id": mid,
        "label": label,
        "vendor": vendor,
        "model": model,
        "context": context,
        "price_in": p_in,
        "price_out": p_out,
        "currency": _CURRENCY,
        "price_unit": _UNIT,
        "price_note": price_note,
        "price_src": price_src,
        "tags": tags,
        "strengths": strengths,
        "weakness": weakness,
        "best_for": best_for,
        "recommend": recommend,
    }


MODELS = [
    # ── DeepSeek ────────────────────────────────────────────────
    # 2026-10-13 实测修正：DeepSeek API 只认 deepseek-flash / deepseek-v4-pro
    # 两个名字（传 deepseek-chat / deepseek-reasoner / v4.x 一律 400）。
    # 上一版注册表里的 v4.1-flash 等名字是错的，已按 API 报错信息改正。
    _m(
        "deepseek-flash", "DeepSeek Flash", "deepseek",
        "deepseek-flash",
        "1M", None, None,
        "价格以官网为准（价格变动快，不写死数字）",
        ["性价比第一梯队", "1M 上下文", "Agent 循环首选"],
        "DeepSeek 当前的主力档位，API 报错信息里给出的两个正式模型名之一。"
        "价格与档位以官网定价页为准。",
        "高峰时段可能按倍率计费，跑大批量前先看一眼官网。",
        "本项目的默认选择：Agent 循环调用量大，DeepSeek 便宜稳定。",
        "https://api-docs.deepseek.com/quick_start/pricing",
        recommend=True,
    ),
    _m(
        "deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek", "deepseek-v4-pro",
        "1M", None, None,
        "价格以官网为准（价格变动快，不写死数字）",
        ["强推理", "1M 上下文", "代码"],
        "DeepSeek 的旗舰档，复杂重构和长链推理更稳。",
        "比 Flash 贵，简单任务上用它是浪费。",
        "默认模型反复做不对、需要更强规划能力时切过来救场。",
        "https://api-docs.deepseek.com/quick_start/pricing",
    ),

    # ── 阿里云百炼（通义千问）──────────────────────────────────
    _m(
        "qwen-plus", "通义千问 Qwen Plus", "dashscope", "qwen-plus",
        "1M", 0.8, 4.8,
        "2026-09 官网价（单次输入 ≤128K 档）；更长输入分档涨价",
        ["阿里云", "均衡", "中文强"],
        "千问系列的中档主力，中文理解与指令跟随稳，工具调用支持完善。"
        "1M 上下文，价格比 qwen-max 低一个档。",
        "输出价随输入长度分档上浮（128K 以上翻 2.5 倍），超长上下文场景要留意。",
        "想试国产非 DeepSeek 路线时的首选；中文场景比同价位海外模型更顺。",
        "https://help.aliyun.com/zh/model-studio/model-pricing",
    ),
    _m(
        "qwen3.5-flash", "通义千问 Qwen3.5 Flash", "dashscope", "qwen3.5-flash",
        "1M", 0.24, 2.4,
        "2026-09 官网价",
        ["极便宜", "轻量任务"],
        "便宜到可以忽略成本，做格式化、抽取、简单问答这类不需要深推理的活很合适。",
        "复杂 Agent 规划容易掉链子，别拿它跑主循环。",
        "跑批量回归测试、压测闸门逻辑时用它，把成本压到最低。",
        "https://help.aliyun.com/zh/model-studio/model-pricing",
    ),
    _m(
        "qwen-max", "通义千问 Qwen Max", "dashscope", "qwen-max",
        "1M", 2.4, 9.6,
        "2026-09 官网价；北京地域有免费额度",
        ["旗舰", "中文强", "阿里云"],
        "千问的上一代旗舰，中文写作、长文理解和指令遵循都很扎实，工程上很稳。",
        "价格是 qwen-plus 的 2-3 倍，且已被 qwen3-max / qwen3.8-max 超越。",
        "演示中文交付物（需求文档、测试报告）质量时，输出更「像人写的」。",
        "https://help.aliyun.com/zh/model-studio/model-pricing",
    ),
    _m(
        "qwen3-max", "通义千问 Qwen3 Max", "dashscope", "qwen3-max",
        "256K", 2.5, 10.0,
        "2026-09 官网价（单次输入 ≤32K 档）",
        ["新一代旗舰", "思考模式", "工具调用"],
        "支持思考 / 非思考双模式，复杂规划能力比上一代强一截，Agent 场景更耐用。",
        "输入超 32K 后单价上浮（128K 档 ¥4，256K 档 ¥7）；上下文 256K 不如 DeepSeek 的 1M。",
        "想展示「同一套代码换模型就能跑」时，用它和 DeepSeek 做对照很合适。",
        "https://help.aliyun.com/zh/model-studio/model-pricing",
    ),

    # ── 智谱 GLM ────────────────────────────────────────────────
    _m(
        "glm-5.3-flash", "智谱 GLM-5.3 Flash", "zhipu", "glm-5.3-flash",
        "1M", 0.8, 2.8,
        "2026-09 官网价：输入 ¥0.8 / 输出 ¥2.8 / 缓存命中 ¥0.23；"
        "有活动期五折（输入 ¥0.4、输出 ¥1.4）；缓存存储限时免费",
        ["性价比", "原生多模态", "1M 上下文", "开源可商用"],
        "320B-A18B 的 MoE，原生多模态（图片 / 视频 / 文件 / 文本），"
        "视觉编码直接融进 Coding 循环里 —— 能自己看渲染结果再改。"
        "MIT 协议开源、跑在国产芯片上，输入价只要 ¥0.8，是这份表里最便宜的第一梯队模型。",
        "输出 ¥2.8 仍比 DeepSeek V4.1 Flash 空闲档贵；"
        "官方也明说 GLM-5.3 与 5.3-Flash **都不在免费清单里**（免费的是 4.x 那一代）。",
        "想要「1M 上下文 + 多模态 + 便宜」的国产替代时首选；"
        "还能本地 Ollama 拉量化版零成本跑。",
        "https://docs.bigmodel.cn/cn/guide/start/pricing",
        recommend=True,
    ),
    _m(
        "glm-5.3-flashx", "智谱 GLM-5.3 FlashX", "zhipu", "glm-5.3-flashx",
        "1M", 2.0, 7.0,
        "2026-09-18 上线，官网价：输入 ¥2 / 输出 ¥7 / 缓存命中 ¥0.57",
        ["更快", "200 tokens/s", "原生多模态"],
        "和 5.3-Flash 同底子，主推**速度**（最高 200 tokens/s）。"
        "批量或交互式场景里体感差别很明显。",
        "价格是 5.3-Flash 的 2.5 倍 —— 为速度付费，不是为智能付费。",
        "用户会盯着屏幕等结果时用它（比如群聊里逐字回显）。",
        "https://docs.bigmodel.cn/cn/guide/start/pricing",
    ),
    _m(
        "glm-5.3", "智谱 GLM-5.3（旗舰）", "zhipu", "glm-5.3",
        "1M", 8.0, 28.0,
        "2026-09 官网价：输入 ¥8 / 输出 ¥28 / 缓存命中 ¥2",
        ["旗舰", "1M 上下文", "长程任务", "智能体"],
        "智谱现役旗舰，长程任务和智能体能力的上限最高，中文技术表达也最稳。",
        "输出 ¥28/百万，是 GLM-5.3-Flash 的十倍、DeepSeek 空闲档的七倍。",
        "只在「便宜模型反复做不对、需要更强规划」时切过来救场，别当默认。",
        "https://docs.bigmodel.cn/cn/guide/start/pricing",
    ),
    _m(
        "glm-4.7-flash", "智谱 GLM-4.7 Flash（免费档）", "zhipu", "glm-4.7-flash",
        "200K", 0.0, 0.0,
        "官方标注免费；以官网为准",
        ["免费", "开发调试", "200K 上下文"],
        "**完全免费**的一档，还带 200K 上下文，跑通流程、验证工具调用链路都不心疼。",
        "能力明显弱于 5.x：复杂代码任务、多步规划容易断。免费档通常还有速率限制。",
        "开发阶段反复调试验证闸门 / 事件流时用它，把调试成本降到 0；"
        "正式演示前再切回 DeepSeek。",
        "https://docs.bigmodel.cn/cn/guide/start/pricing",
    ),

    # ── 腾讯混元 ────────────────────────────────────────────────
    _m(
        "hunyuan-hy4-preview", "腾讯混元 Hy4 preview", "tencent",
        "hunyuan-hy4-preview",
        "1M", 6.0, 18.0,
        "2026-08-28 发布时的官方价：输入 ¥6 / 输出 ¥18 / 缓存命中 ¥0.3（每百万 tokens）。"
        "⚠ 若报 model not found，请到控制台确认当前 model id 再填",
        ["旗舰", "1M 上下文", "开源", "腾讯生态"],
        "770B 总参 / 49B 激活，上下文突破 1M。腾讯内部 163 名专家做 203 个工程任务盲测，"
        "均分 2.99/4，略胜 GLM 5.3（2.92）和 Kimi K3（2.94）。已开源。",
        "价格是这份表里最贵的档之一（输入 ¥6 / 输出 ¥18）；"
        "官方自己也承认有「长思考过度、自我验证过多」的倾向。"
        "另外接口默认只有 5 个并发，Agent 并行时容易撞限流。",
        "要看国产旗舰的上限、或者项目本来就在腾讯生态里时用它做对照。",
        "https://hy.tencent.com/research/hy4-preview",
    ),
    _m(
        "hunyuan-turbos-latest", "腾讯混元 TurboS", "tencent",
        "hunyuan-turbos-latest",
        "256K", None, None,
        "以腾讯云控制台当日报价为准（本条不猜数字）",
        ["稳定", "低延迟"],
        "混元系列里工程上最稳的一档，低延迟，适合做默认跑量的模型。",
        "智能上限不如 Hy4 preview；具体价格请以控制台为准。",
        "想低成本试腾讯这条线时先用它——确认 key 通了、再决定要不要上 Hy4。",
        "https://cloud.tencent.com/document/product/1729/104753",
    ),

    # ── Kimi ───────────────────────────────────────────────────
    _m(
        "kimi-k2.6", "月之暗面 Kimi K2.6", "moonshot", "kimi-k2.6",
        "256K", 6.5, 27.0,
        "2026-07 官方价；缓存命中 ¥1.10",
        ["长上下文", "工具调用", "代码"],
        "K2 系列以长上下文和 Agent 工具调用见长，256K 上下文处理长文档、长代码库更从容。",
        "价格不便宜，且 256K 上下文不如 DeepSeek 的 1M。",
        "当任务需要把很长的文件整个塞进上下文（读大仓库、审长文档）时值得切过来。",
        "https://platform.kimi.com/docs/pricing/chat",
    ),
    _m(
        "kimi-k2.7-code", "月之暗面 Kimi K2.7 Code", "moonshot", "kimi-k2.7-code",
        "256K", 6.5, 27.0,
        "2026-07 官方价；有高速版",
        ["代码专项", "高速版"],
        "针对代码优化过的版本，代码生成 / 修复的命中率更高，还有 260 tokens/s 的高速版。",
        "通用对话和中文写作不如通用模型；价格与 K2.6 相同。",
        "程序员的活儿特别重（大量改代码、修 bug）时用它，比通用模型稳。",
        "https://platform.kimi.com/docs/pricing/chat",
    ),
]

# 自定义条目：上面的表格覆盖不到的长尾（硅基流动、火山引擎豆包、
# OpenAI、本地 Ollama……）都走这里 —— 只要它兼容 OpenAI 协议就能接。
CUSTOM_ID = "custom"

_BY_ID = {m["id"]: m for m in MODELS}


def get_model(model_id: str) -> dict | None:
    """按 id 取模型；``custom`` 返回一个模板（真实参数由用户填）。"""
    if model_id == CUSTOM_ID:
        return {
            "id": CUSTOM_ID,
            "label": "自定义（任何 OpenAI 兼容端点）",
            "vendor": CUSTOM_ID,
            "model": "",
            "context": "—",
            "price_in": None,
            "price_out": None,
            "currency": _CURRENCY,
            "price_unit": _UNIT,
            "price_note": "由使用者自行填写",
            "price_src": "",
            "tags": ["自定义", "OpenAI 兼容"],
            "strengths": "任何兼容 OpenAI 协议的端点都能接：硅基流动、火山引擎豆包、"
                         "OpenAI、Claude 网关、本地 Ollama / vLLM 都行。",
            "weakness": "价格和上下文要自己确认，本项目不做校验。",
            "best_for": "想接一个上面没列出来的服务，或者接自己部署的模型。",
            "recommend": False,
        }
    return _BY_ID.get(model_id)


def list_models() -> list[dict]:
    return list(MODELS)


def vendor_of(model_id: str) -> dict | None:
    m = get_model(model_id)
    if not m:
        return None
    return VENDORS.get(m["vendor"])


def default_model_id() -> str:
    for m in MODELS:
        if m.get("recommend"):
            return m["id"]
    return MODELS[0]["id"] if MODELS else CUSTOM_ID


def resolved_endpoint(model_id: str) -> dict:
    """把一条模型记录解析成真正可用的连接三件套。

    ``base_url`` 挂的是**厂商**而不是模型 —— 同一家的模型共用一个端点，
    写两遍只会有一份是错的。所以模型记录里不存 base_url，
    统一在这里从厂商表取，避免"改了厂商端点却漏改某个模型"。

    Returns:
        {"base_url": str, "model": str, "vendor": str, "label": str}
    """
    entry = get_model(model_id) or {}
    vendor_id = entry.get("vendor", "")
    vendor = VENDORS.get(vendor_id) or {}

    base_url = vendor.get("base_url", "")
    model_name = entry.get("model", "")

    # 自定义端点没有厂商表，参数来自用户填写
    if model_id == CUSTOM_ID:
        try:
            from .settings import get_custom

            custom = get_custom()
            base_url = custom.get("base_url", "") or base_url
            model_name = custom.get("model", "") or model_name
        except Exception:
            pass

    return {
        "base_url": (base_url or "").rstrip("/"),
        "model": model_name or "",
        "vendor": vendor_id,
        "label": entry.get("label", model_id),
    }
