"""Settings dialog — full config coverage for Phase C."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...config import deep_update, load_config, save_config
from ...cover import (
    COVER_MODE_FULL,
    COVER_MODE_OFF,
    COVER_MODE_PROMPT_ONLY,
    pipeline_to_legacy_flags,
    resolve_cover_pipeline_from_config,
)


def _scroll_page(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    return scroll


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("一览成文 — 设置")
        self.resize(640, 560)
        self._config: dict[str, Any] = {}

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self._build_llm_tab()
        self._build_transcribe_tab()
        self._build_youtube_tab()
        self._build_cover_tab()
        self._build_host_tab()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.load_from_disk()

    def _on_cover_enable_toggled(self, checked: bool) -> None:
        if self._cover_pipeline_syncing:
            return
        self._cover_pipeline_syncing = True
        try:
            if checked:
                self.cover_prompt_only.setChecked(False)
        finally:
            self._cover_pipeline_syncing = False

    def _on_cover_prompt_only_toggled(self, checked: bool) -> None:
        if self._cover_pipeline_syncing:
            return
        self._cover_pipeline_syncing = True
        try:
            if checked:
                self.cover_enable.setChecked(False)
        finally:
            self._cover_pipeline_syncing = False

    def _cover_pipeline_from_ui(self) -> str:
        if self.cover_enable.isChecked():
            return COVER_MODE_FULL
        if self.cover_prompt_only.isChecked():
            return COVER_MODE_PROMPT_ONLY
        return COVER_MODE_OFF

    def _apply_cover_pipeline_to_ui(self, pipeline: str) -> None:
        self._cover_pipeline_syncing = True
        try:
            if pipeline == COVER_MODE_FULL:
                self.cover_enable.setChecked(True)
                self.cover_prompt_only.setChecked(False)
            elif pipeline == COVER_MODE_PROMPT_ONLY:
                self.cover_enable.setChecked(False)
                self.cover_prompt_only.setChecked(True)
            else:
                self.cover_enable.setChecked(False)
                self.cover_prompt_only.setChecked(False)
        finally:
            self._cover_pipeline_syncing = False

    def _build_llm_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.llm_provider = QLineEdit()
        self.llm_api_key = QLineEdit()
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.llm_base_url = QLineEdit()
        self.llm_model = QLineEdit()
        self.llm_temperature = QLineEdit()
        self.llm_max_tokens = QSpinBox()
        # 2026-09-10: 扩到 1M tokens 上限，兼容 MiniMax-M3 / Gemini 1.5+ / Claude 1M 上下文
        # 1M tokens ≈ 75 万汉字 ≈ 2-3 本长篇小说
        self.llm_max_tokens.setRange(256, 1_000_000)
        self.llm_max_tokens.setToolTip(
            "LLM 单次请求最多能处理的 token 数（输入 + 输出共享）。\n"
            "1M tokens ≈ 75 万汉字 ≈ 2-3 本长篇小说。\n"
            "MiniMax-M3 / Gemini 1.5+ / Claude 1M 实测支持 1M。\n"
            "128K 以内的老模型会自动用更小值（不会报错）。"
        )
        self.llm_timeout = QSpinBox()
        self.llm_timeout.setRange(10, 3600)
        self.llm_retries = QSpinBox()
        self.llm_retries.setRange(0, 20)
        form.addRow("Provider", self.llm_provider)
        form.addRow("API Key", self.llm_api_key)
        form.addRow("Base URL", self.llm_base_url)
        form.addRow("Model", self.llm_model)
        form.addRow("Temperature", self.llm_temperature)
        form.addRow("Max tokens", self.llm_max_tokens)
        form.addRow("超时(秒)", self.llm_timeout)
        form.addRow("重试次数", self.llm_retries)
        self.tabs.addTab(_scroll_page(page), "大模型")

    def _build_transcribe_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)

        # 基础选项
        base = QGroupBox("基础")
        form = QFormLayout(base)
        self.tr_engine = QComboBox()
        self.tr_engine.addItem("funasr", "funasr")
        self.tr_engine.addItem("whisper", "whisper")
        self.tr_engine.addItem("qwen_asr", "qwen_asr")
        self.tr_funasr = QLineEdit()
        self.tr_funasr.setToolTip(
            "仅在 ASR 引擎 = funasr 时生效。\n"
            "切换到 qwen_asr 引擎后，此字段不参与实际加载，"
            "请使用下方「Qwen3-ASR 高级」配置 model_id / context_file / hf_home。"
        )
        self.tr_model_size = QComboBox()
        for size in ("tiny", "base", "small"):
            self.tr_model_size.addItem(size, size)
        self.tr_threads = QSpinBox()
        self.tr_threads.setRange(1, 64)
        self.tr_auto = QCheckBox("自动优化相关兼容开关")
        self.tr_funasr_dir = QLineEdit()
        self.tr_funasr_dir.setPlaceholderText("留空则自动选择")
        form.addRow("默认 ASR 引擎", self.tr_engine)
        form.addRow("FunASR 模型（funasr 引擎专用）", self.tr_funasr)
        form.addRow("Whisper 大小", self.tr_model_size)
        form.addRow("CPU 线程", self.tr_threads)
        form.addRow("FunASR 模型目录（funasr 引擎专用）", self.tr_funasr_dir)
        form.addRow(self.tr_auto)
        outer.addWidget(base)

        # Qwen3-ASR 高级
        qwen_box = QGroupBox("Qwen3-ASR 高级（qwen_asr 引擎专用）")
        qform = QFormLayout(qwen_box)

        self.qwen_model = QComboBox()
        self.qwen_model.addItem(
            "Qwen/Qwen3-ASR-0.6B（稳跑 12GB 显卡）",
            "Qwen/Qwen3-ASR-0.6B",
        )
        self.qwen_model.addItem(
            "Qwen/Qwen3-ASR-1.7B（精度更高，需 GPU 显存 ≥ 8GB）",
            "Qwen/Qwen3-ASR-1.7B",
        )
        self.qwen_model.setToolTip(
            "切换模型会在下次转写时重新加载（约 5s），\n"
            "_get_or_load_model 内部会自动释放旧模型 cache，"
            "不会长期占 GPU 显存。"
        )

        self.qwen_context = QLineEdit()
        self.qwen_context.setPlaceholderText("专名偏置词表路径（留空 = 无 context）")
        self.qwen_context_btn = QPushButton("浏览…")
        self.qwen_context_btn.clicked.connect(self._pick_qwen_context)

        self.qwen_hf_home = QLineEdit()
        self.qwen_hf_home.setPlaceholderText(r"E:\AI_Models\Qwen3-ASR")
        self.qwen_hf_home_btn = QPushButton("浏览…")
        self.qwen_hf_home_btn.clicked.connect(self._pick_qwen_hf_home)

        self.qwen_language = QComboBox()
        # 2026-09-09：Qwen3-ASR 0.0.6 不支持 "Auto"，实测会报 Unsupported language
        # 只暴露用户实际用得到的白名单语言（日韩双语视频常见）
        # 完整 30 种白名单见 qwen_asr.py _SUPPORTED_LANGS
        self.qwen_language.addItem("Chinese（中文）", "Chinese")
        self.qwen_language.addItem("English（英文）", "English")
        self.qwen_language.addItem("Japanese（日语）", "Japanese")
        self.qwen_language.addItem("Korean（韩语）", "Korean")

        # 2026-09-09：device fallback 选项（GPU 1 掉驱动时用）
        self.qwen_device = QComboBox()
        self.qwen_device.addItem(
            "auto（CUDA 可用就用，否则自动降级 CPU）", "auto"
        )
        self.qwen_device.addItem(
            "cuda（强制 GPU 0，失败报错 — 适合调试驱动）", "cuda"
        )
        self.qwen_device.addItem(
            "cpu（强制 CPU 跑 — GPU 驱动掉了 / 玩游戏抢卡 / OOM 复测）", "cpu"
        )
        self.qwen_device.setToolTip(
            "auto: 默认。CUDA 不可用时静默降级 CPU + WARNING 日志。\n"
            "cuda: 强制 GPU 0。CUDA 不可用立即报错（适合排查驱动）。\n"
            "cpu:  强制 CPU。1.7B 模型在 CPU 上比 GPU 慢 5-10x，"
            "建议切到 0.6B 提速。"
        )

        ctx_row = QWidget()
        ctx_layout = QHBoxLayout(ctx_row)
        ctx_layout.setContentsMargins(0, 0, 0, 0)
        ctx_layout.addWidget(self.qwen_context, 1)
        ctx_layout.addWidget(self.qwen_context_btn)

        hf_row = QWidget()
        hf_layout = QHBoxLayout(hf_row)
        hf_layout.setContentsMargins(0, 0, 0, 0)
        hf_layout.addWidget(self.qwen_hf_home, 1)
        hf_layout.addWidget(self.qwen_hf_home_btn)

        qform.addRow("模型", self.qwen_model)
        qform.addRow("Context 文件（专名偏置）", ctx_row)
        qform.addRow("HF 缓存目录", hf_row)
        qform.addRow("语言", self.qwen_language)
        qform.addRow("Device（GPU / CPU fallback）", self.qwen_device)
        outer.addWidget(qwen_box)

        # 2026-09-09: 中英混合等组合语言值的 API 限制说明（不暴露误导项）
        # qwen_asr 0.0.6 validate_language 只接受 30 种单语种；Chinese 跑混合足够
        mix_tip = QLabel(
            "💡 中英/中日/中韩混合语言：选 Chinese 即可。\n"
            "  · Qwen3-ASR 0.0.6 API 强制单语种输入（不支持 'Chinese+English' 这种组合值），"
            "组合值会被 validate_language 拒绝\n"
            "  · 模型本身支持 code-switching，B 站知识区/番剧混剪实测 Chinese 跑 + "
            "下游 LLM 二次纠错效果最好\n"
            "  · 真要传 Chinese+English 这种组合值会让 model.transcribe 报 "
            "Unsupported language（你之前 9.9 那个错就是类似根因）"
        )
        mix_tip.setWordWrap(True)
        mix_tip.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(mix_tip)

        # GPU 兼容性提示（只读，避免用户乱改破坏 GPU 加载）
        gpu_tip = QLabel(
            "⚙ GPU 兼容性约束：\n"
            "  · 显存上限：max_memory = {0: \"7GiB\"}（1.7B / 0.6B 通用，"
            "避免 RTX 5070 Ti 12GB OOM）\n"
            "  · 精度：bfloat16\n"
            "  · 模型切换 / device 切换时自动释放旧 cache 再重载，过程不长期占显存\n"
            "  · 长音频（> 7.5 分钟）自动切 5 分钟一段，防止 KV cache 撑爆\n"
            "  · ⚠ GPU 驱动掉了（CUDA 不可用）→ device=auto 会静默降级到 CPU，"
            "1.7B 在 CPU 上比 GPU 慢 5-10x，建议切到 0.6B 提速\n"
            "  · 加载失败（CUDA OOM / no device）→ 自动重试 CPU 一次 + WARNING\n"
            "修改后点「保存」即生效；下次转写会按新配置加载。"
        )
        gpu_tip.setWordWrap(True)
        gpu_tip.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(gpu_tip)

        tip = QLabel(
            "模型目录为语音模型的下载与缓存位置；路径中请避免中文等非英文字符。"
            "留空时由程序自动选择。单次任务可在工作台「高级 ASR」临时覆盖引擎参数。"
        )
        tip.setWordWrap(True)
        outer.addWidget(tip)

        outer.addStretch(1)
        self.tabs.addTab(_scroll_page(page), "转写")

    def _pick_qwen_context(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Qwen3-ASR 专名偏置词表",
            self.qwen_context.text().strip() or "",
            "Text files (*.txt);;All files (*.*)",
        )
        if path:
            self.qwen_context.setText(path)

    def _pick_qwen_hf_home(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 HF 模型缓存目录",
            self.qwen_hf_home.text().strip() or r"E:\AI_Models\Qwen3-ASR",
        )
        if path:
            self.qwen_hf_home.setText(path)

    def _build_youtube_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.yt_browser = QComboBox()
        self.yt_browser.addItem("（空）", "")
        for name in ("chrome", "edge", "firefox"):
            self.yt_browser.addItem(name, name)
        self.yt_cookies_file = QLineEdit()
        self.yt_po_token = QLineEdit()
        form.addRow("默认从浏览器读 cookies", self.yt_browser)
        form.addRow("默认 cookies 文件", self.yt_cookies_file)
        form.addRow("PO Token", self.yt_po_token)
        tip = QLabel("工作台单次任务中的 Cookies 选项会覆盖此处的默认值。")
        tip.setWordWrap(True)
        form.addRow(tip)
        self.tabs.addTab(_scroll_page(page), "YouTube / 下载")

    def _build_cover_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        base = QGroupBox("基础开关与服务")
        form = QFormLayout(base)
        # 互斥：启用 AI 封面（提示词+API） / 仅导出提示词；都不勾=关闭
        self.cover_enable = QCheckBox("启用 AI 封面（提示词 + 生图 API）")
        self.cover_prompt_only = QCheckBox("仅导出封面提示词（不调生图 API）")
        self.cover_enable.setChecked(False)
        self.cover_prompt_only.setChecked(False)
        self._cover_pipeline_syncing = False
        self.cover_enable.toggled.connect(self._on_cover_enable_toggled)
        self.cover_prompt_only.toggled.connect(self._on_cover_prompt_only_toggled)
        self.cover_provider = QLineEdit()
        self.cover_base_url = QLineEdit()
        self.cover_api_key = QLineEdit()
        self.cover_api_key.setEchoMode(QLineEdit.Password)
        self.cover_model = QLineEdit()
        self.cover_edit_model = QLineEdit()
        self.cover_mode = QLineEdit()
        self.cover_size = QLineEdit()
        self.cover_format = QLineEdit()
        self.cover_brand = QLineEdit()
        form.addRow(self.cover_enable)
        form.addRow(self.cover_prompt_only)
        form.addRow("Provider", self.cover_provider)
        form.addRow("Base URL", self.cover_base_url)
        form.addRow("API Key", self.cover_api_key)
        form.addRow("Model", self.cover_model)
        form.addRow("Edit model", self.cover_edit_model)
        form.addRow("mode", self.cover_mode)
        form.addRow("size", self.cover_size)
        form.addRow("output_format", self.cover_format)
        form.addRow("brand", self.cover_brand)
        layout.addWidget(base)

        flags = QGroupBox("参考图与策略")
        ff = QFormLayout(flags)
        self.cover_use_ref = QCheckBox("use_reference_image")
        self.cover_enable_edit = QCheckBox("enable_image_edit")
        self.cover_send_b64 = QCheckBox("send_local_reference_as_base64")
        self.cover_fallback_t2i = QCheckBox("fallback_to_text_to_image")
        self.cover_use_model_url = QCheckBox("use_model_output_url_in_frontmatter")
        self.cover_force_t2i = QCheckBox("force_text_to_image_for_noisy_thumbnail")
        self.cover_openai_edit = QLineEdit()
        self.cover_ref_url = QLineEdit()
        for w in (
            self.cover_use_ref,
            self.cover_enable_edit,
            self.cover_send_b64,
            self.cover_fallback_t2i,
            self.cover_use_model_url,
            self.cover_force_t2i,
        ):
            ff.addRow(w)
        ff.addRow("openai_edit_strategy", self.cover_openai_edit)
        ff.addRow("reference_image_url", self.cover_ref_url)
        layout.addWidget(flags)

        text = QGroupBox("风格文案")
        tf = QFormLayout(text)
        self.cover_style = QPlainTextEdit()
        self.cover_style.setMinimumHeight(90)
        self.cover_negative = QPlainTextEdit()
        self.cover_negative.setMinimumHeight(90)
        tf.addRow("style", self.cover_style)
        tf.addRow("negative_prompt", self.cover_negative)
        layout.addWidget(text)

        timeouts = QGroupBox("超时与轮询（高级）")
        timeouts.setCheckable(True)
        timeouts.setChecked(False)
        to = QFormLayout(timeouts)
        self.cover_submit_to = QSpinBox()
        self.cover_submit_to.setRange(10, 3600)
        self.cover_download_to = QSpinBox()
        self.cover_download_to.setRange(10, 3600)
        self.cover_poll_to = QSpinBox()
        self.cover_poll_to.setRange(10, 3600)
        self.cover_poll_interval = QSpinBox()
        self.cover_poll_interval.setRange(1, 120)
        self.cover_max_wait = QSpinBox()
        self.cover_max_wait.setRange(30, 7200)
        to.addRow("submit_timeout_seconds", self.cover_submit_to)
        to.addRow("download_timeout_seconds", self.cover_download_to)
        to.addRow("poll_timeout_seconds", self.cover_poll_to)
        to.addRow("poll_interval_seconds", self.cover_poll_interval)
        to.addRow("max_wait_seconds", self.cover_max_wait)
        layout.addWidget(timeouts)
        self.cover_timeouts_box = timeouts

        pre = QGroupBox("参考图预处理阈值（高级）")
        pre.setCheckable(True)
        pre.setChecked(False)
        pf = QFormLayout(pre)
        self.cover_enable_pre = QCheckBox("enable_reference_preprocess")
        self.cover_text_thr = QDoubleSpinBox()
        self.cover_text_thr.setDecimals(3)
        self.cover_text_thr.setRange(0, 10)
        self.cover_min_food = QDoubleSpinBox()
        self.cover_min_food.setDecimals(3)
        self.cover_min_food.setRange(0, 1)
        self.cover_crop_food = QDoubleSpinBox()
        self.cover_crop_food.setDecimals(3)
        self.cover_crop_food.setRange(0, 1)
        self.cover_crop_top = QDoubleSpinBox()
        self.cover_crop_top.setDecimals(3)
        self.cover_crop_top.setRange(0, 1)
        self.cover_crop_bottom = QDoubleSpinBox()
        self.cover_crop_bottom.setDecimals(3)
        self.cover_crop_bottom.setRange(0, 1)
        pf.addRow(self.cover_enable_pre)
        pf.addRow("reference_text_score_threshold", self.cover_text_thr)
        pf.addRow("reference_min_food_score", self.cover_min_food)
        pf.addRow("reference_crop_min_food_score", self.cover_crop_food)
        pf.addRow("reference_crop_top_ratio", self.cover_crop_top)
        pf.addRow("reference_crop_bottom_ratio", self.cover_crop_bottom)
        layout.addWidget(pre)
        self.cover_pre_box = pre
        layout.addStretch(1)
        self.tabs.addTab(_scroll_page(page), "AI 封面")

    def _build_host_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.host_enable = QCheckBox("启用图床上传")
        self.host_provider = QLineEdit()
        self.host_api_url = QLineEdit()
        self.host_token = QLineEdit()
        self.host_token.setEchoMode(QLineEdit.Password)
        self.host_token_field = QLineEdit()
        self.host_file_field = QLineEdit()
        self.host_url_path = QLineEdit()
        self.host_timeout = QSpinBox()
        self.host_timeout.setRange(10, 3600)
        self.host_extra = QPlainTextEdit()
        self.host_extra.setPlaceholderText('JSON 对象，例如 {"album":"food"}')
        self.host_extra.setMaximumHeight(100)
        form.addRow(self.host_enable)
        form.addRow("Provider", self.host_provider)
        form.addRow("API URL", self.host_api_url)
        form.addRow("Token", self.host_token)
        form.addRow("token_field", self.host_token_field)
        form.addRow("file_field", self.host_file_field)
        form.addRow("url_json_path", self.host_url_path)
        form.addRow("timeout_seconds", self.host_timeout)
        form.addRow("extra_fields (JSON)", self.host_extra)
        self.tabs.addTab(_scroll_page(page), "图床")

    def load_from_disk(self) -> None:
        self._config = load_config() or {}
        llm = self._config.get("llm") or {}
        self.llm_provider.setText(str(llm.get("provider", "")))
        self.llm_api_key.setText(str(llm.get("api_key", "")))
        self.llm_base_url.setText(str(llm.get("base_url", "")))
        self.llm_model.setText(str(llm.get("model", "")))
        self.llm_temperature.setText(str(llm.get("temperature", "0.3")))
        self.llm_max_tokens.setValue(int(llm.get("max_tokens") or 12000))
        self.llm_timeout.setValue(int(llm.get("timeout_seconds") or 180))
        self.llm_retries.setValue(int(llm.get("max_retries") or 3))

        tr = self._config.get("transcribe") or {}
        idx = self.tr_engine.findData(str(tr.get("asr_engine") or "funasr"))
        self.tr_engine.setCurrentIndex(idx if idx >= 0 else 0)
        self.tr_funasr.setText(str(tr.get("funasr_model") or "sensevoice"))
        sidx = self.tr_model_size.findData(str(tr.get("model_size") or "tiny"))
        self.tr_model_size.setCurrentIndex(sidx if sidx >= 0 else 0)
        self.tr_threads.setValue(int(tr.get("cpu_threads") or 4))
        self.tr_auto.setChecked(bool(tr.get("auto_optimize", True)))
        self.tr_funasr_dir.setText(
            str(tr.get("funasr_cache_dir") or tr.get("funasr_dir") or "")
        )

        # Qwen3-ASR 高级子块（独立于 funasr 字段）
        qa = (tr.get("qwen_asr") or {})
        mid = str(qa.get("model_id") or "Qwen/Qwen3-ASR-0.6B")
        midx = self.qwen_model.findData(mid)
        self.qwen_model.setCurrentIndex(midx if midx >= 0 else 0)
        self.qwen_context.setText(str(qa.get("context_file") or ""))
        self.qwen_hf_home.setText(
            str(qa.get("hf_home") or r"E:\AI_Models\Qwen3-ASR")
        )
        lang = str(qa.get("language") or "Chinese")
        # 2026-09-09: Qwen3-ASR 不支持 "Auto" / 空 / 中文别名 — 兜底回 Chinese + WARNING
        # GUI 暴露 4 种（Chinese/English/Japanese/Korean），完整白名单 30 种见 qwen_asr.py
        if lang not in {"Chinese", "English", "Japanese", "Korean"}:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "语言不兼容",
                f"qwen_asr.language='{lang}' 不在 Qwen3-ASR 支持列表中（或 GUI 暂未暴露），"
                f"已自动回退为 'Chinese'。\n保存后会写回 config.json。",
            )
            lang = "Chinese"
        lidx = self.qwen_language.findData(lang)
        self.qwen_language.setCurrentIndex(lidx if lidx >= 0 else 0)
        dev = str(qa.get("device") or "auto")
        didx = self.qwen_device.findData(dev)
        self.qwen_device.setCurrentIndex(didx if didx >= 0 else 0)

        yt = self._config.get("youtube") or {}
        browser = str(yt.get("cookies_from_browser") or "")
        idx = self.yt_browser.findData(browser)
        self.yt_browser.setCurrentIndex(idx if idx >= 0 else 0)
        self.yt_cookies_file.setText(str(yt.get("cookies_file") or ""))
        self.yt_po_token.setText(str(yt.get("po_token") or ""))

        cover = self._config.get("ai_cover") or {}
        self._apply_cover_pipeline_to_ui(resolve_cover_pipeline_from_config(cover))
        self.cover_provider.setText(str(cover.get("provider") or ""))
        self.cover_base_url.setText(str(cover.get("base_url") or ""))
        self.cover_api_key.setText(str(cover.get("api_key") or ""))
        self.cover_model.setText(str(cover.get("model") or ""))
        self.cover_edit_model.setText(str(cover.get("edit_model") or ""))
        self.cover_mode.setText(str(cover.get("mode") or "auto"))
        self.cover_size.setText(str(cover.get("size") or "1344x768"))
        self.cover_format.setText(str(cover.get("output_format") or "jpg"))
        self.cover_brand.setText(str(cover.get("brand") or ""))
        self.cover_use_ref.setChecked(bool(cover.get("use_reference_image", True)))
        self.cover_enable_edit.setChecked(bool(cover.get("enable_image_edit", True)))
        self.cover_send_b64.setChecked(bool(cover.get("send_local_reference_as_base64", True)))
        self.cover_fallback_t2i.setChecked(bool(cover.get("fallback_to_text_to_image", True)))
        self.cover_use_model_url.setChecked(bool(cover.get("use_model_output_url_in_frontmatter", False)))
        self.cover_force_t2i.setChecked(bool(cover.get("force_text_to_image_for_noisy_thumbnail", False)))
        self.cover_openai_edit.setText(str(cover.get("openai_edit_strategy") or "auto"))
        self.cover_ref_url.setText(str(cover.get("reference_image_url") or ""))
        self.cover_style.setPlainText(str(cover.get("style") or ""))
        self.cover_negative.setPlainText(str(cover.get("negative_prompt") or ""))
        self.cover_submit_to.setValue(int(cover.get("submit_timeout_seconds") or 300))
        self.cover_download_to.setValue(int(cover.get("download_timeout_seconds") or 180))
        self.cover_poll_to.setValue(int(cover.get("poll_timeout_seconds") or 120))
        self.cover_poll_interval.setValue(int(cover.get("poll_interval_seconds") or 5))
        self.cover_max_wait.setValue(int(cover.get("max_wait_seconds") or 600))
        self.cover_enable_pre.setChecked(bool(cover.get("enable_reference_preprocess", True)))
        self.cover_text_thr.setValue(float(cover.get("reference_text_score_threshold") or 1.25))
        self.cover_min_food.setValue(float(cover.get("reference_min_food_score") or 0.38))
        self.cover_crop_food.setValue(float(cover.get("reference_crop_min_food_score") or 0.42))
        self.cover_crop_top.setValue(float(cover.get("reference_crop_top_ratio") or 0.18))
        self.cover_crop_bottom.setValue(float(cover.get("reference_crop_bottom_ratio") or 0.18))

        host = self._config.get("image_host") or {}
        self.host_enable.setChecked(bool(host.get("enable")))
        self.host_provider.setText(str(host.get("provider") or ""))
        self.host_api_url.setText(str(host.get("api_url") or ""))
        self.host_token.setText(str(host.get("token") or ""))
        self.host_token_field.setText(str(host.get("token_field") or "token"))
        self.host_file_field.setText(str(host.get("file_field") or "image"))
        self.host_url_path.setText(str(host.get("url_json_path") or "url"))
        self.host_timeout.setValue(int(host.get("timeout_seconds") or 180))
        extra = host.get("extra_fields") or {}
        try:
            self.host_extra.setPlainText(json.dumps(extra, ensure_ascii=False, indent=2) if extra else "{}")
        except (TypeError, ValueError):
            self.host_extra.setPlainText("{}")

    def _collect_updates(self) -> dict[str, Any]:
        try:
            temperature = float(self.llm_temperature.text().strip() or "0.3")
        except ValueError:
            temperature = 0.3
        try:
            extra_fields = json.loads(self.host_extra.toPlainText().strip() or "{}")
            if not isinstance(extra_fields, dict):
                raise ValueError("extra_fields 必须是 JSON 对象")
        except json.JSONDecodeError as exc:
            raise ValueError(f"图床 extra_fields JSON 无效: {exc}") from exc

        return {
            "llm": {
                "provider": self.llm_provider.text().strip(),
                "api_key": self.llm_api_key.text().strip(),
                "base_url": self.llm_base_url.text().strip(),
                "model": self.llm_model.text().strip(),
                "temperature": temperature,
                "max_tokens": self.llm_max_tokens.value(),
                "timeout_seconds": self.llm_timeout.value(),
                "max_retries": self.llm_retries.value(),
            },
            "transcribe": {
                "asr_engine": self.tr_engine.currentData() or "funasr",
                "funasr_model": self.tr_funasr.text().strip() or "sensevoice",
                "model_size": self.tr_model_size.currentData() or "tiny",
                "cpu_threads": self.tr_threads.value(),
                "auto_optimize": self.tr_auto.isChecked(),
                "funasr_cache_dir": self.tr_funasr_dir.text().strip(),
                "qwen_asr": {
                    "model_id": self.qwen_model.currentData()
                    or "Qwen/Qwen3-ASR-0.6B",
                    "context_file": self.qwen_context.text().strip(),
                    "hf_home": self.qwen_hf_home.text().strip()
                    or r"E:\AI_Models\Qwen3-ASR",
                    "language": self.qwen_language.currentData() or "Chinese",
                    "device": self.qwen_device.currentData() or "auto",
                },
            },
            "youtube": {
                "cookies_from_browser": self.yt_browser.currentData() or "",
                "cookies_file": self.yt_cookies_file.text().strip(),
                "po_token": self.yt_po_token.text().strip(),
            },
            "ai_cover": {
                **pipeline_to_legacy_flags(self._cover_pipeline_from_ui()),
                "pipeline": self._cover_pipeline_from_ui(),
                "provider": self.cover_provider.text().strip(),
                "base_url": self.cover_base_url.text().strip(),
                "api_key": self.cover_api_key.text().strip(),
                "model": self.cover_model.text().strip(),
                "edit_model": self.cover_edit_model.text().strip(),
                "mode": self.cover_mode.text().strip() or "auto",
                "size": self.cover_size.text().strip() or "1344x768",
                "output_format": self.cover_format.text().strip() or "jpg",
                "brand": self.cover_brand.text().strip(),
                "use_reference_image": self.cover_use_ref.isChecked(),
                "enable_image_edit": self.cover_enable_edit.isChecked(),
                "send_local_reference_as_base64": self.cover_send_b64.isChecked(),
                "fallback_to_text_to_image": self.cover_fallback_t2i.isChecked(),
                "use_model_output_url_in_frontmatter": self.cover_use_model_url.isChecked(),
                "force_text_to_image_for_noisy_thumbnail": self.cover_force_t2i.isChecked(),
                "openai_edit_strategy": self.cover_openai_edit.text().strip() or "auto",
                "reference_image_url": self.cover_ref_url.text().strip(),
                "style": self.cover_style.toPlainText().strip(),
                "negative_prompt": self.cover_negative.toPlainText().strip(),
                "submit_timeout_seconds": self.cover_submit_to.value(),
                "download_timeout_seconds": self.cover_download_to.value(),
                "poll_timeout_seconds": self.cover_poll_to.value(),
                "poll_interval_seconds": self.cover_poll_interval.value(),
                "max_wait_seconds": self.cover_max_wait.value(),
                "enable_reference_preprocess": self.cover_enable_pre.isChecked(),
                "reference_text_score_threshold": self.cover_text_thr.value(),
                "reference_min_food_score": self.cover_min_food.value(),
                "reference_crop_min_food_score": self.cover_crop_food.value(),
                "reference_crop_top_ratio": self.cover_crop_top.value(),
                "reference_crop_bottom_ratio": self.cover_crop_bottom.value(),
            },
            "image_host": {
                "enable": self.host_enable.isChecked(),
                "provider": self.host_provider.text().strip(),
                "api_url": self.host_api_url.text().strip(),
                "token": self.host_token.text().strip(),
                "token_field": self.host_token_field.text().strip() or "token",
                "file_field": self.host_file_field.text().strip() or "image",
                "url_json_path": self.host_url_path.text().strip() or "url",
                "timeout_seconds": self.host_timeout.value(),
                "extra_fields": extra_fields,
            },
        }

    def _save(self) -> None:
        try:
            current = load_config() or {}
            deep_update(current, self._collect_updates())
            save_config(current)
            self._config = current
            QMessageBox.information(self, "设置", "设置已保存")
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
