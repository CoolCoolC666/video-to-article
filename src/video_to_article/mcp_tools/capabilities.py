"""引擎能力边界（供 Agent 在跑之前判断，而不是跑一半才炸）。

## 为什么要有这张表

这轮调试里，用户连续踩了两次「跑之前就该知道、结果跑到一半才暴露」的问题：
① 图床地址还是模板占位符 → 报成看不懂的 DNS 故障
② max_tokens 超上限 → 400 发生在**请求发出前**，界面上只剩「成稿没生成」

所以这里把「某个引擎要什么前置条件、能不能并发」写死成结构化数据，
让 `transcribe_plan` 能在**跑之前**说出来。

## 并发结论的由来

- 本地引擎（funasr / qwen_asr / whisper）**一律锁 1**：GPU 显存独占或同机
  CPU 线程互抢，并发只会互相拖慢甚至 OOM。
- 云端引擎给 2：上传 + 等待是 IO 密集，能吃满等待时间；但仍有 RPM，
  再高容易触发限速反而更慢。
- **LLM 整理阶段恒为 1**（见 server.py 的信号量设计）：
  不是技术上不能，而是代价大于收益 ——
  触发限速（实测 MiniMax 429 Token Plan 用量上限）、
  单条长文生成就要 40+ 秒（并发不更快，只会让所有请求都慢到超时）、
  失败重试的成本成倍放大，而整条流水线的耗时大头在 ASR 不在 LLM。
"""

from __future__ import annotations

from typing import Any, Dict, List

# ASR 引擎：能力 + 并发上限 + 前置条件
ENGINE_CAPS: Dict[str, Dict[str, Any]] = {
    "funasr": {
        "label": "FunASR（本地）",
        "kind": "local",
        "max_concurrency": 1,
        "needs_public_audio_url": False,
        "needs_image_host": False,
        "supports_speaker_diarization": True,
        "notes": [
            "本地推理，GPU 显存独占，**不可并发**",
            "说话人分离需挂 CAM++ 说话人模型（约 28MB）",
            "本机切段，无时长上限",
        ],
        "models": ["paraformer-zh", "paraformer-zh-streaming", "SenseVoiceSmall"],
    },
    "qwen_asr": {
        "label": "Qwen3-ASR（本地）",
        "kind": "local",
        "max_concurrency": 1,
        "needs_public_audio_url": False,
        "needs_image_host": False,
        "supports_speaker_diarization": False,
        "notes": [
            "本地推理，**不可并发**",
            ">7.5 分钟自动切 5 分钟一段（防 KV cache 撑爆）",
            "⚠ **不支持说话人分离**",
        ],
        "models": ["Qwen3-ASR-1.7B", "Qwen3-ASR-0.6B"],
    },
    "whisper": {
        "label": "faster-whisper（本地）",
        "kind": "local",
        "max_concurrency": 1,
        "needs_public_audio_url": False,
        "needs_image_host": False,
        "supports_speaker_diarization": False,
        "notes": ["本地推理，CPU 线程互抢，**不可并发**"],
        "models": ["tiny", "base", "small"],
    },
    "xf_asr": {
        "label": "讯飞听见（云端）",
        "kind": "cloud",
        "max_concurrency": 2,
        "needs_public_audio_url": False,
        "needs_image_host": False,
        "supports_speaker_diarization": True,
        "notes": [
            "单文件上限 **5 小时 / 500MB**，**不切段**",
            "支持云端 roleType 说话人分离",
            "有 RPM 限制，并发放大有限",
        ],
        "models": ["（讯飞侧固定）"],
    },
    "custom_post": {
        "label": "自定义 POST（云端/自建）",
        "kind": "cloud",
        "max_concurrency": 2,
        "needs_public_audio_url": False,  # 取决于 api_style，动态判定
        "needs_image_host": False,       # 同上
        "supports_speaker_diarization": True,
        "notes": [
            "两类协议完全不同，**必须在 GUI 里选 API 风格**：",
            "",
            "① OpenAI 兼容：multipart 上传文件本体 → 一次请求直接返回",
            "   上限 500 秒 / 50MB，超了自动切 450 秒一段",
            "   需要：端点（只填到 /v1）+ API Key 或请求头文件",
            "",
            "② DashScope 异步：application/json，**音频必须是公网 URL**",
            "   流程：借图床换 URL → 提交 → 轮询 → 下载结果 JSON",
            "   上限 12 小时 / 2GB，基本不用切段",
            "   需要：图床（r2 / oss / easyimage 之一）+ **异步模型名**",
            "",
            "⚠ 填错风格会直接被重置连接（ConnectionResetError 10054），不返回可读错误",
            "⚠ DashScope 的同步模型（如 qwen3-asr-flash）打异步端点会报",
            "   'current user api does not support asynchronous calls'，",
            "   必须用 -filetrans 结尾的异步模型名",
        ],
        "models": [],
    },
}

# DashScope 异步风格下真正可用的模型（官方会更新版本号，以服务商文档为准）
DASHSCOPE_ASYNC_MODELS = [
    "qwen-audio-3.0-asr-flash-filetrans",
    "qwen3-asr-flash-filetrans",
    "fun-asr",
    "paraformer-v2",
]
# 只支持同步、打异步端点会被拒的模型
DASHSCOPE_SYNC_ONLY = [
    "qwen-audio-3.0-asr-flash",
    "qwen3-asr-flash",
    "fun-asr-flash",
]

IMAGE_HOST_PROVIDERS = {
    "easyimage": "传统 multipart 图床（要 api_url + token）",
    "r2": "Cloudflare R2（S3 兼容，**出网流量永久免费**；需公可读或签名直链）",
    "oss": "阿里云 OSS（S3 兼容，与百炼同云时拉取最快；需公可读或签名直链）",
}


def all_engines() -> List[Dict[str, Any]]:
    return [{"id": k, **{kk: vv for kk, vv in v.items()}} for k, v in ENGINE_CAPS.items()]


def engine_cap(engine: str) -> Dict[str, Any]:
    return ENGINE_CAPS.get(engine, {})


def describe_effective(cfg: dict) -> Dict[str, Any]:
    """从配置里读出「当前实际会用到的引擎链」，并标注其前置条件。"""
    tr = (cfg or {}).get("transcribe") or {}
    engine = str(tr.get("asr_engine") or "funasr")
    cap = dict(engine_cap(engine))
    if engine == "custom_post":
        cp = tr.get("custom_post") or {}
        style = str(cp.get("api_style") or "openai_compat")
        cap["api_style"] = style
        if style == "dashscope_async":
            cap["needs_public_audio_url"] = True
            cap["needs_image_host"] = not bool(cp.get("audio_url"))
            cap["models"] = DASHSCOPE_ASYNC_MODELS
    model = ""
    if engine == "custom_post":
        # ⚠ model_size 是 whisper 的档位（tiny/base/small），
        #   对 custom_post **不是** ASR 模型名 —— 取错字段会得到 'base'
        #   这种毫无意义的值，进而让「模型不在异步清单」的警告乱报。
        model = str((tr.get("custom_post") or {}).get("model") or "")
    else:
        model = str(tr.get("model_size") or "")
    llm = (cfg or {}).get("llm") or {}
    llm_info = {
        "vendor": llm.get("vendor") or "",
        "model": llm.get("model") or "",
        "base_url": llm.get("base_url") or "",
        "max_tokens": llm.get("max_tokens"),
    }
    return {
        "asr": {"id": engine, "model": model, **cap},
        "llm": llm_info,
        "llm_concurrency": 1,
        "note": "ASR 可小并发（见 max_concurrency），**LLM 整理阶段恒为 1**",
    }