"""ASR 引擎清单（单一真源，2026-10-04）。

## 为什么要抽出来

之前引擎列表在**两个地方各写了一份**，而且已经漂移了：

- `settings_dialog` 的「默认 ASR 引擎」下拉：有 funasr / whisper / qwen_asr /
  xf_asr / custom_post（5 个）
- `common_options.AsrOptions` 的「覆盖本次 ASR」下拉：只有 funasr / whisper（2 个）

结果是：fork 新增的三个引擎在「覆盖本次 ASR」里**根本选不到**，
用户只能去设置里改全局默认——而「覆盖本次」本来的意义就是**只改这一条不���全局**。

所以两份列表必须同源。新增引擎时只改这里。
"""

from __future__ import annotations

# (显示名, 引擎 ID) —— 顺序即下拉顺序
ASR_ENGINE_LABELS: list[tuple[str, str]] = [
    ("FunASR（本地）", "funasr"),
    ("Whisper（本地）", "whisper"),
    ("Qwen3-ASR（本地）", "qwen_asr"),
    ("xf_asr（讯飞听见 · 云端）", "xf_asr"),
    ("custom_post（自定义 POST · 云端）", "custom_post"),
]

# 需要「云端凭证」的引擎——在 GUI 里做文案提示用（它们在设置里才有 Key/URL）
CLOUD_ENGINES = frozenset({"xf_asr", "custom_post"})

# 支持「说话人分离」的引擎（其余引擎没有该能力）
SPEAKER_CAPABLE_ENGINES = frozenset({"funasr", "xf_asr", "custom_post"})

# 支持「时间戳输出」的引擎
TIMESTAMP_CAPABLE_ENGINES = frozenset({"funasr", "xf_asr", "custom_post"})

# 旧名兼容：config 里可能还留着已改名的引擎 ID
LEGACY_ENGINE_ALIASES = {
    "minimax_asr": "custom_post",
}


def normalize_engine(engine: str) -> str:
    """把引擎 ID 归一（处理旧别名）。未知值原样返回。"""
    e = str(engine or "").strip()
    return LEGACY_ENGINE_ALIASES.get(e, e)


def engine_label(engine: str) -> str:
    """引擎 ID → 显示名；未知返回原值。"""
    e = normalize_engine(engine)
    for label, code in ASR_ENGINE_LABELS:
        if code == e:
            return label
    return str(engine or "")
