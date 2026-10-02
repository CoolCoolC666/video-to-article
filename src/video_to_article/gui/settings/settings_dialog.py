"""Settings dialog — full config coverage for Phase C."""

from __future__ import annotations

import json
from pathlib import Path
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
from ...media.xf_asr import PD_DOMAINS
from ...media.custom_post_asr import (
    DEFAULT_ENDPOINT,
    HEADERS_FILENAME,
    MINIMAX_LANGUAGES,
    default_headers_file,
    write_headers_template,
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
        # 2026-09-27 新增：讯飞听见云端识别（xf_asr 引擎）
        self.tr_engine.addItem("xf_asr（讯飞听见）", "xf_asr")
        # 2026-10-02 新增：自定义 POST 云端识别（端点+请求头均可配）
        self.tr_engine.addItem("custom_post（自定义 POST）", "custom_post")
        # 2026-09-27 防呆：切到 xf_asr 且凭证齐全时自动取消 Mock（避免"填了凭证但忘了取消勾选"陷阱）
        self.tr_engine.currentIndexChanged.connect(self._on_tr_engine_changed)
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

        # === 2026-10-02 新增：FunASR 高级（本地说话人分离）===
        fs_box = QGroupBox("FunASR 高级（funasr 引擎专用）")
        fsform = QFormLayout(fs_box)

        self.funasr_speaker = QCheckBox("分离说话人（本地 CAM++，标注【说话人1】…）")
        self.funasr_speaker.setToolTip(
            "开启后给 FunASR 挂载 CAM++ 说话人嵌入模型（spk_model=\"cam++\"），\n"
            "输出每段带【说话人N】标签。适合两人对话（访谈 / 对话课 / 师生问答）。\n\n"
            "· 完全本地：不调网络、不花云端额度\n"
            "· 开销小：CAM++ 是 ~28MB 的嵌入模型（非生成式），CPU 即可跑、不占 GPU 显存\n"
            "· 首次开启会从 ModelScope 下载约 28MB 到 FunASR 缓存目录\n"
            "· spk=0 是匿名的录音内标签，不是跨录音稳定的人员 ID\n"
            "· 单人说话时开启没有意义（会全部标成同一个人）"
        )
        self.funasr_speaker.toggled.connect(self._on_funasr_speaker_toggled)

        self.funasr_spk_model = QComboBox()
        self.funasr_spk_model.addItem("CAM++（cam++，中文 16k 通用，推荐）", "cam++")
        self.funasr_spk_model.setToolTip(
            "说话人嵌入模型。目前只接入 CAM++（iic/speech_campplus_sv_zh-cn_16k-common），\n"
            "这是 FunASR 官方 demo 唯一演示的说话人模型。\n"
            "若要换 ERes2NetV2 等，需要同步改 audio.py 的 CAMPLUS 目录/完整性检查。"
        )

        self.funasr_timestamps = QCheckBox("输出时间戳（每段加 [MM:SS] 前缀）")
        self.funasr_timestamps.setChecked(True)
        self.funasr_timestamps.setToolTip(
            "开启后每段前面加 [MM:SS] 时间戳（读 FunASR sentence_info 的 start 字段）。\n"
            "与 xf_asr 的时间戳格式保持一致，方便横向对比。"
        )

        fsform.addRow(self.funasr_speaker)
        fsform.addRow("说话人模型", self.funasr_spk_model)
        fsform.addRow(self.funasr_timestamps)
        outer.addWidget(fs_box)

        fs_tip = QLabel(
            "💡 本地说话人分离（FunASR + CAM++）：\n"
            "  · 完全离线，不消耗任何云端额度，也不把音频传出本机\n"
            "  · SenseVoice 自身不产角色号，必须额外挂 CAM++ 嵌入模型（spk_model 参数）\n"
            "  · FunASR 官方约束：spk_model 必须与 vad_model 一起传（聚类在 VAD 流水线里做）\n"
            "  · 拿不到 sentence_info 时自动退回纯文本，不会中断转写\n"
            "  · 对比：Qwen3-ASR 完全不支持说话人分离；讯飞 xf_asr 支持但是云端能力"
        )
        fs_tip.setWordWrap(True)
        fs_tip.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(fs_tip)

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

        # 2026-09-27 新增：讯飞听见（xf_asr 引擎专用）
        # 2026-09-27 订正：长语音鉴权只用 APPID + SecretKey 两件，删 API Key 字段 → 5 字段
        xf_box = QGroupBox("讯飞听见 高级（xf_asr 引擎专用）")
        xform = QFormLayout(xf_box)

        self.xf_app_id = QLineEdit()
        self.xf_app_id.setPlaceholderText("讯飞控制台 → 我的应用 → APPID")
        self.xf_secret_key = QLineEdit()
        self.xf_secret_key.setEchoMode(QLineEdit.Password)
        self.xf_secret_key.setPlaceholderText(
            "SecretKey（Password 模式）—— 长语音鉴权只这一件"
        )

        self.xf_language = QComboBox()
        # 讯飞长语音转写支持 cn/en/ja 等；与 qwen_asr 不同，没有强制单语种限制
        self.xf_language.addItem("中文（cn）", "cn")
        self.xf_language.addItem("英文（en）", "en")
        self.xf_language.addItem("日语（ja）", "ja")
        self.xf_language.addItem("粤语（cn-cantonese）", "cn-cantonese")
        self.xf_language.setToolTip(
            "讯飞长语音 API 支持 cn/en/ja 等多语种。"
            "讯飞也支持自动检测，但显式指定能减少识别误差。"
        )

        self.xf_mock = QCheckBox("Mock 模式（不调网络，返回 fake 文本，便于本地调试）")
        self.xf_mock.setChecked(True)  # 默认开：用户没填凭证时不会真的调 API

        self.xf_max_wait = QSpinBox()
        self.xf_max_wait.setRange(30, 7200)
        self.xf_max_wait.setValue(600)
        self.xf_max_wait.setToolTip(
            "任务轮询超时（秒）。讯飞长音频通常 1-2 分钟出结果，"
            "1 小时以上视频建议 1800+ 秒。"
        )

        xform.addRow("APP ID", self.xf_app_id)
        xform.addRow("Secret Key", self.xf_secret_key)
        xform.addRow("语言", self.xf_language)
        xform.addRow(self.xf_mock)
        xform.addRow("轮询超时（秒）", self.xf_max_wait)

        # === 2026-10-02 新增：转写增强 4 项（角色分离 / 领域 / 口语规整 / 时间戳）===
        self.xf_role_separation = QCheckBox("分离说话人（标注【说话人1】…）")
        self.xf_role_separation.setToolTip(
            "开启后 upload 带 roleType=1 + roleNum=N，输出每段加【说话人N】标签。\n"
            "适合两人对话（访谈 / 对话课 / 师生问答）。\n"
            "⚠ 需账号开通「角色分离」能力，否则讯飞会拒绝该参数——\n"
            "  本程序会自动退回基础参数重试，不会中断转写。\n"
            "⚠ 官方注明该能力「目前还是测试效果达不到商用标准」。\n"
            "  本地 Qwen3-ASR 不支持说话人分离，此开关只对讯飞生效。"
        )
        self.xf_role_separation.toggled.connect(self._on_xf_role_toggled)

        self.xf_role_num = QSpinBox()
        self.xf_role_num.setRange(0, 10)
        self.xf_role_num.setValue(2)
        self.xf_role_num.setToolTip(
            "发音人个数。0 = 自动盲分（让讯飞自己判断人数）；1-10 = 指定人数。\n"
            "两人对话填 2。建议填实际人数，多填会降低聚类准确率。"
        )

        self.xf_pd_domain = QComboBox()
        for code, label in PD_DOMAINS.items():
            self.xf_pd_domain.addItem(label, code)
        self.xf_pd_domain.setToolTip(
            "垂直领域个性化模型。edu=教育 对课堂实录的术语识别率提升最明显。\n"
            "选「通用」则不传 pd 参数（讯飞默认模型）。"
        )

        self.xf_colloquial_proc = QCheckBox("口语规整（自动去「嗯/啊/呃」+ 口癖重复）")
        self.xf_colloquial_proc.setChecked(True)
        self.xf_colloquial_proc.setToolTip(
            "开启后 upload 带 eng_colloqproc=true。\n"
            "课堂实录/访谈口癖多，去掉后给下游 LLM 成稿的文本干净很多。\n"
            "零成本、零权限，建议常开。"
        )

        self.xf_timestamps = QCheckBox("输出时间戳（每段加 [MM:SS] 前缀）")
        self.xf_timestamps.setChecked(True)
        self.xf_timestamps.setToolTip(
            "开启后每段前面加 [MM:SS] 时间戳（读的是 st.bg / lattice2.begin，\n"
            "本来就在响应里，只是之前没解析）。做文章时定位段落很方便。"
        )

        xform.addRow(self.xf_role_separation)
        xform.addRow("发音人数", self.xf_role_num)
        xform.addRow("垂直领域", self.xf_pd_domain)
        xform.addRow(self.xf_colloquial_proc)
        xform.addRow(self.xf_timestamps)
        outer.addWidget(xf_box)

        # 讯飞听见说明（mock 与隐私）
        xf_tip = QLabel(
            "💡 讯飞听见云端识别（xf_asr 引擎专用）：\n"
            "  · 默认 Mock 模式：勾选时不调网络、不需要凭证，返回 fake 中文文本\n"
            "  · 关闭 Mock + 填全 APPID + SecretKey → 调真实 API（数据传到讯飞云端，注意隐私）\n"
            "  · 长语音鉴权只需 APPID + SecretKey 两件套（APIKey 是短音频 ifasr 字段，长语音不用）\n"
            "  · 凭证缺失时会自动降级 Mock + WARNING（不报错）\n"
            "  · 长音频不切段，整段直传（讯飞 server 自己处理 ≤500MB / 5h）\n"
            "  · 注册地址：https://www.xfyun.cn/ → 控制台 → 语音转写（长语音）→ 创建应用\n"
            "  · 免费 5 小时试用包，足够跑通流程；超出按讯飞官方价目计费"
        )
        xf_tip.setWordWrap(True)
        xf_tip.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(xf_tip)

        # 2026-10-02：说话人分离 / 领域优化的账号权限提示
        xf_enh_tip = QLabel(
            "🎙 转写增强（讯飞云端能力，与本地模型算力无关）：\n"
            "  · 说话人分离需账号开通「角色分离」权限；未开通时程序自动退回基础参数重试\n"
            "  · 官方注明该能力「目前还是测试效果达不到商用标准」，远场课堂录音效果会打折\n"
            "  · Qwen3-ASR 本地模型**不支持**说话人分离（只输出连续文本），此开关仅对讯飞生效\n"
            "  · 真正要在本地分离，需换 FunASR + CAM++（spk_model=\"cam++\"，多占显存），本程序暂未接"
        )
        xf_enh_tip.setWordWrap(True)
        xf_enh_tip.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(xf_enh_tip)

        # === 2026-10-02 新增：自定义 POST（端点 + 请求头都可配）===
        mm_box = QGroupBox("自定义（POST）高级 — custom_post 引擎专用")
        mmform = QFormLayout(mm_box)

        self.cp_endpoint = QLineEdit()
        self.cp_endpoint.setPlaceholderText(DEFAULT_ENDPOINT)
        self.cp_endpoint.setToolTip(
            "接口地址（POST）。默认是 MiniMax 官方的 Speech-to-Text：\n"
            f"{DEFAULT_ENDPOINT}\n\n"
            "可改成任意兼容这套请求-响应契约的服务：\n"
            "  · 私有部署 / 自建网关\n"
            "  · 其他云厂商的兼容接口\n"
            "  · 本地转写服务（配合请求头文件里的鉴权）\n\n"
            "必须以 http:// 或 https:// 开头。\n"
            "程序发起的是 multipart/form-data：model / file / response_format /\n"
            "timestamp_level / stream 五个字段 + language 请求头。"
        )

        self.cp_api_key = QLineEdit()
        self.cp_api_key.setEchoMode(QLineEdit.Password)
        self.cp_api_key.setPlaceholderText(
            "写入 Authorization: Bearer <key>（非 Bearer 鉴权请留空，改用请求头文件）"
        )

        self.cp_headers_file = QLineEdit()
        self.cp_headers_file.setPlaceholderText(
            f"留空 = 用程序内 {HEADERS_FILENAME}"
        )
        self.cp_headers_btn = QPushButton("浏览…")
        self.cp_headers_btn.clicked.connect(self._pick_custom_headers_file)
        self.cp_headers_tpl_btn = QPushButton("生成模板")
        self.cp_headers_tpl_btn.clicked.connect(self._make_custom_headers_template)
        self.cp_headers_row = QWidget()
        _hrow = QHBoxLayout(self.cp_headers_row)
        _hrow.setContentsMargins(0, 0, 0, 0)
        _hrow.addWidget(self.cp_headers_file)
        _hrow.addWidget(self.cp_headers_btn)
        _hrow.addWidget(self.cp_headers_tpl_btn)

        self.cp_language = QComboBox()
        for code, label in MINIMAX_LANGUAGES.items():
            self.cp_language.addItem(label, code)
        self.cp_language.setToolTip(
            "留空 = 自动检测主语言 + 中英混说（官方推荐，跨语言内容更稳）。\n"
            "已知语言时显式指定，短音频 / 术语密集内容更稳。\n"
            "注意：这是 HTTP **header** 不是 form 字段；\n"
            "在请求头文件里写 language 会覆盖这里的值。"
        )

        self.cp_mock = QCheckBox("Mock 模式（不调网络，返回 fake 文本，便于本地调试）")
        self.cp_mock.setChecked(True)  # 默认开：没填 key 时不会真的调 API

        self.cp_role_separation = QCheckBox("分离说话人（云端 diarization，标注【S1】…）")
        self.cp_role_separation.setToolTip(
            "开启后用 response_format=verbose_json，输出每段带【S1】/【S2】标签。\n"
            "需要服务端支持 verbose_json（响应里带 segments[] + n_speakers）。\n"
            "⚠ 超过默认 500 秒会自动切段，而**每段独立重编 S1/S2、"
            "响应里没有跨段身份信息**：\n"
            "  人数相同时只能按编号对齐，两人音色接近时可能认错人。\n"
            "⚠ 短素材（不切段）分离最可靠。"
        )

        self.cp_timestamps = QCheckBox("输出时间戳（每段加 [MM:SS] 前缀）")
        self.cp_timestamps.setChecked(True)
        self.cp_timestamps.setToolTip(
            "开启后每段前面加 [MM:SS] 时间戳（读 segments[].start，单位是**秒**）。\n"
            "与 xf_asr / funasr 的时间戳格式保持一致。"
        )

        self.cp_max_wait = QSpinBox()
        self.cp_max_wait.setRange(30, 7200)
        self.cp_max_wait.setValue(300)
        self.cp_max_wait.setToolTip(
            "单次 HTTP 请求超时（秒）。默认按一次���返回（非流式），\n"
            "500 秒音频约几秒到几十秒返回。切段后每段各用一次。"
        )

        mmform.addRow("接口地址（POST）", self.cp_endpoint)
        mmform.addRow("API Key", self.cp_api_key)
        mmform.addRow("请求头文件", self.cp_headers_row)
        mmform.addRow("语言", self.cp_language)
        mmform.addRow(self.cp_mock)
        mmform.addRow(self.cp_role_separation)
        mmform.addRow(self.cp_timestamps)
        mmform.addRow("单请求超时（秒）", self.cp_max_wait)
        outer.addWidget(mm_box)

        mm_tip = QLabel(
            "💡 自定义 POST 云端 ASR（默认按 MiniMax asr-1.0 契约实现）：\n"
            "  · 接口地址可改 → 指向私有部署 / 网关 / 任何兼容服务\n"
            "  · 请求头走可编辑的 text 文件（每行 Key: Value，# 注释）\n"
            "    该文件可能含密钥，已加入 .gitignore；程序只打印键名不打印值\n"
            "  · 鉴权两处都行：API Key 输入框（Bearer）或请求头文件里写 Authorization\n"
            "  · 请求是 multipart/form-data（与讯飞相反：讯飞必须 query string + raw body）\n"
            "  · **默认硬限制：单请求 ≤ 500 秒 / 50 MB**，超了直接报错不截断\n"
            "    → 程序自动切 450 秒一段并压成单声道 16kHz mp3（识别精度不受影响）\n"
            "    → 换私有部署若限制不同，改 custom_post_asr.py 顶部 MAX_DURATION_SEC / MAX_FILE_BYTES\n"
            "  · verbose_json 才返回 segments[] + n_speakers（说话人分离 + 时间戳）\n"
            "  · 错误体是 OpenAI 风格（400/401/402/403/404/413/422/429/500）\n"
            "  · MiniMax Key 申请：https://platform.minimax.cn/user-center/basic-information/interface-key"
        )
        mm_tip.setWordWrap(True)
        mm_tip.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(mm_tip)

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

    def _on_tr_engine_changed(self, _index: int) -> None:
        """2026-09-27 防呆：切到云端引擎时自动同步 Mock 状态。

        触发场景：
        - 用户在「默认 ASR 引擎」下拉切到 xf_asr / minimax_asr
        - 若凭证都填了 + Mock 仍勾选 → 自动取消 Mock
          （因为凭证齐全还勾 mock = 实际走 mock 浪费 API 调用）
        - 若凭证缺失 → 保持 Mock 勾选（避免启动时悄悄调 API 失败）
        - 切回其他引擎（funasr/whisper/qwen_asr）→ 不动云端引擎的 GroupBox
          （用户可能只是临时切走，回头还要切回来用）
        """
        engine = self.tr_engine.currentData()
        if engine == "xf_asr":
            has_creds = bool(
                self.xf_app_id.text().strip() and self.xf_secret_key.text().strip()
            )
            label = "讯飞听见"
            mock_box = self.xf_mock
        elif engine == "custom_post":
            # 凭证可能在 API Key 输入框，也可能在请求头文件的 Authorization 里
            has_creds = bool(self.cp_api_key.text().strip())
            if not has_creds:
                try:
                    from ...media.custom_post_asr import load_headers_file

                    hp = (self.cp_headers_file.text().strip()
                          or str(default_headers_file()))
                    has_creds = any(
                        k.lower() == "authorization" and v.strip()
                        for k, v in load_headers_file(Path(hp)).items()
                    )
                except Exception:
                    has_creds = False
            label = "自定义 POST"
            mock_box = self.cp_mock
        else:
            return
        if has_creds and mock_box.isChecked():
            mock_box.setChecked(False)
            QMessageBox.information(
                self,
                f"{label} 已自动取消 Mock",
                f"检测到 {engine} 引擎 + 凭证齐全，自动取消 Mock 模式（避免误调 mock）。\n"
                f"如确实要本地调试，可重新勾选。",
            )

    def _on_xf_role_toggled(self, checked: bool) -> None:
        """2026-10-02：说话人分离开关 → 「发音人数」随之 enable/disable。

        关掉分离时人数没有意义，置灰避免用户以为填了会生效。
        """
        self.xf_role_num.setEnabled(bool(checked))

    def _on_funasr_speaker_toggled(self, checked: bool) -> None:
        """2026-10-02：本地说话人分离开关 → 模型下拉随之 enable/disable。"""
        self.funasr_spk_model.setEnabled(bool(checked))

    def _pick_custom_headers_file(self) -> None:
        """选请求头文件（custom_post 引擎专用）。"""
        cur = (self.cp_headers_file.text().strip()
               or str(default_headers_file()))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择自定义 POST ASR 的请求头文件",
            cur,
            "Text files (*.txt);;All files (*.*)",
        )
        if path:
            self.cp_headers_file.setText(path)

    def _make_custom_headers_template(self) -> None:
        """生成请求头文件模板（已存在则提示，不静默覆盖）。"""
        cur = (self.cp_headers_file.text().strip()
               or str(default_headers_file()))
        path = Path(cur)
        existed = path.exists()
        try:
            write_headers_template(path, overwrite=False)
        except OSError as e:
            QMessageBox.warning(
                self, "生成模板失败", f"无法写入 {path}：\n{e}"
            )
            return
        if existed:
            QMessageBox.information(
                self,
                "模板已存在",
                f"{path}\n\n已存在，未覆盖（避免把你手写的请求头冲掉）。\n"
                f"要重新生成请先删除该文件或换个路径。",
            )
        else:
            self.cp_headers_file.setText(str(path))
            QMessageBox.information(
                self,
                "已生成请求头模板",
                f"{path}\n\n格式：每行一条 Key: Value，# 开头是注释。\n"
                f"留空「API Key」时，在这里写 Authorization: <你的凭据> 也能鉴权。\n"
                f"⚠ 该文件可能含密钥，已在 .gitignore 里，不要提交到仓库。",
            )

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

        # 2026-10-02：FunASR 本地说话人分离（先设 enable 再设值，避免 toggle 抢跑）
        self.funasr_spk_model.setEnabled(bool(tr.get("funasr_speaker", False)))
        self.funasr_speaker.setChecked(bool(tr.get("funasr_speaker", False)))
        fspk = str(tr.get("funasr_spk_model") or "cam++")
        fsidx = self.funasr_spk_model.findData(fspk)
        self.funasr_spk_model.setCurrentIndex(fsidx if fsidx >= 0 else 0)
        self.funasr_timestamps.setChecked(bool(tr.get("funasr_timestamps", True)))

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

        # 讯飞听见（xf_asr 引擎专用，2026-09-27 新增；2026-09-27 订正：删 api_key 字段）
        xa = (tr.get("xf_asr") or {})
        self.xf_app_id.setText(str(xa.get("app_id") or ""))
        self.xf_secret_key.setText(str(xa.get("secret_key") or ""))
        xlang = str(xa.get("language") or "cn")
        xidx = self.xf_language.findData(xlang)
        self.xf_language.setCurrentIndex(xidx if xidx >= 0 else 0)
        # 缺凭证时默认 mock=true（避免启动时悄悄调 API 失败）
        self.xf_mock.setChecked(bool(xa.get("mock", True)))
        try:
            self.xf_max_wait.setValue(int(xa.get("max_wait_seconds") or 600))
        except (TypeError, ValueError):
            self.xf_max_wait.setValue(600)

        # 2026-10-02：转写增强 5 项（先设 enable 状态，再设值，避免 toggle 抢跑）
        self.xf_role_num.setEnabled(bool(xa.get("role_separation", False)))
        self.xf_role_separation.setChecked(bool(xa.get("role_separation", False)))
        try:
            self.xf_role_num.setValue(int(xa.get("role_num", 2)))
        except (TypeError, ValueError):
            self.xf_role_num.setValue(2)
        pd_code = str(xa.get("pd_domain") or "")
        pd_idx = self.xf_pd_domain.findData(pd_code)
        self.xf_pd_domain.setCurrentIndex(pd_idx if pd_idx >= 0 else 0)
        self.xf_colloquial_proc.setChecked(bool(xa.get("colloquial_proc", True)))
        self.xf_timestamps.setChecked(bool(xa.get("timestamps", True)))

        # 自定义 POST 云端识别（custom_post 引擎，2026-10-02；旧名 minimax_asr 兼容）
        ca = (tr.get("custom_post") or tr.get("minimax_asr") or {})
        self.cp_endpoint.setText(
            str(ca.get("endpoint") or DEFAULT_ENDPOINT)
        )
        self.cp_api_key.setText(str(ca.get("api_key") or ""))
        self.cp_headers_file.setText(str(ca.get("headers_file") or ""))
        clang = str(ca.get("language") or "")
        cidx = self.cp_language.findData(clang)
        self.cp_language.setCurrentIndex(cidx if cidx >= 0 else 0)
        self.cp_mock.setChecked(bool(ca.get("mock", True)))
        self.cp_role_separation.setChecked(bool(ca.get("role_separation", False)))
        self.cp_timestamps.setChecked(bool(ca.get("timestamps", True)))
        try:
            self.cp_max_wait.setValue(int(ca.get("max_wait_seconds") or 300))
        except (TypeError, ValueError):
            self.cp_max_wait.setValue(300)

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
                # 2026-10-02：本地说话人分离（FunASR + CAM++）
                "funasr_speaker": self.funasr_speaker.isChecked(),
                "funasr_spk_model": self.funasr_spk_model.currentData() or "cam++",
                "funasr_timestamps": self.funasr_timestamps.isChecked(),
                "qwen_asr": {
                    "model_id": self.qwen_model.currentData()
                    or "Qwen/Qwen3-ASR-0.6B",
                    "context_file": self.qwen_context.text().strip(),
                    "hf_home": self.qwen_hf_home.text().strip()
                    or r"E:\AI_Models\Qwen3-ASR",
                    "language": self.qwen_language.currentData() or "Chinese",
                    "device": self.qwen_device.currentData() or "auto",
                },
                "xf_asr": {
                    "app_id": self.xf_app_id.text().strip(),
                    "secret_key": self.xf_secret_key.text().strip(),
                    "language": self.xf_language.currentData() or "cn",
                    "mock": self.xf_mock.isChecked(),
                    "max_wait_seconds": self.xf_max_wait.value(),
                    # 2026-10-02 新增：转写增强
                    "role_separation": self.xf_role_separation.isChecked(),
                    "role_num": self.xf_role_num.value(),
                    "pd_domain": self.xf_pd_domain.currentData() or "",
                    "colloquial_proc": self.xf_colloquial_proc.isChecked(),
                    "timestamps": self.xf_timestamps.isChecked(),
                },
                "custom_post": {
                    "endpoint": self.cp_endpoint.text().strip() or DEFAULT_ENDPOINT,
                    "api_key": self.cp_api_key.text().strip(),
                    "headers_file": self.cp_headers_file.text().strip(),
                    "language": self.cp_language.currentData() or "",
                    "mock": self.cp_mock.isChecked(),
                    "role_separation": self.cp_role_separation.isChecked(),
                    "timestamps": self.cp_timestamps.isChecked(),
                    "max_wait_seconds": self.cp_max_wait.value(),
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
            # 2026-09-27 订正：清理 xf_asr 残留冗余字段
            # APIKey 是短音频 ifasr 实时接口的字段，长语音 lfasr / ifasr_new 不参与签名
            # 此前 6 字段版遗留的 api_key 在新代码里既不读也不写，但磁盘上还残留看着碍眼
            xa_existing = current.get("transcribe", {}).get("xf_asr")
            if isinstance(xa_existing, dict):
                xa_existing.pop("api_key", None)
            deep_update(current, self._collect_updates())
            save_config(current)
            self._config = current
            QMessageBox.information(self, "设置", "设置已保存")
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
