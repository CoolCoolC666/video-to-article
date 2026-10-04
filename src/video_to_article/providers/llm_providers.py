"""LLM 协议 / 厂商注册表 + 协议解析（2026-10-04）。

## 为什么要有这个模块

`config.llm.provider` 这个字段名有误导性：它**不是厂商名，是协议开关**。
`providers/llm.py` 里只有 `openai` / `anthropic` 两个分支，决定的是
「用哪个 SDK、发什么格式的请求」，跟厂商无关。

真实配置长这样：

    "provider": "openai",
    "base_url": "https://api.minimaxi.com/v1"

它的含义是「**用 OpenAI 兼容协议** 连 **MiniMax 这个厂商**」，
不是「厂商是 openai」。所以本模块把两个概念拆开：

| 概念 | 字段 | 决定什么 |
| --- | --- | --- |
| 协议 protocol | `llm.protocol` | 用哪个 SDK、什么鉴权头、什么 body |
| 厂商 vendor   | `llm.vendor`   | 预填 Base URL、日志标识、UI 分组、模型名提示 |

## 兼容性保证（2026-10-04 用户拍板：按原考虑，严格兼容）

旧配置**零改动继续能跑**。`resolve_protocol()` 的判定顺序：

1. 有 `protocol` 字段 → 直接用（新配置）
2. 没有 → 回落到旧 `provider` 字段
3. `provider == "anthropic"` → `anthropic`
4. 其他一切（含空 / 拼错 / 未来新增值）→ `openai_chat`

第 4 条是关键兜底：**旧代码里 `provider` 拼错会报「不支持的提供商」直接失败**
（`llm.py:49`），而绝大多数 OpenAI 兼容服务都该走 openai_chat。
这个改动顺带修掉了「provider 写个空格就整个转写挂掉」的问题。
"""
from __future__ import annotations

from typing import Dict, Optional

# ============ 协议 ============
# 只影响「用哪个 SDK / 什么请求格式」；加新协议要同时改 providers/llm.py
PROTOCOL_LABELS = {
    "openai_chat": "OpenAI 兼容（绝大多数云 / 中转都属此类）",
    "anthropic": "Anthropic 原生（Claude）",
}

# 旧 provider 值 → 新 protocol 的迁移映射
_LEGACY_PROVIDER_MAP = {
    "anthropic": "anthropic",
}


def resolve_protocol(llm_cfg: dict) -> str:
    """从 llm 配置块解析出协议。旧配置零改动可用。"""
    cfg = llm_cfg or {}
    explicit = str(cfg.get("protocol") or "").strip()
    if explicit in PROTOCOL_LABELS:
        return explicit
    legacy = str(cfg.get("provider") or "").strip().lower()
    return _LEGACY_PROVIDER_MAP.get(legacy, "openai_chat")


# ============ 厂商 ============
# 只影响 UI 预填与日志标识。base_url 一律可手改，预填不是强制。
# ⚠ 别在这里写「能力判断」——厂商嗅探逻辑（哪些参数要传/不传）应该
#   显式配置，而不是靠字符串匹配 base_url（那是 cover.py 现在的做法，本轮不动）。
VENDOR_REGISTRY: Dict[str, Dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimaxi.com/v1",
    },
    "deepseek": {
        "label": "DeepSeek",
        # 2026-10-04 核对官方 api-docs.deepseek.com：写的是**不带 /v1**。
        # 旧版文档曾用 /v1，服务端两个都收，但既然新文档明确不带，就跟着改。
        "base_url": "https://api.deepseek.com",
    },
    "qwen": {
        "label": "阿里通义千问（DashScope）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "moonshot": {
        "label": "Moonshot（Kimi）",
        "base_url": "https://api.moonshot.cn/v1",
    },
    "siliconflow": {
        "label": "硅基流动（SiliconFlow）",
        "base_url": "https://api.siliconflow.cn/v1",
    },
    "xai": {
        "label": "xAI（Grok）",
        "base_url": "https://api.x.ai/v1",
    },
    "anthropic": {
        "label": "Anthropic（Claude）",
        "base_url": "https://api.anthropic.com",
    },
    "custom": {
        "label": "自定义 / 其他（手填 Base URL）",
        "base_url": "",
    },
}

# base_url → vendor 的反向推断（UI 回显时用；猜不中就落 custom）
_BASE_URL_HINTS = [
    ("api.openai.com", "openai"),
    ("minimax", "minimax"),
    ("minimaxi", "minimax"),
    ("deepseek", "deepseek"),
    ("dashscope", "qwen"),
    ("moonshot", "moonshot"),
    ("siliconflow", "siliconflow"),
    ("api.x.ai", "xai"),
    ("api.anthropic.com", "anthropic"),
]


def guess_vendor_from_url(base_url: str) -> str:
    """从 base_url 反推厂商。猜不中返回 "custom"。

    ⚠ 这是**兜底推断**，不是权威判定。用户显式选了 vendor 就以用户为准。
    """
    u = str(base_url or "").lower()
    if not u:
        return "custom"
    for needle, vendor in _BASE_URL_HINTS:
        if needle in u:
            return vendor
    return "custom"


def vendor_base_url(vendor: str) -> str:
    """厂商对应的推荐 base_url；未知厂商返回空串。"""
    return VENDOR_REGISTRY.get(vendor or "", {}).get("base_url", "")


def vendor_label(vendor: str) -> str:
    """厂商显示名；未知厂商回显原值。"""
    entry = VENDOR_REGISTRY.get(vendor or "")
    return entry["label"] if entry else str(vendor or "")


def effective_vendor(llm_cfg: dict) -> str:
    """解析生效厂商：显式 vendor 优先，否则从 base_url 猜。"""
    cfg = llm_cfg or {}
    explicit = str(cfg.get("vendor") or "").strip()
    if explicit in VENDOR_REGISTRY:
        return explicit
    return guess_vendor_from_url(cfg.get("base_url", ""))


def mask_secret(secret: str, keep: int = 4) -> str:
    """密钥掩码，用于 UI 回显 / 日志。**永远不要把原值写进日志。**"""
    s = str(secret or "")
    if not s:
        return ""
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}***{s[-2:]}"
