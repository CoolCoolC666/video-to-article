"""配置档案的通用机制（2026-10-04）。

## 为什么抽这个模块

档案功能已经接到**四个**地方了（大模型 / 自定义 POST ASR / AI 封面 / 图床）。
每处都手写一遍「存档案 / 应用档案 / 管理弹窗 / 读写 config」的话，
改一个 bug 要改四处，而且**必然漂移**——本轮已经漂移过一次
（引擎列表在 settings 和 common_options 各写一份，导致新引擎在
「覆盖本次 ASR」里选不到）。

所以把机制抽到这里，用法是继承一个 mixin + 声明 4 样东西：

    class SettingsDialog(ProfileMixin):
        PROFILES = [
            ProfileSpec(
                prefix="llm",                    # 控件名前缀：self.llm_profile_*
                config_path=("llm",),            # 档案存在 config 的哪
                spec=PROFILE_SPEC_LLM,           # 列表渲染字段规格
                kind_label="大模型",
                fields=[...],                    # 存档案时从 UI 取哪些字段
            ),
            ProfileSpec(...),                   # 可注册多个
        ]

## 三条必须守住的约定

1. **profiles 必须是 list，不能是 dict**
   `config.deep_update` 对 list **整体替换**、对 dict **递归合并**。
   存成 dict 时「删除档案」只会 merge 覆盖，删掉的 key 依然留在配置里，
   变成**删不掉的幽灵档案**。这个区别 review 时极难一眼看出，所以有测试锁着。

2. **档案是模板库，不是权威**
   改档案**不**自动改下方当前字段，必须点「应用到当前」才覆盖。
   否则用户会「以为在改档案，其实改的是当前生效配置」。

3. **敏感字段一律掩码**
   列表渲染时走 `mask_secret()`，日志里不打印值。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QComboBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ..providers.llm_providers import mask_secret

# 渲染规格条目：(字段键, 显示名, 是否敏感)
FieldSpec = tuple

# ---- 四套字段规格（各档案区列表里显示什么）----
# ⚠ 敏感字段一律 secret=True —— 列表渲染走 mask_secret，不会明文显示。
PROFILE_SPEC_LLM: List[FieldSpec] = [
    ("protocol", "协议", False),
    ("vendor", "厂商", False),
    ("model", "模型", False),
    ("base_url", "BaseURL", False),
    ("api_key", "Key", True),
]
PROFILE_SPEC_ASR: List[FieldSpec] = [
    ("endpoint", "端点", False),
    ("language", "语言", False),
    ("api_key", "Key", True),
    ("headers_file", "请求头文件", False),
]
PROFILE_SPEC_COVER: List[FieldSpec] = [
    ("provider", "Provider", False),
    ("model", "模型", False),
    ("base_url", "BaseURL", False),
    ("api_key", "Key", True),
]
PROFILE_SPEC_HOST: List[FieldSpec] = [
    ("provider", "Provider", False),
    ("api_url", "API URL", False),
    ("token", "Token", True),
]


@dataclass
class ProfileSpec:
    """一处档案区的声明。"""

    prefix: str                    # 控件名前缀，如 "llm" → self.llm_profile_combo
    config_path: Sequence[str]     # 档案在 config 里的路径，如 ("llm",)
    spec: List[FieldSpec]          # 列表渲染规格
    kind_label: str                # 弹窗标题用，如 "大模型"
    fields: Callable[[Any], Dict[str, Any]]  # 从对话框 UI 采集当前配置
    apply: Callable[[Any, Dict[str, Any]], None]  # 把档案套回 UI
    hint: str = ""                 # 档案区下拉框的 tooltip
    dup_hint: str = ""             # 复制按钮的额外提示
    combo_fmt: Callable[[Dict[str, Any]], str] = field(
        default=lambda p: str(p.get("label") or p.get("id") or "")
    )


class ProfileManagerDialog(QDialog):
    """档案管理弹窗（重命名 / 复制 / 删除 / 排序）。

    按字段规格渲染，所以四个引擎/模块能共用这一个。
    刻意**不含**导入/导出——档案里有 API Key，导出等于给密钥开外流口子。
    """

    def __init__(
        self,
        parent,
        profiles: list,
        field_spec: list,
        kind_label: str = "配置",
        dup_hint: str = "「复制」会连 API Key 一起复制（存主力 + 备用常用）。",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"管理{kind_label}配置档案")
        self.resize(620, 430)
        self._profiles = [dict(p) for p in profiles]
        self._spec = list(field_spec)

        root = QVBoxLayout(self)
        self.list_w = QListWidget()
        self.list_w.itemDoubleClicked.connect(lambda _: self._rename())
        root.addWidget(QLabel(
            f"双击可重命名。\n{dup_hint}\n"
            "「删除」不可撤销；至少要保留一个档案。"
        ))
        root.addWidget(self.list_w, 1)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        for text, slot in (
            ("重命名…", self._rename),
            ("复制", self._duplicate),
            ("删除", self._delete),
            ("上移", lambda: self._move(-1)),
            ("下移", lambda: self._move(1)),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            rl.addWidget(b)
        rl.addStretch(1)
        root.addWidget(row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._reload()

    # ---------- 渲染 ----------

    def _reload(self) -> None:
        self.list_w.clear()
        for p in self._profiles:
            parts = []
            for key, label, secret in self._spec:
                v = p.get(key, "")
                if secret and v:
                    v = mask_secret(str(v))
                elif v == "" or v is None:
                    v = "—"
                parts.append(f"{label}={v}")
            item = QListWidgetItem(
                f"{p.get('label', p.get('id'))}    " + "  ".join(parts)
            )
            item.setData(Qt.UserRole, p.get("id"))
            self.list_w.addItem(item)
        if self._profiles:
            self.list_w.setCurrentRow(0)

    def _current_idx(self) -> int:
        return self.list_w.currentRow()

    # ---------- 操作 ----------

    def _rename(self) -> None:
        i = self._current_idx()
        if i < 0:
            return
        old = self._profiles[i].get("label", "")
        name, ok = QInputDialog.getText(self, "重命名档案", "档案名称：", text=old)
        if not ok or not name.strip():
            return
        # ⚠ 只改 label，**id 不动**——id 是配置里的引用锚点，改了会失联
        self._profiles[i]["label"] = name.strip()
        self._reload()
        self.list_w.setCurrentRow(i)
        self.accept()

    def _duplicate(self) -> None:
        i = self._current_idx()
        if i < 0:
            return
        src = dict(self._profiles[i])
        src["id"] = f"{src.get('id')}_copy{int(time.time())}"
        src["label"] = f"{src.get('label', '')} 副本"
        self._profiles.insert(i + 1, src)
        self._reload()
        self.list_w.setCurrentRow(i + 1)
        self.accept()

    def _delete(self) -> None:
        i = self._current_idx()
        if i < 0:
            return
        if len(self._profiles) <= 1:
            QMessageBox.warning(self, "不能删空", "至少要保留一个档案。")
            return
        label = self._profiles[i].get("label", "")
        if QMessageBox.question(
            self, "删除档案",
            f"确定删除「{label}」？\n\n此操作不可撤销（只影响档案，不会改动当前生效配置）。",
        ) != QMessageBox.Yes:
            return
        self._profiles.pop(i)
        self._reload()
        self.accept()

    def _move(self, delta: int) -> None:
        i = self._current_idx()
        j = i + delta
        if i < 0 or not (0 <= j < len(self._profiles)):
            return
        self._profiles[i], self._profiles[j] = self._profiles[j], self._profiles[i]
        self._reload()
        self.list_w.setCurrentRow(j)
        self.accept()

    def result_profiles(self) -> list:
        return self._profiles


class ProfileMixin:
    """给 SettingsDialog 用的档案机制 mixin。

    使用方需要在 __init__ **之前**声明 `PROFILES`，然后在自己的
    `_build_*_tab` 里调用 `self._build_profile_box(spec)` 建 UI。
    """

    PROFILES: List[ProfileSpec] = []

    # ---------- UI 构建 ----------

    def _build_profile_box(self, spec: ProfileSpec) -> QGroupBox:
        box = QGroupBox(f"{spec.kind_label}配置档案")
        lay = QVBoxLayout(box)
        combo = QComboBox()
        combo.setToolTip(spec.hint or (
            "已保存的配置档案。选中一个点「应用到当前」即可套用。\n"
            "档案是**模板库**：改档案不会自动改下方字段，"
            "需点「应用到当前」才会覆盖。"
        ))
        setattr(self, f"{spec.prefix}_profile_combo", combo)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        for text, slot in (
            ("应用到当前", lambda: self._profile_apply(spec)),
            ("把当前存为新档案", lambda: self._profile_save_new(spec)),
            ("管理…", lambda: self._profile_manage(spec)),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            rl.addWidget(b)
            if text == "管理…":
                setattr(self, f"{spec.prefix}_profile_manage_btn", b)
        rl.addStretch(1)
        lay.addWidget(combo)
        lay.addWidget(row)
        return box

    # ---------- 读写 ----------

    def _profile_block(self, spec: ProfileSpec) -> dict:
        """取（或建）该档案区对应的 config 子块。"""
        node = self._config
        for key in spec.config_path:
            nxt = node.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                node[key] = nxt
            node = nxt
        return node

    def _get_profiles(self, spec: ProfileSpec) -> list:
        profs = self._profile_block(spec).get("profiles")
        return [dict(p) for p in profs] if isinstance(profs, list) else []

    def _set_profiles(
        self,
        spec: ProfileSpec,
        profiles: list,
        keep_active: Optional[str] = None,
    ) -> None:
        blk = self._profile_block(spec)
        blk["profiles"] = profiles
        if keep_active is not None:
            blk["active_profile"] = keep_active
        self._refresh_profile_combo(spec)

    def _refresh_profile_combo(self, spec: ProfileSpec) -> None:
        combo: QComboBox = getattr(self, f"{spec.prefix}_profile_combo")
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for p in self._get_profiles(spec):
            combo.addItem(spec.combo_fmt(p), p.get("id"))
        idx = combo.findData(cur)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        has = combo.count() > 0
        btn = getattr(self, f"{spec.prefix}_profile_manage_btn", None)
        if btn is not None:
            btn.setEnabled(has)

    # ---------- 动作 ----------

    def _profile_save_new(self, spec: ProfileSpec) -> None:
        name, ok = QInputDialog.getText(
            self, f"新建{spec.kind_label}配置档案", "档案名称："
        )
        if not ok or not name.strip():
            return
        profiles = self._get_profiles(spec)
        pid = f"{spec.prefix}{len(profiles) + 1}_{int(time.time())}"
        prof = {"id": pid, "label": name.strip()}
        prof.update(spec.fields(self))
        profiles.append(prof)
        combo: QComboBox = getattr(self, f"{spec.prefix}_profile_combo")
        self._set_profiles(spec, profiles, keep_active=pid)
        QMessageBox.information(
            self, "已保存",
            f"档案「{name.strip()}」已保存。\n"
            f"点主对话框的「保存」才会写进 config.json。",
        )
        _ = combo

    def _profile_apply(self, spec: ProfileSpec) -> None:
        combo: QComboBox = getattr(self, f"{spec.prefix}_profile_combo")
        pid = combo.currentData()
        prof = next((p for p in self._get_profiles(spec) if p.get("id") == pid), None)
        if not prof:
            QMessageBox.information(self, "档案", "请先在下拉框里选一个档案。")
            return
        spec.apply(self, prof)
        QMessageBox.information(
            self, "已应用",
            f"档案「{prof.get('label')}」已套用到当前配置。\n记得点「保存」写盘。",
        )

    def _profile_manage(self, spec: ProfileSpec) -> None:
        dlg = ProfileManagerDialog(
            self, self._get_profiles(spec), spec.spec,
            kind_label=spec.kind_label, dup_hint=spec.dup_hint,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        new_profiles = dlg.result_profiles()
        if not new_profiles:
            QMessageBox.warning(
                self, "不能删空",
                "至少要保留一个档案。\n若只想清理，请先「把当前存为新档案」再删。",
            )
            return
        combo: QComboBox = getattr(self, f"{spec.prefix}_profile_combo")
        self._set_profiles(spec, new_profiles, keep_active=combo.currentData())
        QMessageBox.information(
            self, "档案已更新",
            f"当前共 {len(new_profiles)} 个{spec.kind_label}档案。\n"
            f"记得点主对话框的「保存」写进 config.json。",
        )

    # ---------- 写回 config ----------

    def _profile_updates(self, spec: ProfileSpec) -> Dict[str, Any]:
        """档案区要写回 config 的字段。空档案时不写任何键。

        ⚠ `profiles` 必须是 list —— 见模块 docstring 第 1 条。
        """
        combo: QComboBox = getattr(self, f"{spec.prefix}_profile_combo")
        profiles = self._get_profiles(spec)
        if not profiles:
            return {}
        return {
            "profiles": profiles,
            "active_profile": combo.currentData() or "",
        }
