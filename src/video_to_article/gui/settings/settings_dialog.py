"""Settings dialog — full config coverage for Phase C."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
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
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
    API_STYLES,
    DEFAULT_ENDPOINT,
    HEADERS_FILENAME,
    MINIMAX_LANGUAGES,
    _looks_like_placeholder_url,
    build_stt_endpoint,
    default_headers_file,
    write_headers_template,
)
from ...providers.llm_models import fetch_model_limits, fetch_models, format_limit_hint
from ..asr_engines import ASR_ENGINE_LABELS, normalize_engine
from ..profile_store import (
    PROFILE_SPEC_ASR,
    PROFILE_SPEC_COVER,
    PROFILE_SPEC_HOST,
    PROFILE_SPEC_LLM,
    ProfileManagerDialog,
    ProfileMixin,
    ProfileSpec,
)
from ...providers.llm_providers import (
    PROTOCOL_LABELS,
    VENDOR_REGISTRY,
    effective_vendor,
    mask_secret,
    resolve_protocol,
    vendor_base_url,
)


def _scroll_page(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    return scroll


# 2026-10-04：四处档案区的完整声明。
# 机制（存/应用/管理/读写 config）全在 profile_store.ProfileMixin，这里只声明
# 「存什么字段 / 存到 config 哪 / 列表显示什么」——加第五处档案只需在这里加一段。
_PROFILE_SPECS: dict[str, ProfileSpec] = {
    "llm": ProfileSpec(
        prefix="llm",
        config_path=("llm",),
        spec=PROFILE_SPEC_LLM,
        kind_label="大模型",
        hint=(
            "已保存的大模型配置档案。选中一个点「应用到当前」即可套用。\n"
            "档案是**模板库**：改档案不会自动改下方字段，"
            "需点「应用到当前」才会覆盖（避免「以为在改档案，其实改的是当前配置」）。"
        ),
        fields=lambda d: {
            "protocol": d.llm_protocol.currentData() or "openai_chat",
            "vendor": d.llm_vendor.currentData() or "custom",
            "base_url": d.llm_base_url.text().strip(),
            "api_key": d.llm_api_key.text().strip(),
            "model": d.llm_model.currentText().strip(),
            "temperature": d._ui_temperature(),
            "max_tokens": d.llm_max_tokens.value(),
            "timeout_seconds": d.llm_timeout.value(),
            "max_retries": d.llm_retries.value(),
            "models_cache": list(d._last_fetched_models),
            "models_fetched_at": int(time.time()) if d._last_fetched_models else 0,
        },
        apply=lambda d, p: d._apply_llm_profile_to_ui(p),
        combo_fmt=lambda p: (
            f"{p.get('label', p.get('id'))}  ·  {p.get('model') or '（无模型）'}"
        ),
    ),
    "cp": ProfileSpec(
        prefix="cp",
        config_path=("transcribe", "custom_post"),
        spec=PROFILE_SPEC_ASR,
        kind_label="自定义（POST）",
        hint=(
            "已保存的自定义 POST 配置档案。选中一个点「应用到当前」即可套用。\n"
            "档案是**模板库**：改档案不会自动改上方字段，"
            "需点「应用到当前」才会覆盖。\n\n"
            "每个档案可指向**不同的请求头文件**，这样不同 Provider 的\n"
            "非 Bearer 鉴权（自定义头 / 私有网关 token）就能各存各的，互不干扰。"
        ),
        dup_hint=(
            "「复制」会连 API Key 和请求头文件路径一起复制。\n"
            "若两个 Provider 用不同的请求头文件，记得复制后改一下路径。"
        ),
        fields=lambda d: {
            "api_style": d.cp_api_style.currentData() or "openai_compat",
            "endpoint": d.cp_endpoint.text().strip(),
            "model": d.cp_model.text().strip(),
            "audio_url": d.cp_audio_url.text().strip(),
            "api_key": d.cp_api_key.text().strip(),
            "headers_file": d.cp_headers_file.text().strip(),
            "language": d.cp_language.currentData() or "",
            "mock": d.cp_mock.isChecked(),
            "role_separation": d.cp_role_separation.isChecked(),
            "speaker_count": d.cp_speaker_count.value(),
            "timestamps": d.cp_timestamps.isChecked(),
            "colloquial_proc": d.cp_colloquial_proc.isChecked(),
            "max_wait_seconds": d.cp_max_wait.value(),
        },
        apply=lambda d, p: d._apply_cp_profile_to_ui(p),
        combo_fmt=lambda p: (
            f"{p.get('label', p.get('id'))}  ·  "
            f"{(str(p.get('endpoint') or '').split('//')[-1][:28]) or '（无端点）'}"
        ),
    ),
    "cover": ProfileSpec(
        prefix="cover",
        config_path=("ai_cover",),
        spec=PROFILE_SPEC_COVER,
        kind_label="AI 封面",
        hint=(
            "已保存的 AI 封面配置档案（Provider / Base URL / Key / 生图模型…）。\n"
            "常用于在多套生图服务之间切换（ModelScope / OpenAI 兼容 / xAI 等）。\n"
            "档案是**模板库**：需点「应用到当前」才会覆盖上方字段。"
        ),
        fields=lambda d: {
            "provider": d.cover_provider.text().strip(),
            "base_url": d.cover_base_url.text().strip(),
            "api_key": d.cover_api_key.text().strip(),
            "model": d.cover_model.text().strip(),
            "edit_model": d.cover_edit_model.text().strip(),
            "mode": d.cover_mode.text().strip(),
            "size": d.cover_size.text().strip(),
            "output_format": d.cover_format.text().strip(),
            "brand": d.cover_brand.text().strip(),
            "pipeline": d._cover_pipeline_from_ui(),
        },
        apply=lambda d, p: d._apply_cover_profile_to_ui(p),
        combo_fmt=lambda p: (
            f"{p.get('label', p.get('id'))}  ·  "
            f"{p.get('provider') or '?'} / {p.get('model') or '无模型'}"
        ),
    ),
    "host": ProfileSpec(
        prefix="host",
        config_path=("image_host",),
        spec=PROFILE_SPEC_HOST,
        kind_label="图床",
        hint=(
            "已保存的图床配置档案（Provider / API URL / Token…）。\n"
            "常用于在多套图床（Chevereto / 自建 / 各家 API）之间切换。\n"
            "档案是**模板库**：需点「应用到当前」才会覆盖上方字段。"
        ),
        dup_hint="「复制」会连 Token 一起复制（存主图床 + 备用图床常用）。",
        fields=lambda d: {
            "enable": d.host_enable.isChecked(),
            "provider": d.host_provider.text().strip(),
            "api_url": d.host_api_url.text().strip(),
            "token": d.host_token.text().strip(),
            "token_field": d.host_token_field.text().strip() or "token",
            "file_field": d.host_file_field.text().strip() or "image",
            "url_json_path": d.host_url_path.text().strip() or "url",
            "timeout_seconds": int(d.host_timeout.value()),
            "extra_fields": d.host_extra.toPlainText().strip(),
            # S3 系（r2 / oss）—— 2026-10-04，DashScope 音频中转
            "bucket": d.host_bucket.text().strip(),
            "endpoint": d.host_endpoint.text().strip(),
            "region": d.host_region.text().strip(),
            "access_key_id": d.host_ak_id.text().strip(),
            "access_key_secret": d.host_ak_secret.text().strip(),
            "public_base_url": d.host_public_base.text().strip(),
            "presign_seconds": int(d.host_presign.value()),
        },
        apply=lambda d, p: d._apply_host_profile_to_ui(p),
        combo_fmt=lambda p: (
            f"{p.get('label', p.get('id'))}  ·  {p.get('provider') or '?'}"
        ),
    ),
}


class SettingsDialog(ProfileMixin, QDialog):
    PROFILES = list(_PROFILE_SPECS.values())

    @staticmethod
    def _spec(key: str) -> ProfileSpec:
        return _PROFILE_SPECS[key]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("一览成文 — 设置")
        self.resize(640, 560)
        self._config: dict[str, Any] = {}
        # 2026-10-04：最近一次抓到的模型列表，存档案时一起带进 models_cache
        self._last_fetched_models: list = []
        # 2026-10-05：抓模型时顺带拿到的各模型输出上限（部分厂商不返回）
        # {model_id: {"max_output_tokens": int, "context_window": int}}
        self._last_model_limits: dict = {}

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
        # 2026-10-04：右下角「管理配置档案…」。
        # 放**左侧**而不是挤在 Save/Cancel 中间，是为了不破坏「Save 在最右」的肌肉记忆。
        # 档案区现在有四处（LLM / 自定义 POST / AI 封面 / 图床），所以这里先问管哪一块。
        self.profiles_manage_btn = QPushButton("管理配置档案…")
        self.profiles_manage_btn.clicked.connect(self._open_profile_manager)
        buttons.layout().insertWidget(0, self.profiles_manage_btn)
        root.addWidget(buttons)

        self.load_from_disk()
        # 2026-10-04：load_from_disk 之后要重跑一次显隐同步——
        # 建 Tab 时引擎还是默认值，config 里的实际引擎是这时才读进来的
        self._sync_engine_visibility()

    def _on_llm_vendor_changed(self) -> None:
        """切厂商时预填推荐 Base URL（可手改，不强制）。

        只在 Base URL 为空、或还是上一个厂商的推荐值时才覆盖——
        否则会把用户手填的自定义地址冲掉。
        """
        vendor = self.llm_vendor.currentData() or ""
        suggested = vendor_base_url(vendor)
        if not suggested:
            return
        cur = self.llm_base_url.text().strip()
        # 当前为空，或等于任一厂商的推荐值（说明用户没手动改过）→ 才覆盖
        if not cur or cur in {vendor_base_url(v) for v in VENDOR_REGISTRY}:
            self.llm_base_url.setText(suggested)

    def _on_fetch_models_clicked(self) -> None:
        """抓取服务端模型列表（后台线程，不阻塞 UI）。"""
        base_url = self.llm_base_url.text().strip()
        api_key = self.llm_api_key.text().strip()
        if not base_url:
            QMessageBox.warning(self, "抓取模型", "请先填写 Base URL。")
            return

        # 抓取期间禁用按钮，防重复点击
        self.llm_model_fetch_btn.setEnabled(False)
        self.llm_model_fetch_btn.setText("抓取中…")

        from PySide6.QtCore import QThread

        class _FetchThread(QThread):
            done = Signal(object, object, object)  # (models, error, limits)

            def __init__(self, url: str, key: str):
                super().__init__()
                self._url = url
                self._key = key

            def run(self) -> None:
                try:
                    models, err = fetch_models(self._url, self._key)
                    limits = {} if err else fetch_model_limits(self._url, self._key)
                except Exception as e:  # 兜底：线程里绝不让异常冒出去
                    models, err, limits = [], f"{type(e).__name__}: {e}", {}
                self.done.emit(models, err, limits)

        self._fetch_thread = _FetchThread(base_url, api_key)
        self._fetch_thread.done.connect(self._on_fetch_models_done)
        self._fetch_thread.finished.connect(
            lambda: self.llm_model_fetch_btn.setEnabled(True)
        )
        self._fetch_thread.start()

    def _on_fetch_models_done(self, models: list, err, limits: dict = None) -> None:
        """抓取线程回调。⚠ 失败时**绝不清空** Model 已有的内容。"""
        self.llm_model_fetch_btn.setText("⟳ 抓取")
        current = self.llm_model.currentText().strip()
        if err:
            QMessageBox.warning(
                self,
                "抓取模型失败",
                f"{err}\n\n"
                f"不影响使用——Model 仍可手动填写。\n"
                f"（已填内容不会被清空：{current or '（空）'}）",
            )
            return

        # 保留用户已填的值（即使在列表里也别冲掉它——那可能是服务端已下线的模型）
        self._last_fetched_models = list(models)
        self._last_model_limits = dict(limits or {})
        self.llm_model.clear()
        if current:
            self.llm_model.addItem(current)
        self.llm_model.addItems(models)
        self.llm_model.setCurrentIndex(0)

        # 顺带把「当前模型的输出上限」告诉用户 —— max_tokens 配超会被服务端
        # 400 静默拒绝（发生在请求发出前），界面上只剩「成稿没生成」一条线索。
        target = current or (models[0] if models else "")
        hint = format_limit_hint(target, self._last_model_limits) if target else ""
        msg = (
            f"已获取 {len(models)} 个模型，已填入下拉框。\n\n"
            + "\n".join(models[:20])
            + (f"\n… 还有 {len(models) - 20} 个" if len(models) > 20 else "")
        )
        if hint:
            msg += f"\n\n💡 {hint}"
            ceiling = (self._last_model_limits.get(target) or {}).get("max_output_tokens")
            if ceiling and self.llm_max_tokens.value() > ceiling:
                msg += (
                    f"\n⚠ 你当前填的 max_tokens="
                    f"{self.llm_max_tokens.value():,} 超过了这个上限，"
                    f"保存后生成成稿会被服务端拒绝（且**界面上看不出原因**）。"
                    f"\n   建议改成 {ceiling:,} 或以下。"
                )
        else:
            msg += (
                "\n\n💡 该服务未在 /models 里报告输出上限，"
                "max_tokens 请按服务商文档填写（配超会被静默拒绝）。"
            )
        QMessageBox.information(self, "抓取模型成功", msg)

    # 2026-10-04：档案机制（存/应用/管理/读写）已抽到 profile_store.ProfileMixin，
    # 四处档案区共用同一套实现。本类只在 PROFILES 里声明各自的字段。
    # ---------- 档案 apply（ProfileMixin 会调用）----------

    def _apply_llm_profile_to_ui(self, prof: dict) -> None:
        """把大模型档案套到 UI 控件上。"""
        self.llm_protocol.setCurrentIndex(
            max(0, self.llm_protocol.findData(prof.get("protocol") or "openai_chat"))
        )
        self.llm_vendor.setCurrentIndex(
            max(0, self.llm_vendor.findData(prof.get("vendor") or "custom"))
        )
        self.llm_base_url.setText(str(prof.get("base_url") or ""))
        self.llm_api_key.setText(str(prof.get("api_key") or ""))
        cache = prof.get("models_cache") or []
        self.llm_model.clear()
        self.llm_model.addItems([str(m) for m in cache])
        self.llm_model.setCurrentText(str(prof.get("model") or ""))
        self.llm_temperature.setText(str(prof.get("temperature", 0.3)))
        for setter, key, default in (
            (self.llm_max_tokens.setValue, "max_tokens", 12000),
            (self.llm_timeout.setValue, "timeout_seconds", 180),
            (self.llm_retries.setValue, "max_retries", 3),
        ):
            try:
                setter(int(prof.get(key) or default))
            except (TypeError, ValueError):
                setter(default)

    def _apply_cp_profile_to_ui(self, prof: dict) -> None:
        """把自定义 POST 档案套到 UI 控件上。"""
        sidx = self.cp_api_style.findData(
            str(prof.get("api_style") or "openai_compat")
        )
        self.cp_api_style.setCurrentIndex(sidx if sidx >= 0 else 0)
        self.cp_endpoint.setText(str(prof.get("endpoint") or ""))
        self.cp_model.setText(str(prof.get("model") or ""))
        self.cp_audio_url.setText(str(prof.get("audio_url") or ""))
        self.cp_api_key.setText(str(prof.get("api_key") or ""))
        self.cp_headers_file.setText(str(prof.get("headers_file") or ""))
        li = self.cp_language.findData(str(prof.get("language") or ""))
        self.cp_language.setCurrentIndex(li if li >= 0 else 0)
        self.cp_mock.setChecked(bool(prof.get("mock", True)))
        self.cp_role_separation.setChecked(bool(prof.get("role_separation", False)))
        try:
            self.cp_speaker_count.setValue(int(prof.get("speaker_count") or 2))
        except (TypeError, ValueError):
            self.cp_speaker_count.setValue(2)
        self.cp_timestamps.setChecked(bool(prof.get("timestamps", True)))
        self.cp_colloquial_proc.setChecked(bool(prof.get("colloquial_proc", False)))
        try:
            self.cp_max_wait.setValue(int(prof.get("max_wait_seconds") or 300))
        except (TypeError, ValueError):
            self.cp_max_wait.setValue(300)
        self._on_cp_style_changed()

    def _apply_cover_profile_to_ui(self, prof: dict) -> None:
        """把 AI 封面档案套到 UI 控件上。"""
        self._apply_cover_pipeline_to_ui(
            str(prof.get("pipeline") or COVER_MODE_OFF)
        )
        for attr, key in (
            ("cover_provider", "provider"),
            ("cover_base_url", "base_url"),
            ("cover_api_key", "api_key"),
            ("cover_model", "model"),
            ("cover_edit_model", "edit_model"),
            ("cover_mode", "mode"),
            ("cover_size", "size"),
            ("cover_format", "output_format"),
            ("cover_brand", "brand"),
        ):
            getattr(self, attr).setText(str(prof.get(key) or ""))

    def _apply_host_profile_to_ui(self, prof: dict) -> None:
        """把图床档案套到 UI 控件上。"""
        self.host_enable.setChecked(bool(prof.get("enable")))
        for attr, key in (
            ("host_provider", "provider"),
            ("host_api_url", "api_url"),
            ("host_token", "token"),
            ("host_token_field", "token_field"),
            ("host_file_field", "file_field"),
            ("host_url_path", "url_json_path"),
            # S3 系（r2 / oss）—— 2026-10-04
            ("host_bucket", "bucket"),
            ("host_endpoint", "endpoint"),
            ("host_region", "region"),
            ("host_ak_id", "access_key_id"),
            ("host_ak_secret", "access_key_secret"),
            ("host_public_base", "public_base_url"),
        ):
            getattr(self, attr).setText(str(prof.get(key) or ""))
        try:
            self.host_presign.setValue(int(prof.get("presign_seconds") or 0))
        except (TypeError, ValueError):
            self.host_presign.setValue(0)
        self._on_host_provider_changed()
        try:
            self.host_timeout.setValue(int(prof.get("timeout_seconds") or 180))
        except (TypeError, ValueError):
            self.host_timeout.setValue(180)
        self.host_extra.setPlainText(str(prof.get("extra_fields") or "{}"))

    def _ui_temperature(self) -> float:
        try:
            return float(self.llm_temperature.text().strip() or 0.3)
        except ValueError:
            return 0.3

    def _open_profile_manager(self) -> None:
        """右下角「管理配置档案…」的入口。

        档案区有四处，所以先让用户选管哪一块——比塞四个按钮到按钮行干净，
        也不破坏 Save/Cancel 的位置记忆。各 Tab 内部还有自己的「管理…」直达按钮。
        """
        keys = [s.config_path for s in self.PROFILES]
        labels = [s.kind_label for s in self.PROFILES]
        pairs = [
            f"{label}（{' > '.join(path)}）"
            for label, path in zip(labels, keys)
        ]
        choice, ok = QInputDialog.getItem(
            self, "管理配置档案", "要管理哪一类配置档案？", pairs, 0, False
        )
        if not ok or choice not in pairs:
            return
        self._profile_manage(self.PROFILES[pairs.index(choice)])

    def _eng_block(self, engine: str, *widgets: QWidget) -> None:
        """登记「这个 widget 属于哪个引擎」，供 _sync_engine_visibility 显隐用。"""
        self._engine_blocks.setdefault(engine, []).extend(widgets)

    def _sync_engine_visibility(self) -> None:
        """只显示当前引擎的设置分组，除非勾了「显示全部」。

        2026-10-04：转写 Tab 原本把 5 个引擎的设置全铺开，页面长到要滚很久。
        现在默认只显示所选引擎那一个；勾「显示全部引擎的设置」恢复原样。

        ⚠ 隐藏只是 setVisible(False)，**参数照常读写**——不会丢配置。
        """
        show_all = self.tr_show_all.isChecked()
        current = normalize_engine(self.tr_engine.currentData() or "funasr")
        for engine, widgets in self._engine_blocks.items():
            visible = show_all or (engine == current)
            for w in widgets:
                w.setVisible(visible)

    def _on_host_provider_changed(self) -> None:
        """按 provider 切字段组：**一次只显示该填的那一组**。

        保持 host_provider 是自由文本框（档案机制按 .text() 取值，
        改 QComboBox 会牵动 spec/apply/save/load 四处），靠 textChanged
        做识别 —— 用户一敲 r2 就自动切换，不用先去下拉里选。

        ⚠ 两组字段的标签很像但完全不是一回事：
        easyimage  = API URL + Token（multipart 图床）
        r2 / oss   = endpoint + bucket + 密钥（S3）
        平铺在一起时用户会把 endpoint 填进「API URL」，所以必须互斥显示。
        """
        raw = str(self.host_provider.text() or "").strip().lower()
        is_s3 = raw in ("r2", "oss")
        self._host_s3_box.setVisible(is_s3)
        self._host_easy_box.setVisible(not is_s3)
        if is_s3:
            self._host_hint.setText(
                "✅ 当前填**下面这组 S3 字段**（上面「传统图床」那组不用管）：\n"
                + (
                    "  · R2  endpoint = https://<AccountID>.r2.cloudflarestorage.com\n"
                    "    AccountID 在 Cloudflare 后台 R2 页面右侧\n"
                    "  · OSS endpoint = https://oss-cn-<地域>.aliyuncs.com\n"
                    "    地域建议与你的百炼同区，如 cn-beijing\n"
                    "  · ⚠ 别再往「API URL」填 endpoint 了，那是 easyimage 才用的"
                    if raw == "r2"
                    else "  · endpoint = https://oss-cn-<地域>.aliyuncs.com"
                    "（如 cn-beijing，与百炼同区）\n"
                    "  · ⚠ 别再往「API URL」填 endpoint 了，那是 easyimage 才用的"
                )
            )
        else:
            self._host_hint.setText(
                "💡 图床/对象存储的用途：AI 封面出图 + 给 DashScope 异步 ASR 中转音频。\n"
                "  · 当前 provider=easyimage，填上面「传统图床」那组（API URL + Token）\n"
                "  · 若要接 Cloudflare R2 / 阿里云 OSS，把 Provider 改成 r2 / oss，\n"
                "    字段会自动切换成 S3 那组（endpoint / bucket / 密钥）\n"
                "  · DashScope 硬性要求音频是**公网 URL**，所以桶要外部可读：\n"
                "    R2 开 Public Development URL，OSS 设 ACL 公共读；\n"
                "    不想开公读就把「签名有效期」设成 3600，走临时签名直链"
            )

    def _on_cp_style_changed(self) -> None:
        """切 API 风格：启用/禁用该风格专属的字段，并刷新端点预览。"""
        is_ds = self.cp_api_style.currentData() == "dashscope_async"
        self.cp_model.setEnabled(is_ds)
        self.cp_audio_url.setEnabled(is_ds)
        self.cp_speaker_count.setEnabled(is_ds and self.cp_role_separation.isChecked())
        self.cp_colloquial_proc.setEnabled(is_ds)
        self.cp_role_separation.setText(
            "分离说话人（DashScope diarization_enabled / OpenAI 兼容 verbose_json）"
            if is_ds
            else "分离说话人（云端 diarization，标注【S1】…）"
        )
        self._update_cp_endpoint_preview()

    def _update_cp_endpoint_preview(self) -> None:
        """实时显示最终请求地址 —— 解决「URL 到底该填到哪一层」的困惑。"""
        style = self.cp_api_style.currentData() or "openai_compat"
        base = self.cp_endpoint.text().strip()
        if not base:
            self.cp_endpoint_preview.setText("（填 Base URL 后这里会显示最终请求地址）")
            return
        if _looks_like_placeholder_url(base):
            # ⚠ 提前预警：这类占位符真跑到转写那一步，报出来的是
            #    'Failed to resolve xn--...'（中文域名被 punycode 编码），
            #    看着像 DNS 故障，实质是没填 —— 在这里说出来最省事。
            self.cp_endpoint_preview.setText(
                "⚠ 这看起来还是模板占位符，没换成真实域名。"
                "（中文域名会被转成 xn--… 报 DNS 错，很难看出是没填）"
            )
            self.cp_endpoint_preview.setStyleSheet("color: #b26a00; font-size: 11px;")
            return
        self.cp_endpoint_preview.setStyleSheet("color: #888; font-size: 11px;")
        try:
            url = build_stt_endpoint(base, style)
        except Exception:
            self.cp_endpoint_preview.setText("（Base URL 格式有问题）")
            return
        extra = ""
        if style == "dashscope_async":
            extra = "　轮询 {base}/tasks/{task_id}"
        self.cp_endpoint_preview.setText(f"→ 实际请求：{url}{extra}")

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
        outer = QVBoxLayout(page)
        form = QFormLayout()
        # 2026-10-04：标签列固定宽度，让「协议」「厂商」「API Key」等长短不一的
        # 标签左边缘对齐（QFormLayout 默认按最长标签算宽度，但我们显式锁定，
        # 这样以后增删行不会让整列标签宽度跳来跳去）
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        # ---- 配置档案区（2026-10-04）—— 机制在 profile_store.ProfileMixin ----
        outer.addWidget(self._build_profile_box(self._spec("llm")))
        # ---- 基础字段 ----
        self.llm_protocol = QComboBox()
        for code, label in PROTOCOL_LABELS.items():
            self.llm_protocol.addItem(label, code)
        self.llm_protocol.setToolTip(
            "**协议**决定用哪个 SDK、发什么格式的请求——不是厂商名。\n"
            "绝大多数云服务 / 中转 / 本地推理都属 OpenAI 兼容类。\n"
            "只有直接用 Claude 时才选 Anthropic 原生。"
        )

        self.llm_vendor = QComboBox()
        for code, entry in VENDOR_REGISTRY.items():
            self.llm_vendor.addItem(entry["label"], code)
        self.llm_vendor.setToolTip(
            "**厂商**只用于预填下面的 Base URL 和日志标识，Base URL 随时可手改。\n"
            "切换厂商会自动填推荐地址，但不会覆盖你已经改过的内容。"
        )
        self.llm_vendor.currentIndexChanged.connect(self._on_llm_vendor_changed)

        self.llm_api_key = QLineEdit()
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.llm_base_url = QLineEdit()
        self.llm_base_url.setPlaceholderText("https://api.example.com/v1")

        # 2026-10-04：Model 改成**可编辑下拉**——
        # 纯下拉在 provider 不给 /models 时会让人没法用，可编辑才能兜底手填
        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        self.llm_model.setInsertPolicy(QComboBox.NoInsert)
        self.llm_model.setToolTip(
            "模型名。可以点 ⟳ 从服务端自动抓取，也可以直接手打。\n"
            "有些服务不提供 /models 接口，那就只能手填。\n"
            "抓取失败不会清空这里已填的内容。"
        )
        self.llm_model_fetch_btn = QPushButton("⟳ 抓取")
        self.llm_model_fetch_btn.setFixedWidth(78)
        self.llm_model_fetch_btn.setToolTip(
            "向 {Base URL}/models 发一个只读请求，列出该 Key 能用的模型。\n"
            "不会消耗额度。任何失败都不会影响保存或手填。"
        )
        self.llm_model_fetch_btn.clicked.connect(self._on_fetch_models_clicked)
        model_row = QWidget()
        mlay = QHBoxLayout(model_row)
        mlay.setContentsMargins(0, 0, 0, 0)
        mlay.addWidget(self.llm_model, 1)
        mlay.addWidget(self.llm_model_fetch_btn, 0)

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

        # 标签统一用「中文名（英文 key）」，长度接近 → 视觉对齐
        form.addRow("协议 Protocol", self.llm_protocol)
        form.addRow("厂商 Vendor", self.llm_vendor)
        form.addRow("Base URL", self.llm_base_url)
        form.addRow("API Key", self.llm_api_key)
        form.addRow("Model", model_row)
        form.addRow("Temperature", self.llm_temperature)
        form.addRow("Max tokens", self.llm_max_tokens)
        form.addRow("超时(秒)", self.llm_timeout)
        form.addRow("重试次数", self.llm_retries)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        # 让标签列宽度跟随最长项（"协议 Protocol"），所有行左边缘一致
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        outer.addLayout(form)
        outer.addStretch(1)

        self.tabs.addTab(_scroll_page(page), "大模型")

    def _build_transcribe_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)

        # 基础选项
        base = QGroupBox("基础")
        form = QFormLayout(base)
        self.tr_engine = QComboBox()
        # 2026-10-04：改用共享清单（gui/asr_engines.py），与「覆盖本次 ASR」
        # 那个下拉同源。之前两份列表各写一份且已漂移，导致新引擎在那边选不到。
        for _label, _code in ASR_ENGINE_LABELS:
            self.tr_engine.addItem(_label, _code)
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
        # 2026-10-04：默认「只看当前引擎」，下面只显示所选引擎的设置分组。
        # 用户要回到原来那种全量表单时勾这个。
        self.tr_show_all = QCheckBox("显示全部引擎的设置（不分当前引擎）")
        self.tr_show_all.setChecked(False)
        self.tr_show_all.setToolTip(
            "默认只显示「默认 ASR 引擎」所选的那一个引擎的设置，转写页因此短很多。\n\n"
            "勾选后恢复成显示全部引擎的完整表单（调试 / 一次性批量改参数时用）。\n"
            "⚠ 隐藏的分组**参数仍会照常保存**，只是不显示——不会丢配置。"
        )
        form.addRow(self.tr_show_all)
        # 引擎与引擎专属分组的映射（见 _sync_engine_visibility）
        self._engine_blocks = {}
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
        self._eng_block("funasr", fs_box, fs_tip)

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
        self._eng_block("qwen_asr", qwen_box)

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
        self._eng_block("xf_asr", xf_box, xf_tip, xf_enh_tip)

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

        self.cp_endpoint = QLineEdit()
        self.cp_endpoint.setPlaceholderText("只填到 /api/v1 这一层，后面的路径程序自己拼")
        self.cp_endpoint.setToolTip(
            "⚠ **只填到 /api/v1 这一层**，后面的路径由程序按「API 风格」自动拼：\n"
            "  · OpenAI 兼容  → {你填的}/speech_to_text\n"
            "  · DashScope 异步 → {你填的}/services/audio/asr/transcription\n"
            "                        轮询 {你填的}/tasks/{task_id}\n\n"
            "填完整路径也能认出来（会先剥掉再拼），不会拼出 /speech_to_text/speech_to_text。\n\n"
            "示例：\n"
            "  MiniMax   https://api.minimax.cn/v1\n"
            "  阿里云百炼 https://dashscope.aliyuncs.com/api/v1\n"
            "  业务空间   https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1\n"
            "  本地服务   http://127.0.0.1:8000/v1\n"
        )
        self.cp_endpoint.textChanged.connect(self._update_cp_endpoint_preview)

        # === 2026-10-04 新增：API 风格（两类协议完全不同的服务）===
        self.cp_api_style = QComboBox()
        for code, label in API_STYLES.items():
            self.cp_api_style.addItem(label, code)
        self.cp_api_style.setToolTip(
            "同一个框要接两类**协议完全不同**的服务，所以必须显式选风格。\n\n"
            "OpenAI 兼容（默认）：\n"
            "  multipart/form-data 上传文件本体 → 一次请求直接返回 text + segments\n"
            "  上限 500 秒 / 50 MB，超了自动切段\n\n"
            "DashScope 异步（阿里云百炼 Qwen3-ASR / Paraformer）：\n"
            "  application/json，**音频必须是公网 URL**（不能上传文件）\n"
            "  流程：借图床传音频拿 URL → 提交任务 → 轮询 → 下载结果 JSON\n"
            "  上限 12 小时 / 2 GB，基本不用切段\n"
            "  ⚠ 填错风格会直接连接被重置（实测 10054）"
        )
        self.cp_api_style.currentIndexChanged.connect(self._on_cp_style_changed)

        # 端点实时预览（解决「URL 到底怎么填」）
        self.cp_endpoint_preview = QLabel("（填 Base URL 后这里会显示最终请求地址）")
        self.cp_endpoint_preview.setStyleSheet("color: #888; font-size: 11px;")
        self.cp_endpoint_preview.setWordWrap(True)

        self.cp_model = QLineEdit()
        self.cp_model.setPlaceholderText("仅 DashScope 异步需要，如 paraformer-v2")
        self.cp_model.setToolTip(
            "DashScope 异步**必填**，且只能填「长音频/异步」那一族。\n\n"
            "⚠ 同系列有两个名字，**只差一个 -filetrans 后缀**，极易抄错：\n"
            "    qwen3-asr-flash            短音频 ≤5 分钟   同步   ❌ 不能用\n"
            "    qwen3-asr-flash-filetrans   长音频 ≤12 小时  异步   ✅ 用这个\n"
            "  填错的话服务端只回英文\n"
            "  'current user api does not support asynchronous calls'，\n"
            "  完全看不出是少抄了后缀。\n\n"
            "可直接填的异步模型名（以你手上官方文档为准，版本会更新）：\n"
            "  qwen-audio-3.0-asr-flash-filetrans  Qwen-Audio，≤2GB/12h\n"
            "  qwen3-asr-flash-filetrans           Qwen3，≤2GB/12h\n"
            "  fun-asr                             Fun-ASR\n"
            "  paraformer-v2                       仅北京地域\n\n"
            "OpenAI 兼容风格下这个字段被忽略（协议里 model 由程序固定为 asr-1.0）。"
        )

        # 2026-10-04：DashScope 要公网 URL，而「怎么拿到 URL」有两条路
        # （借图床 / 手动直链）。图床没配好时这是唯一出路，所以必须能填。
        self.cp_audio_url = QLineEdit()
        self.cp_audio_url.setPlaceholderText("留空 = 借「设置 → 图床」中转；填了则直接用它")
        self.cp_audio_url.setToolTip(
            "**只 DashScope 异步需要** —— 它不接受文件上传，音频必须是公网 URL。\n\n"
            "留空 = 程序自动把音频传到你的图床换 URL（需要先在「设置 → 图床」\n"
            "填好真实域名并启用；只填了模板占位符会直接报出来）。\n\n"
            "填了 = 直接用这个直链，跳过图床。适合：\n"
            "  · 你已经手动把 mp3 传到网盘/对象存储\n"
            "  · 固定音频反复转写（省一次上传）\n\n"
            "⚠ 不要在这里填**本地路径**（如 E:/.../a.mp3），DashScope 拿不到。\n"
            "⚠ URL 里的中文/空格由程序自动 percent-encode，不用手动处理。"
        )

        self.cp_speaker_count = QSpinBox()
        self.cp_speaker_count.setRange(2, 100)
        self.cp_speaker_count.setValue(2)
        self.cp_speaker_count.setToolTip(
            "DashScope 说话人数**参考值**（2-100），只在勾了「分离说话人」时生效。\n"
            "官方明确：它只是提示算法「尽量输出这个人数」，不保证一定输出。\n"
            "⚠ 开了说话人分离后，官方建议**音频不超过 2 小时**，否则可能失败或超时。"
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
        # 风格与勾选两条路径共用 _on_cp_style_changed 这一个真源，
        # 避免「哪些字段该启用」在两处各写一遍、日后各自漂移。
        self.cp_role_separation.toggled.connect(
            lambda _checked: self._on_cp_style_changed()
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

        self.cp_colloquial_proc = QCheckBox("口语规整（去掉「嗯/啊/呃」等语气词）")
        self.cp_colloquial_proc.setToolTip(
            "仅 DashScope 异步有效（对应它的 disfluency_removal_enabled 参数）。\n"
            "OpenAI 兼容风格下请用上面的「分离说话人」旁的口语规整，"
            "或在请求头文件里加对应参数。\n"
            "课堂实录 / 访谈口癖多，去掉后给下游 LLM 成稿的文本干净很多。"
        )

        mmform.addRow("API 风格", self.cp_api_style)
        mmform.addRow("接口地址（只填到 /api/v1）", self.cp_endpoint)
        mmform.addRow(self.cp_endpoint_preview)
        mmform.addRow("模型名（DashScope 必填）", self.cp_model)
        mmform.addRow("音频直链（DashScope 用）", self.cp_audio_url)
        mmform.addRow("API Key", self.cp_api_key)
        mmform.addRow("请求头文件", self.cp_headers_row)
        mmform.addRow("语言", self.cp_language)
        mmform.addRow(self.cp_mock)
        mmform.addRow(self.cp_role_separation)
        mmform.addRow("说话人数（参考值）", self.cp_speaker_count)
        mmform.addRow(self.cp_timestamps)
        mmform.addRow(self.cp_colloquial_proc)
        mmform.addRow("单请求超时（秒）", self.cp_max_wait)
        outer.addWidget(mm_box)
        self._on_cp_style_changed()

        # === 2026-10-04：自定义 POST 配置档案（机制在 profile_store）===
        cp_prof_box = self._build_profile_box(self._spec("cp"))
        outer.addWidget(cp_prof_box)

        mm_tip = QLabel(
            "💡 自定义 POST 云端 ASR —— 上方「API 风格」决定协议，两类完全不同：\n"
            "  · **OpenAI 兼容**（默认，按 MiniMax asr-1.0 契约）：\n"
            "    multipart/form-data 上传文件本体，一次请求直接返回 text + segments\n"
            "    硬限制单请求 ≤ 500 秒 / 50 MB → 超了自动切 450 秒一段（压单声道 16kHz mp3）\n"
            "  · **DashScope 异步**（阿里云百炼 Qwen3-ASR / Paraformer）：\n"
            "    application/json，**音频必须是公网 URL**（不接受文件上传）\n"
            "    流程：借图床换 URL → 提交任务 → 轮询 → 下载结果（上限 12h/2GB，不切段）\n"
            "    ⚠ 拿 OpenAI 那套去打 DashScope 会被直接重置连接（10054），不返回可读错误\n"
            "  · 接口地址**只填到 /api/v1**，路径由程序拼（下方有实时预览）\n"
            "  · 请求头走可编辑的 text 文件（每行 Key: Value，# 注释）\n"
            "    该文件可能含密钥，已加入 .gitignore；程序只打印键名不打印值\n"
            "  · 鉴权两处都行：API Key 输入框（Bearer）或请求头文件里写 Authorization\n"
            "  · verbose_json 才返回 segments[] + n_speakers（说话人分离 + 时间戳）\n"
            "  · MiniMax Key 申请：https://platform.minimax.cn/user-center/basic-information/interface-key"
        )
        mm_tip.setWordWrap(True)
        mm_tip.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(mm_tip)
        self._eng_block("custom_post", mm_box, cp_prof_box, mm_tip)

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

        # 2026-10-04：按所选引擎显示对应设置分组（默认行为）
        self.tr_engine.currentIndexChanged.connect(self._sync_engine_visibility)
        self.tr_show_all.toggled.connect(self._sync_engine_visibility)
        self._sync_engine_visibility()

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

        # 2026-10-04：档案区放最上面——切服务是最高频动作，不必滚到底部
        layout.addWidget(self._build_profile_box(self._spec("cover")))

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
        # 2026-10-04：档案区放最上面（切图床是高频动作）
        form.addRow(self._build_profile_box(self._spec("host")))
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

        # === easyimage 专属字段（2026-10-04 整组收纳）===
        # ⚠ 切 r2/oss 时必须把这一整组收起来：它们的标签（API URL / Token）
        # 长得和 S3 的 endpoint / 密钥很像，但**完全不是一回事**。
        # 之前两套字段平铺在一起，用户会把 endpoint 填进「API URL」——
        # 那正是本轮用户卡住的直接原因。
        easy_box = QGroupBox("传统图床（provider = easyimage 时才填）")
        easy_form = QFormLayout(easy_box)
        easy_form.addRow("API URL", self.host_api_url)
        easy_form.addRow("Token", self.host_token)
        easy_form.addRow("token_field", self.host_token_field)
        easy_form.addRow("file_field", self.host_file_field)
        easy_form.addRow("url_json_path", self.host_url_path)
        easy_form.addRow("timeout_seconds", self.host_timeout)
        easy_form.addRow("extra_fields (JSON)", self.host_extra)
        form.addRow(easy_box)
        self._host_easy_box = easy_box

        # === 2026-10-04：S3 兼容对象存储（R2 / OSS）===
        # 用途是给 DashScope 异步 ASR 做音频中转 —— 它不接受文件上传，
        # 音频必须先变成公网 URL。easyimage 也能传，但没有公网直链语义。
        self.host_provider.setPlaceholderText("easyimage（默认）/ r2 / oss")
        self.host_provider.setToolTip(
            "easyimage  传统 multipart 图床（Chevereto / 自建），填下面的 API URL + Token\n"
            "r2         Cloudflare R2（S3 兼容，**出网流量永久免费**）\n"
            "oss        阿里云 OSS（S3 兼容，与百炼同云时 DashScope 拉取最快）\n\n"
            "切成 r2 / oss 后，下面这组字段才会启用。"
        )
        s3_box = QGroupBox("S3 兼容对象存储（r2 / oss 用）")
        s3_form = QFormLayout(s3_box)
        self.host_bucket = QLineEdit()
        self.host_bucket.setPlaceholderText("桶名，如 my-audio")
        self.host_endpoint = QLineEdit()
        self.host_endpoint.setPlaceholderText(
            "r2  = https://<AccountID>.r2.cloudflarestorage.com\n"
            "oss = https://oss-cn-<地域>.aliyuncs.com（建议与百炼同区）"
        )
        self.host_region = QLineEdit()
        self.host_region.setPlaceholderText("oss 填 cn-beijing 之类；r2 留空即可（自动 auto）")
        self.host_ak_id = QLineEdit()
        self.host_ak_id.setPlaceholderText("AccessKeyId（R2 叫 API Token ID）")
        self.host_ak_secret = QLineEdit()
        self.host_ak_secret.setEchoMode(QLineEdit.Password)
        self.host_ak_secret.setPlaceholderText("AccessKeySecret / R2 Secret Key")
        self.host_public_base = QLineEdit()
        self.host_public_base.setPlaceholderText(
            "公网访问前缀（可选）。r2 用 https://pub-xxxx.r2.dev；\n"
            "oss 用 https://<bucket>.oss-cn-<region>.aliyuncs.com。留空则按 endpoint 拼"
        )
        self.host_presign = QSpinBox()
        self.host_presign.setRange(0, 86400)
        self.host_presign.setSuffix(" 秒")
        self.host_presign.setToolTip(
            "0 = 用上面那个公网直链（要求桶**公开可读**）。\n"
            "大于 0 = 生成带签名的临时直链（私有桶也能用，更安全）。\n"
            "建议 3600：足够 DashScope 拉完，又不会长期暴露。"
        )
        s3_form.addRow("bucket", self.host_bucket)
        s3_form.addRow("endpoint", self.host_endpoint)
        s3_form.addRow("region", self.host_region)
        s3_form.addRow("access_key_id", self.host_ak_id)
        s3_form.addRow("access_key_secret", self.host_ak_secret)
        s3_form.addRow("public_base_url", self.host_public_base)
        s3_form.addRow("签名有效期", self.host_presign)
        form.addRow(s3_box)
        self._host_s3_box = s3_box
        # ⚠ hint 必须**先建好再 connect**：textChanged 一触发就会调 handler，
        #   handler 里要 setText 到它身上，顺序反了直接 AttributeError。
        host_tip = QLabel()
        host_tip.setWordWrap(True)
        host_tip.setStyleSheet("color: #666; font-size: 11px;")
        form.addRow(host_tip)
        self._host_hint = host_tip
        self.host_provider.textChanged.connect(self._on_host_provider_changed)
        self._on_host_provider_changed()

        self.tabs.addTab(_scroll_page(page), "图床")

    def load_from_disk(self) -> None:
        self._config = load_config() or {}
        llm = self._config.get("llm") or {}
        # 2026-10-04：协议/厂商两个正交字段。旧配置只有 provider，
        # 由 resolve_protocol()/effective_vendor() 兜底推导，**零改动继续能跑**。
        self._config.setdefault("llm", llm)
        pidx = self.llm_protocol.findData(resolve_protocol(llm))
        self.llm_protocol.setCurrentIndex(pidx if pidx >= 0 else 0)
        vidx = self.llm_vendor.findData(effective_vendor(llm))
        self.llm_vendor.setCurrentIndex(vidx if vidx >= 0 else 0)
        self.llm_api_key.setText(str(llm.get("api_key", "")))
        self.llm_base_url.setText(str(llm.get("base_url", "")))
        self.llm_model.clear()
        self.llm_model.setCurrentText(str(llm.get("model", "")))
        self.llm_temperature.setText(str(llm.get("temperature", "0.3")))
        self.llm_max_tokens.setValue(int(llm.get("max_tokens") or 12000))
        self.llm_timeout.setValue(int(llm.get("timeout_seconds") or 180))
        self.llm_retries.setValue(int(llm.get("max_retries") or 3))

        tr = self._config.get("transcribe") or {}
        # ⚠ 必须 normalize_engine：config 里可能还留着旧名 "minimax_asr"，
        #   直接 findData 找不到就静默回落到默认 funasr（用户以为改了引擎其实没改）。
        idx = self.tr_engine.findData(
            normalize_engine(tr.get("asr_engine") or "funasr")
        )
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
        cstyle = str(ca.get("api_style") or "openai_compat")
        cstyle_idx = self.cp_api_style.findData(cstyle)
        self.cp_api_style.setCurrentIndex(cstyle_idx if cstyle_idx >= 0 else 0)
        self.cp_endpoint.setText(
            str(ca.get("endpoint") or DEFAULT_ENDPOINT)
        )
        self.cp_model.setText(str(ca.get("model") or ""))
        self.cp_audio_url.setText(str(ca.get("audio_url") or ""))
        try:
            self.cp_speaker_count.setValue(int(ca.get("speaker_count") or 2))
        except (TypeError, ValueError):
            self.cp_speaker_count.setValue(2)
        self.cp_api_key.setText(str(ca.get("api_key") or ""))
        self.cp_headers_file.setText(str(ca.get("headers_file") or ""))
        clang = str(ca.get("language") or "")
        cidx = self.cp_language.findData(clang)
        self.cp_language.setCurrentIndex(cidx if cidx >= 0 else 0)
        self.cp_mock.setChecked(bool(ca.get("mock", True)))
        self.cp_role_separation.setChecked(bool(ca.get("role_separation", False)))
        self.cp_timestamps.setChecked(bool(ca.get("timestamps", True)))
        self.cp_colloquial_proc.setChecked(bool(ca.get("colloquial_proc", False)))
        try:
            self.cp_max_wait.setValue(int(ca.get("max_wait_seconds") or 300))
        except (TypeError, ValueError):
            self.cp_max_wait.setValue(300)
        self._on_cp_style_changed()  # 风格决定哪些字段可用 + 端点预览
        # 2026-10-04：把 custom_post 档案灌进内存，供「应用到当前 / 管理…」用
        tr.setdefault("custom_post", ca)
        self._refresh_profile_combo(self._spec("cp"))

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
        # S3 系字段（2026-10-04：DashScope 音频中转）
        self.host_bucket.setText(str(host.get("bucket") or ""))
        self.host_endpoint.setText(str(host.get("endpoint") or ""))
        self.host_region.setText(str(host.get("region") or ""))
        self.host_ak_id.setText(str(host.get("access_key_id") or ""))
        self.host_ak_secret.setText(str(host.get("access_key_secret") or ""))
        self.host_public_base.setText(str(host.get("public_base_url") or ""))
        try:
            self.host_presign.setValue(int(host.get("presign_seconds") or 0))
        except (TypeError, ValueError):
            self.host_presign.setValue(0)
        self._on_host_provider_changed()
        extra = host.get("extra_fields") or {}
        try:
            self.host_extra.setPlainText(json.dumps(extra, ensure_ascii=False, indent=2) if extra else "{}")
        except (TypeError, ValueError):
            self.host_extra.setPlainText("{}")

        # 2026-10-04：四处档案区统一在这里灌入内存 + 刷新下拉
        # （控件在各 _build_*_tab 里已建好，这里只做数据侧初始化）
        for spec in self.PROFILES:
            # 确保 config 里有对应子块，供「存档案」时写入
            self._profile_block(spec)
            self._refresh_profile_combo(spec)

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

        llm_updates = {
            # 2026-10-04：protocol/vendor 是新的一等字段。
            # provider 保留写入是为了**向后兼容**——万一用户把 config 换回
            # 旧结构（绿色包覆盖 / git 切回旧 commit），旧代码仍认得。
            "protocol": self.llm_protocol.currentData() or "openai_chat",
            "vendor": self.llm_vendor.currentData() or "custom",
            "provider": self.llm_protocol.currentData() or "openai_chat",
            "api_key": self.llm_api_key.text().strip(),
            "base_url": self.llm_base_url.text().strip(),
            "model": self.llm_model.currentText().strip(),
            "temperature": temperature,
            "max_tokens": self.llm_max_tokens.value(),
            "timeout_seconds": self.llm_timeout.value(),
            "max_retries": self.llm_retries.value(),
        }
        # ⚠ profiles 必须是 list（不是 dict）：deep_update 对 list 整体替换、
        #   对 dict 递归合并——存成 dict 的话删除档案后 key 不会消失（幽灵档案）。
        #   ProfileMixin._profile_updates 统一处理四处档案区，这里只管 llm。
        llm_updates.update(self._profile_updates(self._spec("llm")))

        # custom_post 当前生效值（2026-10-04）
        cp_updates = {
            "api_style": self.cp_api_style.currentData() or "openai_compat",
            "endpoint": self.cp_endpoint.text().strip() or DEFAULT_ENDPOINT,
            "model": self.cp_model.text().strip(),
            "audio_url": self.cp_audio_url.text().strip(),
            "api_key": self.cp_api_key.text().strip(),
            "headers_file": self.cp_headers_file.text().strip(),
            "language": self.cp_language.currentData() or "",
            "mock": self.cp_mock.isChecked(),
            "role_separation": self.cp_role_separation.isChecked(),
            "speaker_count": self.cp_speaker_count.value(),
            "timestamps": self.cp_timestamps.isChecked(),
            "colloquial_proc": self.cp_colloquial_proc.isChecked(),
            "max_wait_seconds": self.cp_max_wait.value(),
        }
        # custom_post 档案（2026-10-04）—— 同样是 list，理由见上。
        cp_updates.update(self._profile_updates(self._spec("cp")))

        return {
            "llm": llm_updates,
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
                "custom_post": cp_updates,
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
                # 2026-10-04：AI 封面配置档案（list，理由同 llm）
                **self._profile_updates(self._spec("cover")),
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
                # S3 兼容对象存储（r2 / oss）—— DashScope 音频中转用
                "bucket": self.host_bucket.text().strip(),
                "endpoint": self.host_endpoint.text().strip(),
                "region": self.host_region.text().strip(),
                "access_key_id": self.host_ak_id.text().strip(),
                "access_key_secret": self.host_ak_secret.text().strip(),
                "public_base_url": self.host_public_base.text().strip(),
                "presign_seconds": self.host_presign.value(),
                # 2026-10-04：图床配置档案
                **self._profile_updates(self._spec("host")),
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
