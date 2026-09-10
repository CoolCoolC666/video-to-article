"""Smoke test: SettingsDialog reads transcribe.qwen_asr, _collect_updates writes it back."""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from video_to_article.gui.settings.settings_dialog import SettingsDialog
from video_to_article import config as cfg_mod

sample = {
    "transcribe": {
        "asr_engine": "qwen_asr",
        "funasr_model": "Qwen/Qwen3-ASR-0.6B",
        "model_size": "small",
        "cpu_threads": 6,
        "auto_optimize": True,
        "funasr_cache_dir": r"E:\AI Agent\Models\FunASR",
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "context_file": r"E:\AI Agent\Data list\.minimax\skills\funasr-hotword\contexts\starrail.txt",
            "language": "Chinese",
            "hf_home": r"E:\AI_Models\Qwen3-ASR",
        },
    }
}

cfg_mod.load_config = lambda: sample
# 也 patch settings_dialog 模块内的 load_config 引用
from video_to_article.gui.settings import settings_dialog as sd_mod
sd_mod.load_config = lambda: sample
d = SettingsDialog()
print("tr_engine currentData       =", d.tr_engine.currentData())
print("tr_funasr text              =", d.tr_funasr.text())
print("qwen_model currentData      =", d.qwen_model.currentData())
print("qwen_context text           =", d.qwen_context.text())
print("qwen_hf_home text           =", d.qwen_hf_home.text())
print("qwen_language currentData   =", d.qwen_language.currentData())
print()

# Simulate the user flipping to 1.7B and tweaking context path
d.qwen_model.setCurrentIndex(d.qwen_model.findData("Qwen/Qwen3-ASR-1.7B"))
d.qwen_context.setText(r"D:\new_context\mywords.txt")
d.qwen_hf_home.setText(r"D:\hf_cache")

updates = d._collect_updates()
print("_collect_updates['transcribe'] =")
print(json.dumps(updates["transcribe"], ensure_ascii=False, indent=2))

assert "qwen_asr" in updates["transcribe"], "qwen_asr 子块丢失"
assert updates["transcribe"]["qwen_asr"]["model_id"] == "Qwen/Qwen3-ASR-1.7B"
assert updates["transcribe"]["qwen_asr"]["context_file"] == r"D:\new_context\mywords.txt"
assert updates["transcribe"]["qwen_asr"]["hf_home"] == r"D:\hf_cache"
assert updates["transcribe"]["qwen_asr"]["language"] == "Chinese"
# funasr_model 仍然在（不破坏既有字段）
assert updates["transcribe"]["funasr_model"] == "Qwen/Qwen3-ASR-0.6B"
# asr_engine 正确
assert updates["transcribe"]["asr_engine"] == "qwen_asr"
print()
print("OK: qwen_asr 子块正确读 + 写，未破坏既有字段")
