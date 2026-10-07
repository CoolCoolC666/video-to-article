"""配置的只读视图：解析、脱敏、能力判定。

**本模块只读** —— 任何函数都不会写 config.json。
Agent 帮你跑一次任务不该把你 GUI 里的当前选择弄乱，所以
「临时指定档案」只作用在内存副本上；永久切换是 `config_apply` 的事（P2）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..paths import APP_ROOT
from ..providers.llm_providers import mask_secret

# 档案在 config 里的四类位置（与 settings_dialog 的 PROFILES 对齐）
PROFILE_KINDS = {
    "llm": ("llm",),
    "asr": ("transcribe", "custom_post"),
    "cover": ("ai_cover",),
    "host": ("image_host",),
}

# list_profiles 里用 "<kind>__active" 标记「当前激活哪个档案」
ACTIVE_SUFFIX = "__active"

SECRET_FIELDS = frozenset({
    "api_key", "token", "secret", "password", "access_key_secret",
    "access_key_id", "bearer", "auth", "cookie", "cookies",
})


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------
def _mask_value(key: str, value: Any) -> Any:
    """递归脱敏：键名命中敏感词就只留长度；token 这类半掩码。"""
    lk = str(key).lower()
    if lk in SECRET_FIELDS or any(s in lk for s in ("token", "secret", "password", "cookie")):
        if not value:
            return ""
        if isinstance(value, str):
            return mask_secret(value) or f"<{len(value)} 字符>"
        return "<已设置>"
    if isinstance(value, dict):
        return {k: _mask_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(key, v) for v in value]
    return value


def masked_config(cfg: dict) -> dict:
    """整份配置的脱敏副本（给 config_list / config_describe 用）。"""
    return _mask_value("", cfg or {})


# --------------------------------------------------------------------------
# 载入
# --------------------------------------------------------------------------
def default_config_path() -> Path:
    return Path(APP_ROOT) / "config.json"


def configs_dir() -> Path:
    return Path(APP_ROOT) / "configs"


def load_config_file(path: Optional[str] = None) -> Dict[str, Any]:
    """载入一份配置。省略 path = 用程序默认的 config.json。

    读不到就抛 RuntimeError —— 调用方据此给用户可读提示，
    绝不能悄悄退回默认配置（那会让 Agent 用错配置去花钱）。
    """
    p = Path(path) if path else default_config_path()
    if not p.exists():
        hint = ""
        if path is None and configs_dir().exists():
            hint = f"（可选配置文件目录：{configs_dir()}）"
        raise RuntimeError(f"配置文件不存在: {p}{hint}")
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"配置文件不是合法 JSON: {p} — {e}") from e


def list_config_files() -> List[Dict[str, Any]]:
    """列出可用配置文件（**不读内容**，只报存在性与大小）。"""
    out: List[Dict[str, Any]] = []
    default = default_config_path()
    if default.exists():
        out.append({
            "id": "<default>",
            "path": str(default),
            "is_default": True,
            "size_bytes": default.stat().st_size,
        })
    d = configs_dir()
    if d.exists():
        for f in sorted(d.glob("*.json")):
            out.append({
                "id": f.stem,
                "path": str(f),
                "is_default": False,
                "size_bytes": f.stat().st_size,
            })
    return out


# --------------------------------------------------------------------------
# 档案
# --------------------------------------------------------------------------
def _get(cfg: dict, path: tuple) -> dict:
    node: Any = cfg
    for k in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(k)
    return node if isinstance(node, dict) else {}


def list_profiles(cfg: dict) -> Dict[str, List[Dict[str, Any]]]:
    """列出四类档案（密钥已脱敏）。"""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for kind, path in PROFILE_KINDS.items():
        block = _get(cfg, path)
        raw = block.get("profiles")
        items: List[Dict[str, Any]] = []
        # ⚠ profiles 必须是 list：存成 dict 会出现删不掉的「幽灵档案」
        if isinstance(raw, list):
            for p in raw:
                if not isinstance(p, dict):
                    continue
                items.append({
                    "id": p.get("id") or "",
                    "label": p.get("label") or p.get("id") or "",
                    "vendor": p.get("vendor") or "",
                    "model": p.get("model") or "",
                    "endpoint": _mask_value("endpoint", p.get("endpoint") or p.get("base_url") or ""),
                    "api_key": _mask_value("api_key", p.get("api_key") or ""),
                })
        out[kind] = items
        out[f"{kind}{ACTIVE_SUFFIX}"] = block.get("active_profile") or ""
    return out


def resolve_profiles(cfg: dict, wanted: Optional[Dict[str, str]] = None) -> dict:
    """在**内存副本**上激活指定档案。

    ⚠ 绝不回写磁盘 —— 这正是「调用不得污染 GUI 当前选择」的落点。
    `wanted` 形如 `{"llm": "llm3_xxx", "asr": "cp3_yyy"}`；
    值既可以是档案 id，也可以是 label（label 重复时取第一个并记警告）。
    """
    out = copy.deepcopy(cfg or {})
    if not wanted:
        return out
    for kind, path in PROFILE_KINDS.items():
        target = (wanted.get(kind) or "").strip()
        if not target:
            continue
        block = _get(out, path)
        raw = block.get("profiles")
        if not isinstance(raw, list):
            continue
        hit = None
        for p in raw:
            if not isinstance(p, dict):
                continue
            if p.get("id") == target or p.get("label") == target:
                hit = p
                break
        if hit is None:
            known = [str(p.get("label") or p.get("id")) for p in raw if isinstance(p, dict)]
            raise RuntimeError(
                f"没有找到 {kind} 档案 {target!r}。可用：{known or '（该类暂无档案）'}"
            )
        merged = {**hit}
        merged.pop("models_cache", None)
        block.clear()
        block.update(merged)
        block["profiles"] = raw  # 保住档案列表本身
        block["active_profile"] = hit.get("id") or hit.get("label")
    return out


def apply_overrides(cfg: dict, overrides: Optional[dict]) -> dict:
    """仅对本次调用生效的临时覆盖（如 asr_engine / prompts）。"""
    out = copy.deepcopy(cfg or {})
    if not overrides:
        return out
    tr = out.setdefault("transcribe", {})
    if "asr_engine" in overrides and overrides["asr_engine"]:
        tr["asr_engine"] = overrides["asr_engine"]
    if "model_size" in overrides and overrides["model_size"]:
        tr["model_size"] = overrides["model_size"]
    cp = tr.setdefault("custom_post", {})
    if "model" in overrides and overrides["model"]:
        cp["model"] = overrides["model"]
    return out


def effective_config(
    config_file: Optional[str] = None,
    profiles: Optional[Dict[str, str]] = None,
    overrides: Optional[dict] = None,
) -> Dict[str, Any]:
    """配置文件 → 激活档案 → 临时覆盖，逐层生效。全程内存副本。"""
    cfg = load_config_file(config_file)
    cfg = resolve_profiles(cfg, profiles)
    cfg = apply_overrides(cfg, overrides)
    return cfg