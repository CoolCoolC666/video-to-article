"""Smoke tests for FunASR 本地说话人分离（2026-10-02 新增）。

Covers:
  1. resolve_funasr_spk_model_name() — CAM++ 别名解析 + 空串表示不分离
  2. format_funasr_speaker_text() — sentence_info → 【说话人N】+ [MM:SS]
  3. 官方两处 demo 键名不一致（sentence / text）都要读
  4. SenseVoice 富标签剥离
  5. 无 sentence_info / 空结果 → 退回空串（调用方据此走纯文本路径）
  6. processor._resolve_engine_config() 对 funasr 收 funasr_* 扁平键
  7. SettingsDialog 读写 transcribe.funasr_speaker / funasr_spk_model / funasr_timestamps

不下载模型、不跑 torch —— 全部是纯函数 + GUI 字段断言。
从仓库根运行：python tests\\smoke_funasr_speaker.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")


def test_spk_model_resolve():
    """1. CAM++ 模型名解析：别名归一 + 空串 = 不分离。

    FunASR 的 spk_model 要么是模型 ID（"cam++"，让 FunASR 自己下载），
    要么是本地快照绝对路径。空串表示「不传 spk_model」。
    """
    from video_to_article.media.audio import resolve_funasr_spk_model_name

    # 本机可能还没下 CAM++，那 resolve 会返回模型 ID 而不是路径
    # 两种结果都合法，只断言「不是 None / 不是空串 / 是 str」
    for alias in ("cam++", "CAM++", "camplus", "CampPlus", "camplus"):
        got = resolve_funasr_spk_model_name(alias)
        assert isinstance(got, str), f"{alias!r} 应返回 str，实得 {type(got)}"
        assert got, f"{alias!r} 不应返回空串（开了分离就必须有模型）"
        assert not os.path.exists(got) or os.path.isabs(got), (
            f"{alias!r} 若返回路径必须是绝对路径，实得 {got!r}"
        )

    # 空串 / 纯空白 = 明确表示不分离
    assert resolve_funasr_spk_model_name("") == "", "空串应表示不分离"
    assert resolve_funasr_spk_model_name("   ") == "", "空白应归一为不分离"
    print("OK 1: CAM++ 别名解析对（cam++/CAM++/campplus/camplus + 空串不分离）\n")


def test_speaker_text_format():
    """2. sentence_info → 【说话人N】+ [MM:SS]。

    FunASR + CAM++ 生效时，generate() 返回结果带 sentence_info，
    每项 {spk, start(ms), end(ms), text|sentence}。
    """
    from video_to_article.media.audio import format_funasr_speaker_text

    result = [
        {
            "sentence_info": [
                {"spk": 0, "start": 3200, "end": 7000, "text": "你好我是老师"},
                {"spk": 1, "start": 65000, "end": 80000, "text": "老师好我是学生"},
                {"spk": 0, "start": 120000, "end": 130000, "text": "我们开始上课吧"},
            ]
        }
    ]

    both = format_funasr_speaker_text(result, timestamps=True)
    lines = both.split("\n")
    assert len(lines) == 3, f"应 3 行，实得 {len(lines)}: {both!r}"
    # spk=0 → 说话人1, spk=1 → 说话人2（连续编号，不是直接印 0/1）
    assert lines[0] == "[00:03] 【说话人1】你好我是老师", f"第 1 行错: {lines[0]!r}"
    assert lines[1] == "[01:05] 【说话人2】老师好我是学生", f"第 2 行错: {lines[1]!r}"
    assert lines[2].startswith("[02:00] 【说话人1】"), f"第 3 行错: {lines[2]!r}"
    print(f"OK 2a: 说话人 + 时间戳 →\n{both}")

    # 只开时间戳
    only_ts = format_funasr_speaker_text(result, timestamps=False)
    assert only_ts == "【说话人1】你好我是老师\n【说话人2】老师好我是学生\n【说话人1】我们开始上课吧", (
        f"timestamps=False 时不该有时间戳，实得 {only_ts!r}"
    )
    print(f"OK 2b: timestamps=False → {only_ts!r}")

    # 3. 官方两处 demo 键名不一致 → sentence 和 text 都要读
    mixed_keys = [
        {
            "sentence_info": [
                {"spk": 0, "start": 0, "end": 1000, "sentence": "用 sentence 键的"},
                {"spk": 1, "start": 2000, "end": 3000, "text": "用 text 键的"},
            ]
        }
    ]
    mixed = format_funasr_speaker_text(mixed_keys, timestamps=False)
    assert mixed == "【说话人1】用 sentence 键的\n【说话人2】用 text 键的", (
        f"sentence/text 两个键名都应被读取，实得 {mixed!r}"
    )
    print(f"OK 3: 官方 demo 键名不一致（sentence / text）都读 → {mixed!r}")


def test_rich_tag_strip():
    """4. SenseVoice 富标签要剥掉，否则会污染说话人行的排版。"""
    from video_to_article.media.audio import _strip_rich_tags

    assert _strip_rich_tags("<|zh|><|NEUTRAL|><|Speech|><|withitn|>你好") == "你好"
    assert _strip_rich_tags("无标签文本") == "无标签文本"
    assert _strip_rich_tags("") == ""
    # 富标签出现在带说话人标签的行里也不能残留
    from video_to_article.media.audio import format_funasr_speaker_text

    tagged = [
        {
            "sentence_info": [
                {
                    "spk": 0,
                    "start": 1000,
                    "end": 2000,
                    "text": "<|zh|><|HAPPY|><|Speech|>带标签的句子",
                }
            ]
        }
    ]
    out = format_funasr_speaker_text(tagged, timestamps=True)
    assert "<|" not in out, f"输出里不该残留富标签，实得 {out!r}"
    assert out == "[00:01] 【说话人1】带标签的句子", f"实际输出 {out!r}"
    print(f"OK 4: SenseVoice 富标签已剥离 → {out!r}")


def test_fallback_paths():
    """5. 拿不到 sentence_info 时返回空串，调用方据此退回纯文本（不崩）。"""
    from video_to_article.media.audio import format_funasr_speaker_text

    # 根本没 sentence_info（CAM++ 没生效 / 老版本 FunASR）
    assert format_funasr_speaker_text([{"text": "纯文本结果"}]) == ""
    # sentence_info 是空 list
    assert format_funasr_speaker_text([{"sentence_info": []}]) == ""
    # sentence_info 存在但全是空文本
    assert format_funasr_speaker_text(
        [{"sentence_info": [{"spk": 0, "text": "  "}]}]
    ) == ""
    # 结果本身是空的 / 结构怪
    assert format_funasr_speaker_text([]) == ""
    assert format_funasr_speaker_text("not a list") == ""
    assert format_funasr_speaker_text([{"no_spk": 1}]) == ""
    print("OK 5: 无 sentence_info / 空结果 / 怪结构 → 全部返回空串不崩\n")


def test_resolve_engine_config():
    """6. processor._resolve_engine_config 对 funasr 收 funasr_* 扁平键。

    funasr 的配置是扁平的（不在子块里），跟 qwen_asr / xf_asr 不同，
    所以用前缀过滤而不是取某个块——漏一个键就等于功能没生效。
    """
    from video_to_article.processor import _resolve_engine_config

    config = {
        "transcribe": {
            "asr_engine": "funasr",
            "funasr_model": "sensevoice",
            "funasr_speaker": True,
            "funasr_spk_model": "cam++",
            "funasr_timestamps": True,
            "funasr_cache_dir": "E:/models/funasr",
            # 不该混进 funasr 配置块的其他引擎字段
            "model_size": "tiny",
            "cpu_threads": 4,
            "qwen_asr": {"model_id": "Qwen/Qwen3-ASR-1.7B"},
            "xf_asr": {"app_id": "x", "secret_key": "y"},
        }
    }
    got = _resolve_engine_config(config, "funasr")
    assert got is not None, "funasr 应该拿到配置块"
    assert got.get("funasr_speaker") is True, f"说话人开关没传过去: {got}"
    assert got.get("funasr_spk_model") == "cam++", f"说话人模型没传过去: {got}"
    assert got.get("funasr_timestamps") is True, f"时间戳开关没传过去: {got}"
    # 其他引擎的块 / 通用键都不该混进来
    assert "qwen_asr" not in got, f"不该混进 qwen_asr 块: {got}"
    assert "xf_asr" not in got, f"不该混进 xf_asr 块: {got}"
    assert "model_size" not in got, f"不该混进 whisper 专用的 model_size: {got}"
    print(f"OK 6a: funasr 配置块只收 funasr_* 扁平键 → {got}")

    # 其它引擎行为不变
    assert _resolve_engine_config(config, "qwen_asr") == {
        "model_id": "Qwen/Qwen3-ASR-1.7B"
    }
    assert _resolve_engine_config(config, "xf_asr") == {"app_id": "x", "secret_key": "y"}
    assert _resolve_engine_config(config, "whisper") is None
    assert _resolve_engine_config(None, "funasr") is None
    print("OK 6b: 其它引擎的 _resolve_engine_config 行为不变\n")


def test_settings_dialog_roundtrip():
    """7. SettingsDialog 读写 transcribe.funasr_* 三项 + 联动置灰。"""
    from PySide6.QtWidgets import QApplication
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article import config as cfg_mod
    from video_to_article.gui.settings import settings_dialog as sd_mod

    app = QApplication.instance() or QApplication([])

    sample = {
        "transcribe": {
            "asr_engine": "funasr",
            "funasr_speaker": True,
            "funasr_spk_model": "cam++",
            "funasr_timestamps": True,
        }
    }
    cfg_mod.load_config = lambda: sample
    sd_mod.load_config = lambda: sample

    d = SettingsDialog()
    assert d.funasr_speaker.isChecked() is True, "funasr_speaker 读错"
    assert d.funasr_spk_model.currentData() == "cam++", "funasr_spk_model 读错"
    assert d.funasr_timestamps.isChecked() is True, "funasr_timestamps 读错"
    # 联动：开了分离 → 说话人模型下拉可用
    assert d.funasr_spk_model.isEnabled() is True, "开了分离模型下拉应可用"

    # 模拟用户改动 + 校验写
    d.funasr_speaker.setChecked(False)
    assert d.funasr_spk_model.isEnabled() is False, "关掉分离后模型下拉应置灰"
    d.funasr_timestamps.setChecked(False)

    updates = d._collect_updates()
    tr = updates["transcribe"]
    assert tr["funasr_speaker"] is False, f"funasr_speaker 写错: {tr}"
    assert tr["funasr_spk_model"] == "cam++", f"funasr_spk_model 写错: {tr}"
    assert tr["funasr_timestamps"] is False, f"funasr_timestamps 写错: {tr}"
    print("OK 7: SettingsDialog funasr 说话人分离字段读/写/联动都对\n")


def main():
    test_spk_model_resolve()
    test_speaker_text_format()
    test_rich_tag_strip()
    test_fallback_paths()
    test_resolve_engine_config()
    test_settings_dialog_roundtrip()
    print("=" * 50)
    print("ALL funasr speaker-separation smoke tests passed ✓")
    print("=" * 50)


if __name__ == "__main__":
    main()
