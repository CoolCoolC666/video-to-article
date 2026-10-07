"""批量转写 MCP 工具的冒烟测试（2026-10-07，P0 阶段）。

重点盯三件事：
  1. **密钥绝不出现在返回值里**（这是 MCP 工具最危险的回归方向）
  2. **调用不得污染磁盘上的 active_profile**（用户 GUI 里的选择不能被 Agent 弄乱）
  3. 警告要能命中「跑之前就该知道」的那几类坑

跑法（必须从仓库根）：
    .venv\\Scripts\\python.exe tests\\smoke_mcp_tools.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import video_to_article.mcp_tools.server as S
from video_to_article.mcp_tools.capabilities import (
    DASHSCOPE_ASYNC_MODELS,
    ENGINE_CAPS,
    describe_effective,
)
from video_to_article.mcp_tools.config_view import (
    ACTIVE_SUFFIX,
    apply_overrides,
    effective_config,
    list_profiles,
    load_config_file,
    masked_config,
    resolve_profiles,
)

TMP = Path(tempfile.gettempdir())


def _fake_config() -> dict:
    return {
        "llm": {
            "vendor": "deepseek", "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash", "api_key": "sk-SECRET-KEY-abcdef0123456789",
            "max_tokens": 520000,
            "active_profile": "llmA",
            "profiles": [
                {"id": "llmA", "label": "Deepseek（调）", "vendor": "deepseek",
                 "model": "deepseek-flash", "base_url": "https://api.deepseek.com",
                 "api_key": "sk-SECRET-KEY-abcdef0123456789"},
                {"id": "llmB", "label": "Minimax", "vendor": "minimax",
                 "model": "MiniMax-M3", "api_key": "sk-OTHER-SECRET-9999"},
            ],
        },
        "transcribe": {
            "asr_engine": "custom_post", "model_size": "base",
            "custom_post": {
                "api_style": "dashscope_async", "model": "paraformer-v2",
                "api_key": "sk-CP-SECRET-777",
                "profiles": [
                    {"id": "cpA", "label": "DashScope", "model": "qwen3-asr-flash-filetrans"},
                ],
                "active_profile": "cpA",
            },
        },
        "image_host": {
            "enable": True, "provider": "r2",
            "api_url": "https://acct.r2.cloudflarestorage.com",
            "access_key_id": "AKIA-SECRET", "access_key_secret": "shhh",
        },
    }


# ---------------------------------------------------------------- 1. 脱敏
def test_secrets_masked():
    cfg = _fake_config()
    blob = json.dumps(masked_config(cfg), ensure_ascii=False)
    for secret in ("sk-SECRET-KEY-abcdef0123456789", "sk-OTHER-SECRET-9999",
                   "sk-CP-SECRET-777", "AKIA-SECRET", "shhh"):
        assert secret not in blob, f"❌ 密钥泄漏到返回体: {secret[:14]}…"
    assert "sk-SECRET" not in blob
    # 非敏感字段要保留
    assert "deepseek-flash" in blob and "r2" in blob
    # 档案列表也要脱敏
    profs = list_profiles(cfg)
    pblob = json.dumps(profs, ensure_ascii=False)
    assert "sk-SECRET-KEY-abcdef0123456789" not in pblob
    assert profs[f"llm{ACTIVE_SUFFIX}"] == "llmA", profs.get(f"llm{ACTIVE_SUFFIX}")
    assert set(profs["llm"][0]) == {"id", "label", "vendor", "model",
                                   "endpoint", "api_key"}, profs["llm"][0]
    print("OK 1: 密钥全部脱敏（含档案列表），非敏感字段保留\n")


# ------------------------------------------------- 2. 档案解析不污染磁盘
def test_profile_resolve_is_in_memory_only():
    cfg = _fake_config()
    before = json.dumps(cfg, sort_keys=True)

    out = resolve_profiles(cfg, {"llm": "Minimax", "asr": "cpA"})
    # 激活的档案被换成 MiniMax
    assert out["llm"]["model"] == "MiniMax-M3", out["llm"]["model"]
    assert out["llm"]["active_profile"] == "llmB"
    # ⚠ 原始 config 必须一字未动 —— 这就是「Agent 不弄乱你 GUI 选择」的落点
    assert json.dumps(cfg, sort_keys=True) == before, "原 config 被改了！"
    # 档案列表本身要保住
    assert isinstance(out["llm"]["profiles"], list) and len(out["llm"]["profiles"]) == 2
    assert isinstance(out["transcribe"]["custom_post"]["profiles"], list)

    # 未知档案要报错并列出可用项（别静默回落）
    try:
        resolve_profiles(cfg, {"llm": "不存在的档案"})
        raise SystemExit("未知档案应该抛错")
    except RuntimeError as e:
        assert "没有找到" in str(e) and "Minimax" in str(e), str(e)
    print("OK 2: 档案激活只在内存副本上，原 config 一字未改；未知档案明确报错\n")


# ------------------------------------------------- 3. overrides 只影响本次
def test_overrides():
    cfg = _fake_config()
    # ⚠ 别用 effective_config(None, ...) —— 那会读**真实**的 config.json，
    #   测试就成了在验证真实配置而不是假数据。直接用两个纯函数组合。
    out = apply_overrides(
        resolve_profiles(cfg, {"llm": "Minimax"}),
        {"asr_engine": "funasr", "model": "whisper-base"},
    )
    assert out["llm"]["model"] == "MiniMax-M3"
    assert out["transcribe"]["asr_engine"] == "funasr"
    assert out["transcribe"]["custom_post"]["model"] == "whisper-base"
    # 原 config 没被改
    assert cfg["transcribe"]["asr_engine"] == "custom_post"
    assert cfg["llm"]["model"] == "deepseek-flash"
    print("OK 3: overrides 仅本次生效，原 config 不受影响\n")


# ------------------------------------------------------ 4. 引擎能力 / 模型名
def test_engine_caps():
    for eid in ("funasr", "qwen_asr", "whisper"):
        assert ENGINE_CAPS[eid]["max_concurrency"] == 1, eid
        assert ENGINE_CAPS[eid]["kind"] == "local"
    assert ENGINE_CAPS["xf_asr"]["max_concurrency"] == 2
    assert ENGINE_CAPS["custom_post"]["max_concurrency"] == 2
    assert "qwen3-asr-flash" not in DASHSCOPE_ASYNC_MODELS, "同步模型不能进异步清单"
    assert all(m.endswith("filetrans") or m in ("fun-asr", "paraformer-v2")
               for m in DASHSCOPE_ASYNC_MODELS), DASHSCOPE_ASYNC_MODELS

    # ⚠ custom_post 的模型名取 custom_post.model，**不是** model_size
    #   （model_size 是 whisper 的 tiny/base/small，取错会得到 'base'）
    eff = describe_effective(_fake_config())
    assert eff["asr"]["model"] == "paraformer-v2", eff["asr"]["model"]
    assert eff["asr"]["api_style"] == "dashscope_async"
    assert eff["asr"]["needs_public_audio_url"] is True
    assert eff["llm_concurrency"] == 1, "LLM 阶段必须恒为 1"
    print("OK 4: 引擎并发规则 / DashScope 模型清单 / 模型名取对字段\n")


# ---------------------------------------------------------- 5. 跑前警告
def test_plan_warnings():
    cfg = _fake_config()
    # a) max_tokens 超上限
    warns = S._plan_warnings(cfg, describe_effective(cfg)["asr"], 3)
    assert any("max_tokens" in w for w in warns), warns

    # b) max_tokens 正常时不该再报
    cfg2 = _fake_config()
    cfg2["llm"]["max_tokens"] = 320000
    w2 = S._plan_warnings(cfg2, describe_effective(cfg2)["asr"], 3)
    assert not any("max_tokens" in w for w in w2), w2

    # c) 同步模型名打异步端点
    cfg3 = _fake_config()
    cfg3["transcribe"]["custom_post"]["model"] = "qwen3-asr-flash"
    w3 = S._plan_warnings(cfg3, describe_effective(cfg3)["asr"], 3)
    assert any("filetrans" in w for w in w3), w3

    # d) 图床既不公网可读也没签名直链
    cfg4 = _fake_config()
    cfg4["transcribe"]["custom_post"]["audio_url"] = ""
    w4 = S._plan_warnings(cfg4, describe_effective(cfg4)["asr"], 3)
    assert any("FILE_DOWNLOAD_FAILED" in w or "public_base_url" in w for w in w4), w4

    # e) planned=0 要说清楚
    assert any("没有任何待处理项" in w for w in S._plan_warnings(cfg, {}, 0))

    # f) source 在 data/ 下（已抽取音频）要警告 —— 本轮真实踩过
    dw = S._source_warnings("data/local/1-课件", None)
    assert dw and "音频" in dw[0], dw
    assert not S._source_warnings("/some/视频目录", None)
    print("OK 5: 跑前警告覆盖 max_tokens/同步模型/图床不可读/源选错\n")


# ------------------------------------------------- 6. 工具只读 + 出参形状
def test_tools_shapes():
    listed = S.config_list()
    assert "configs" in listed
    for cf in listed["configs"]:
        assert "profiles" in cf and "active" in cf
        blob = json.dumps(cf, ensure_ascii=False)
        assert "sk-" not in blob or "***" in blob or "<" in blob, "config_list 疑似漏脱敏"

    caps = S.engine_capabilities()
    assert len(caps["engines"]) == len(ENGINE_CAPS)
    assert caps["dashscope_async_models"] == DASHSCOPE_ASYNC_MODELS

    d = S.config_describe()
    assert d["asr_chain"]["id"] == "custom_post"
    assert d["llm_concurrency"] == 1
    blob = json.dumps(d, ensure_ascii=False)
    for s in ("sk-SECRET", "AKIA-SECRET"):
        assert s not in blob, f"config_describe 泄漏 {s}"
    print("OK 6: 三个只读工具出参形状正确且无密钥泄漏\n")


def main():
    test_secrets_masked()
    test_profile_resolve_is_in_memory_only()
    test_overrides()
    test_engine_caps()
    test_plan_warnings()
    test_tools_shapes()
    print("=" * 56)
    print("ALL mcp-tools smoke tests passed ✓")
    print("=" * 56)


if __name__ == "__main__":
    main()