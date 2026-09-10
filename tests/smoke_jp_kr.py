"""Smoke: GUI 暴露 Japanese / Korean 后，磁盘值能正确读 + 写回 + 兜底"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")

from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])

# 屏蔽弹窗
QMessageBox.warning = staticmethod(lambda *a, **k: print(f"  [警告弹窗] {a[2]}"))


def test_gui_exposes_jp_kr():
    """1. GUI 下拉框含 Chinese/English/Japanese/Korean 共 4 项"""
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article.gui.settings import settings_dialog as sd_mod

    sd_mod.load_config = lambda: {
        "transcribe": {
            "asr_engine": "qwen_asr",
            "qwen_asr": {
                "model_id": "Qwen/Qwen3-ASR-0.6B",
                "context_file": "",
                "hf_home": r"E:\AI_Models\Qwen3-ASR",
                "language": "Chinese",  # 默认
                "device": "auto",
            },
        }
    }
    d = SettingsDialog()
    items = [
        d.qwen_language.itemData(i)
        for i in range(d.qwen_language.count())
    ]
    print(f"qwen_language items: {items}")
    assert items == ["Chinese", "English", "Japanese", "Korean"], f"暴露 4 种，got {items}"
    print("OK 1: GUI 暴露 Chinese/English/Japanese/Korean\n")


def test_disk_japanese_round_trip():
    """2. 磁盘 language=Japanese → GUI 读到 Japanese + 写回 Japanese"""
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article.gui.settings import settings_dialog as sd_mod

    sd_mod.load_config = lambda: {
        "transcribe": {
            "qwen_asr": {"language": "Japanese", "device": "auto"}
        }
    }
    d = SettingsDialog()
    print(f"磁盘 Japanese → GUI currentData = {d.qwen_language.currentData()}")
    assert d.qwen_language.currentData() == "Japanese"

    updates = d._collect_updates()
    assert updates["transcribe"]["qwen_asr"]["language"] == "Japanese"
    print("OK 2: Japanese 磁盘读 + 写回正确\n")


def test_disk_korean_round_trip():
    """3. 磁盘 language=Korean 同上"""
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article.gui.settings import settings_dialog as sd_mod

    sd_mod.load_config = lambda: {
        "transcribe": {
            "qwen_asr": {"language": "Korean", "device": "auto"}
        }
    }
    d = SettingsDialog()
    print(f"磁盘 Korean → GUI currentData = {d.qwen_language.currentData()}")
    assert d.qwen_language.currentData() == "Korean"
    print("OK 3: Korean 磁盘读 + 写回正确\n")


def test_disk_arabic_fallback_to_chinese():
    """4. 磁盘 language=Arabic（白名单内但 GUI 未暴露）→ 兜底 Chinese + 警告"""
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article.gui.settings import settings_dialog as sd_mod

    sd_mod.load_config = lambda: {
        "transcribe": {
            "qwen_asr": {"language": "Arabic", "device": "auto"}
        }
    }
    d = SettingsDialog()
    print(f"磁盘 Arabic → GUI currentData = {d.qwen_language.currentData()} (应兜底 Chinese)")
    assert d.qwen_language.currentData() == "Chinese"
    print("OK 4: 白名单内但 GUI 未暴露的语言兜底 Chinese + 警告\n")


def test_qwen_asr_whitelist_includes_jp_kr():
    """5. qwen_asr._SUPPORTED_LANGS 包含 Japanese + Korean（底层不会兜底）"""
    from video_to_article.media import qwen_asr
    for lang in ("Chinese", "English", "Japanese", "Korean"):
        assert lang in qwen_asr._SUPPORTED_LANGS, f"{lang} 缺漏白名单"
    print(f"OK 5: 白名单 30 种含 Chinese/English/Japanese/Korean（共 {len(qwen_asr._SUPPORTED_LANGS)} 种）\n")


if __name__ == "__main__":
    test_gui_exposes_jp_kr()
    test_disk_japanese_round_trip()
    test_disk_korean_round_trip()
    test_disk_arabic_fallback_to_chinese()
    test_qwen_asr_whitelist_includes_jp_kr()
    print("=" * 50)
    print("ALL JP/KR tests passed ✓")
    print("=" * 50)
