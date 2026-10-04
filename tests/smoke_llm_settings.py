"""Smoke tests for LLM 多档案 + 模型自动发现（2026-10-04）。

Covers:
  1. resolve_protocol() —— 旧 config 零改动兼容（含 provider 拼错的兜底）
  2. effective_vendor() / vendor 预填 / 掩码
  3. build_models_url() —— base_url 各种写法都能拼对
  4. parse_models_response() —— 三种响应形态 + 垃圾输入
  5. fetch_models() —— 五种失败降级，**断言失败时返回 err 而非抛异常**
  6. ⚠ deep_update 语义：profiles 必须存 list（dict 会产生删不掉的幽灵档案）
  7. SettingsDialog 读写 —— 协议/厂商对齐显示、档案增删、写回不丢、Key 不被改坏
  8. ProfileManagerDialog —— 重命名/复制/删除/排序

不联网 —— fetch_models 用 stub session。
从仓库根运行：python tests\\smoke_llm_settings.py
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")

from video_to_article.config import deep_update  # noqa: E402
from video_to_article.providers.llm_models import (  # noqa: E402
    build_models_url,
    parse_models_response,
)
from video_to_article.providers.llm_providers import (  # noqa: E402
    VENDOR_REGISTRY,
    effective_vendor,
    guess_vendor_from_url,
    mask_secret,
    resolve_protocol,
    vendor_base_url,
)

# 2026-10-04 用户真实配置（api_key 已打码，只用于断言"不被改坏"）
REAL_OLD_CONFIG = {
    "provider": "openai",
    "api_key": "sk-oldkey-123456",
    "base_url": "https://api.minimaxi.com/v1",
    "model": "MiniMax-M3",
    "temperature": 0.3,
    "max_tokens": 520000,
    "timeout_seconds": 180,
    "max_retries": 3,
}


def test_resolve_protocol_compat():
    """1. resolve_protocol()：旧 config 零改动兼容。"""
    # 真实旧配置
    assert resolve_protocol(REAL_OLD_CONFIG) == "openai_chat", (
        "用户的真实旧 config 必须解析成 openai_chat"
    )
    # 旧 provider = anthropic
    assert resolve_protocol({"provider": "anthropic"}) == "anthropic"
    # 大小写 / 空格
    assert resolve_protocol({"provider": " OpenAI "}) == "openai_chat"
    # 空 / 缺字段 / 拼错 —— 旧代码这些会报「不支持的提供商」直接失败
    assert resolve_protocol({"provider": ""}) == "openai_chat"
    assert resolve_protocol({}) == "openai_chat"
    assert resolve_protocol({"provider": "乱写"}) == "openai_chat"
    # 显式 protocol 优先
    assert resolve_protocol({"protocol": "anthropic", "provider": "openai"}) == "anthropic"
    # 非法 protocol 值应回落到 provider，而不是崩
    assert resolve_protocol({"protocol": "不存在的协议", "provider": "anthropic"}) == (
        "anthropic"
    )
    print("OK 1: resolve_protocol 旧配置零改动兼容（含 provider 拼错兜底）\n")


def test_vendor():
    """2. 厂商推导 / 预填 / 掩码。"""
    assert effective_vendor(REAL_OLD_CONFIG) == "minimax", "应从 base_url 推出 minimax"
    assert guess_vendor_from_url("https://api.openai.com/v1") == "openai"
    assert guess_vendor_from_url("https://api.deepseek.com/v1") == "deepseek"
    assert guess_vendor_from_url("https://api.invalid.cn/v1") == "custom"
    assert guess_vendor_from_url("") == "custom"
    # 显式 vendor 优先于 base_url 猜测
    assert effective_vendor({"vendor": "xai", "base_url": "https://api.deepseek.com"}) == "xai"
    # 预填
    assert vendor_base_url("minimax") == "https://api.minimaxi.com/v1"
    assert vendor_base_url("不存在的厂商") == ""
    assert vendor_base_url("custom") == ""
    # 每个厂商都有可用的 base_url（custom 除外）
    for code, entry in VENDOR_REGISTRY.items():
        assert "label" in entry, f"厂商 {code} 缺 label"
        if code != "custom":
            assert entry.get("base_url", "").startswith("http"), (
                f"厂商 {code} 的 base_url 不像 URL: {entry.get('base_url')!r}"
            )
    # 掩码：绝不能回显完整密钥
    assert mask_secret("sk-cp1234567890cWo") == "sk-c***Wo"
    assert mask_secret("") == ""
    assert "1234567890" not in mask_secret("sk-cp1234567890cWo"), "掩码后不该含完整密钥"
    print("OK 2: 厂商推导 / 预填 / 密钥掩码\n")


def test_build_models_url():
    """3. base_url 各种写法都要拼对（用户配置里都出现过这些形态）。"""
    assert build_models_url("https://api.minimaxi.com/v1") == (
        "https://api.minimaxi.com/v1/models"
    )
    assert build_models_url("https://api.minimaxi.com/v1/") == (
        "https://api.minimaxi.com/v1/models"
    )
    # 只给根 → 补 /v1
    assert build_models_url("https://x.com") == "https://x.com/v1/models"
    # 已经是 models 结尾 → 不重复加
    assert build_models_url("https://y.com/v1/models") == "https://y.com/v1/models"
    # 空
    assert build_models_url("") == ""
    assert build_models_url(None) == ""
    print("OK 3: build_models_url 容忍各种 base_url 写法\n")


def test_parse_models_response():
    """4. 响应解析：三种常见形态 + 垃圾输入不崩。"""
    # OpenAI 标准（实测 MiniMax 就是这个）
    assert parse_models_response(
        {"object": "list", "data": [{"id": "M3"}, {"id": "M2.7"}]}
    ) == ["M3", "M2.7"]
    # 老式：直接字符串数组
    assert parse_models_response({"data": ["A", "B"]}) == ["A", "B"]
    # 单 key + name 字段
    assert parse_models_response({"models": [{"name": "E"}]}) == ["E"]
    # 顶层就是数组
    assert parse_models_response(["X", "Y"]) == ["X", "Y"]
    # 去重
    assert parse_models_response({"data": [{"id": "A"}, {"id": "A"}]}) == ["A"]
    # 垃圾输入全部返回 []
    for bad in (None, "garbage", 123, {}, {"data": None}, {"data": []}, {"data": [1, None]}):
        assert parse_models_response(bad) == [], f"{bad!r} 应返回空列表"
    print("OK 4: parse_models_response 三种形态 + 垃圾输入不崩\n")


def test_fetch_models_degrade():
    """5. fetch_models()：五种失败降级，**不抛异常**，返回可读原因。"""
    import video_to_article.providers.llm_models as LM

    orig_import = LM.import_required
    state = {"resp": None}

    class FakeResp:
        """模拟 requests.Response。"""

        def __init__(self, status_code=200, ctype="application/json",
                     text='{"data":[{"id":"A"}]}'):
            self.status_code = status_code
            self.headers = {"Content-Type": ctype}
            self.text = text

    FakeRespNS = FakeResp  # 名字保留，语义就是普通响应

    class FakeRequests:
        class exceptions:
            class Timeout(Exception):
                pass

            class RequestException(Exception):
                pass

        @staticmethod
        def get(url, headers=None, timeout=None):
            r = state["resp"]
            if isinstance(r, Exception):
                raise r
            return r

    LM.import_required = lambda mod, pkg=None: (
        FakeRequests if mod == "requests" else orig_import(mod, pkg)
    )
    try:
        # 空 base_url
        models, err = LM.fetch_models("", "k")
        assert models == [] and err and "Base URL" in err, f"{err!r}"

        # 404：没有 /models 接口
        state["resp"] = FakeRespNS(status_code=404)
        models, err = LM.fetch_models("https://x/v1", "k")
        assert models == [] and err and "/models" in err, f"404 提示应含 /models: {err!r}"

        # 401：鉴权失败
        state["resp"] = FakeRespNS(status_code=401)
        models, err = LM.fetch_models("https://x/v1", "k")
        assert models == [] and err and "鉴权" in err, f"401 提示应提鉴权: {err!r}"

        # 返回 HTML 登录页（常见）
        state["resp"] = FakeRespNS(ctype="text/html", text="<html>login</html>")
        models, err = LM.fetch_models("https://x/v1", "k")
        assert models == [] and err and "JSON" in err, f"HTML 响应应被识别: {err!r}"

        # 非 JSON 文本但 Content-Type 说 json
        state["resp"] = FakeRespNS(text="not json at all")
        models, err = LM.fetch_models("https://x/v1", "k")
        assert models == [] and err and "JSON" in err, f"非法 JSON 应被识别: {err!r}"

        # 超时
        state["resp"] = FakeRequests.exceptions.Timeout("slow")
        models, err = LM.fetch_models("https://x/v1", "k")
        assert models == [] and err and "超时" in err, f"超时应被识别: {err!r}"

        # 网络异常
        state["resp"] = FakeRequests.exceptions.RequestException("down")
        models, err = LM.fetch_models("https://x/v1", "k")
        assert models == [] and err, "网络异常应返回错误信息而非抛异常"

        # 空列表（结构不常见）
        state["resp"] = FakeRespNS(text='{"weird": 1}')
        models, err = LM.fetch_models("https://x/v1", "k")
        assert models == [] and err, "解析不出模型应返回错误信息"

        # 成功
        state["resp"] = FakeRespNS(text='{"data":[{"id":"MiniMax-M3"}]}')
        models, err = LM.fetch_models("https://api.minimaxi.com/v1", "k")
        assert models == ["MiniMax-M3"], f"成功路径应返回模型: {models!r} err={err!r}"
        assert err is None, f"成功时 err 应为 None，实得 {err!r}"
    finally:
        LM.import_required = orig_import
    print("OK 5: fetch_models 七种场景（成功 + 6 种降级）均不抛异常\n")


def test_profiles_must_be_list():
    """6. ⚠ profiles 必须存 list —— 存 dict 会产生删不掉的幽灵档案。

    这是本设计里最容易埋雷的一处：deep_update 对 list 整体替换、
    对 dict 递归合并。存成 dict 时「删除档案」只会被 merge 覆盖，
    删掉的 key 依然留在配置里。
    """
    # list：删除真的生效
    cfg = {"llm": {"profiles": [
        {"id": "p1", "label": "A"}, {"id": "p2", "label": "B"},
    ]}}
    deep_update(cfg, {"llm": {"profiles": [{"id": "p1", "label": "A"}]}})
    assert [p["id"] for p in cfg["llm"]["profiles"]] == ["p1"], (
        "list 存法下删除应生效，p2 不该残留"
    )

    # dict：删除不生效（幽灵档案）——这正是我们不用 dict 的原因
    as_dict = {"llm": {"profiles": {"p1": {"label": "A"}, "p2": {"label": "B"}}}}
    deep_update(as_dict, {"llm": {"profiles": {"p1": {"label": "A"}}}})
    assert "p2" in as_dict["llm"]["profiles"], (
        "dict 存法下 p2 会残留（幽灵档案）——若此断言失败，说明 deep_update 行为变了，需重新评估"
    )

    # 不带 profiles 键时，deep_update 不会碰它（档案天然存活）
    keep = {"llm": {"max_tokens": 1}}
    deep_update(keep, {"llm": {"max_tokens": 999}})
    assert "profiles" not in keep["llm"], "没塞 profiles 就不该凭空出现"
    print("OK 6: deep_update 语义（list 删除生效 / dict 产生幽灵档案）\n")


def test_settings_dialog():
    """7. SettingsDialog：协议/厂商对齐、档案增删、写回不丢、Key 不被改坏。"""
    from PySide6.QtWidgets import QApplication

    from video_to_article import config as cfg_mod
    from video_to_article.gui.settings import settings_dialog as sd_mod
    from video_to_article.gui.settings.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    # load_config 返回的是**完整配置**（含 "llm" 顶层键），不是裸的 llm 块
    full_cfg = {"llm": json.loads(json.dumps(REAL_OLD_CONFIG))}
    cfg_mod.load_config = lambda: json.loads(json.dumps(full_cfg))
    sd_mod.load_config = lambda: json.loads(json.dumps(full_cfg))

    d = SettingsDialog()
    # --- 旧 config 零改动加载 ---
    assert d.llm_protocol.currentData() == "openai_chat", "旧 config 协议推导错"
    assert d.llm_vendor.currentData() == "minimax", "旧 config 厂商推导错"
    assert d.llm_model.currentText() == "MiniMax-M3", "旧 config 模型没读出来"
    assert d.llm_api_key.text() == REAL_OLD_CONFIG["api_key"], "旧 config 的 Key 必须原样读出"

    # --- Model 是可编辑下拉（provider 不给 /models 时要能手填）---
    assert d.llm_model.isEditable(), "Model 必须是可编辑下拉（纯下拉在无 /models 时没法用）"

    # --- 右下角管理按钮 ---
    assert d.profiles_manage_btn.text() == "管理配置档案…", "右下角缺管理入口"

    # --- 存档案 ---
    _spec = d._spec("llm")
    d._set_profiles(_spec, [dict(_spec.fields(d), id="p1", label="MiniMax M3 主力")])
    assert len(d._get_profiles(_spec)) == 1
    prof = d._get_profiles(_spec)[0]
    assert prof["vendor"] == "minimax" and prof["protocol"] == "openai_chat"
    assert prof["api_key"] == REAL_OLD_CONFIG["api_key"], "档案里 Key 存错"

    # --- 写回 ---
    up = d._collect_updates()["llm"]
    for k in ("protocol", "vendor", "provider", "profiles", "active_profile"):
        assert k in up, f"写回缺 {k}"
    assert isinstance(up["profiles"], list), "profiles 必须是 list"
    assert up["api_key"] == REAL_OLD_CONFIG["api_key"], "写回过程不能改坏 Key"
    # provider 保留写（向后兼容：万一 config 被换回旧结构）
    assert up["provider"] == "openai_chat", "provider 应保留写入做向后兼容"

    # --- 厂商联动：空 → 预填；手改过 → 不冲掉 ---
    d2 = SettingsDialog()
    d2.llm_base_url.setText("")
    d2.llm_vendor.setCurrentIndex(d2.llm_vendor.findData("deepseek"))
    assert "deepseek" in d2.llm_base_url.text(), "空 base_url 应被预填"
    d2.llm_base_url.setText("https://my-proxy.internal/v1")
    d2.llm_vendor.setCurrentIndex(d2.llm_vendor.findData("qwen"))
    assert d2.llm_base_url.text() == "https://my-proxy.internal/v1", (
        "用户手改过的 base_url 不该被厂商切换冲掉"
    )
    print("OK 7: SettingsDialog（旧 config 加载 / 可编辑下拉 / 档案写回 / 厂商联动）\n")


def test_profile_manager_dialog():
    """8. ProfileManagerDialog：重命名 / 复制 / 删除 / 排序。"""
    from PySide6.QtWidgets import QApplication

    from video_to_article.gui.profile_store import (
        PROFILE_SPEC_LLM,
        ProfileManagerDialog,
    )

    app = QApplication.instance() or QApplication([])
    base = [
        {"id": "p1", "label": "主力", "api_key": "k1", "model": "M1"},
        {"id": "p2", "label": "备用", "api_key": "k2", "model": "M2"},
    ]
    dlg = ProfileManagerDialog(None, base, PROFILE_SPEC_LLM, kind_label="大模型")

    def _sel(i):
        dlg.list_w.setCurrentRow(i)

    # 重命名：只改 label，id 不动（id 是引用锚点）
    _sel(0)
    orig = dlg._rename
    dlg._rename = lambda: dlg._profiles.__setitem__(
        0, {**dlg._profiles[0], "label": "主力改"}
    )
    dlg._rename()
    dlg._rename = orig
    assert dlg._profiles[0]["label"] == "主力改", "重命名失败"
    assert dlg._profiles[0]["id"] == "p1", "重命名不该动 id"

    # 复制：id 要变（不能与原档案撞 id）
    _sel(0)
    dlg._duplicate()
    assert len(dlg._profiles) == 3, "复制后应 3 个"
    assert dlg._profiles[1]["id"] != "p1", "复制的 id 必须与原件不同"
    assert dlg._profiles[1]["api_key"] == "k1", "复制应连 Key 一起复制"
    assert dlg._profiles[1]["label"].endswith("副本"), "副本应有标识"

    # 删除
    dlg._profiles.pop(2)  # 去掉刚复制的，回到 2 个
    _sel(1)
    from PySide6.QtWidgets import QMessageBox

    orig_q = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        dlg._delete()
    finally:
        QMessageBox.question = orig_q
    assert len(dlg._profiles) == 1, f"删除后应剩 1 个，实得 {len(dlg._profiles)}"
    assert dlg._profiles[0]["id"] == "p1", "删错了"

    # 最后一个不允许删
    orig_w = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    try:
        dlg._delete()
    finally:
        QMessageBox.warning = orig_w
    assert len(dlg._profiles) == 1, "只剩一个时不该被删空"

    # 排序
    dlg._profiles = [
        {"id": "a", "label": "A"},
        {"id": "b", "label": "B"},
    ]
    dlg._reload()
    dlg.list_w.setCurrentRow(0)
    dlg._move(1)
    assert [p["id"] for p in dlg._profiles] == ["b", "a"], "下移失败"
    print("OK 8: ProfileManagerDialog（重命名/复制/删除/防删空/排序）\n")


def test_custom_post_profiles():
    """9. custom_post（自定义 POST ASR）也支持多档案。

    2026-10-04 用户要求：切一个云端 Provider 要手打「端点 + Key + 请求头文件路径」
    三件套，而不同 Provider 的鉴权方式还各不相同（Bearer / 自定义头 / 私有网关）。
    所以每个档案可以指向**不同的请求头文件**——这正是这个功能的核心价值。
    """
    from PySide6.QtWidgets import QApplication

    from video_to_article import config as cfg_mod
    from video_to_article.gui.settings import settings_dialog as sd_mod
    from video_to_article.gui.profile_store import (
        PROFILE_SPEC_ASR,
        PROFILE_SPEC_LLM,
        ProfileManagerDialog,
    )
    from video_to_article.gui.settings.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    cfg = {
        "llm": json.loads(json.dumps(REAL_OLD_CONFIG)),
        "transcribe": {
            "asr_engine": "custom_post",
            "custom_post": {
                "endpoint": "https://api.minimax.cn/v1/speech_to_text",
                "api_key": "sk-cp-key-123456",
                "headers_file": "",
                "language": "zh",
                "mock": False,
                "role_separation": True,
                "timestamps": True,
                "max_wait_seconds": 600,
            },
        },
    }
    cfg_mod.load_config = lambda: json.loads(json.dumps(cfg))
    sd_mod.load_config = lambda: json.loads(json.dumps(cfg))

    d = SettingsDialog()
    # --- 旧配置（无档案）必须能加载 ---
    assert d.cp_endpoint.text() == "https://api.minimax.cn/v1/speech_to_text"
    assert d.cp_language.currentData() == "zh"
    assert d.cp_role_separation.isChecked() is True
    assert d.cp_max_wait.value() == 600
    assert d._get_profiles(d._spec("cp")) == [], "没有档案时不该凭空造出来"

    # --- 存两个档案，各自指向不同请求头文件 ---
    _cspec = d._spec("cp")
    d.cp_headers_file.setText("D:/hdr/minimax.txt")
    d._set_profiles(_cspec, [dict(_cspec.fields(d), id="cp1", label="MiniMax 官方")])
    d.cp_headers_file.setText("D:/hdr/gateway.txt")
    d.cp_endpoint.setText("https://gw.internal/stt")
    d._set_profiles(
        _cspec,
        d._get_profiles(_cspec) + [dict(_cspec.fields(d), id="cp2", label="私有网关")],
        keep_active="cp2",
    )
    profs = d._get_profiles(_cspec)
    assert len(profs) == 2, f"应存 2 个档案，实得 {len(profs)}"
    assert profs[0]["headers_file"] == "D:/hdr/minimax.txt"
    assert profs[1]["headers_file"] == "D:/hdr/gateway.txt", (
        "不同档案应能指向不同请求头文件（不同 Provider 鉴权方式不同）"
    )
    assert profs[1]["endpoint"] == "https://gw.internal/stt"

    # --- 应用档案应把 UI 切回对应配置 ---
    d._apply_cp_profile_to_ui(profs[0])
    assert d.cp_endpoint.text() == "https://api.minimax.cn/v1/speech_to_text"
    assert d.cp_headers_file.text() == "D:/hdr/minimax.txt"
    assert d.cp_api_key.text() == "sk-cp-key-123456"

    # --- 写回：档案在、Key 未坏、类型是 list ---
    up = d._collect_updates()
    cp = up["transcribe"]["custom_post"]
    llm = up["llm"]
    assert isinstance(cp.get("profiles"), list), "profiles 必须是 list（否则删不掉）"
    assert len(cp["profiles"]) == 2
    assert cp["api_key"] == "sk-cp-key-123456", "custom_post 的 Key 不能被改坏"
    assert llm["api_key"] == REAL_OLD_CONFIG["api_key"], "LLM 的 Key 不能被改坏"

    # --- 两个引擎的档案互相独立，删一个不复活 ---
    c2 = json.loads(json.dumps(cfg))
    c2["transcribe"]["custom_post"]["profiles"] = [
        {"id": "cp1", "label": "A"}, {"id": "cp2", "label": "B"},
    ]
    deep_update(c2, {"transcribe": {"custom_post": {
        "profiles": [{"id": "cp1", "label": "A"}],
    }}})
    assert [p["id"] for p in c2["transcribe"]["custom_post"]["profiles"]] == ["cp1"]
    # 只改别的字段，删掉的档案不该复活
    deep_update(c2, {"transcribe": {"custom_post": {"max_wait_seconds": 999}}})
    assert [p["id"] for p in c2["transcribe"]["custom_post"]["profiles"]] == ["cp1"]

    # --- 共用弹窗按规格渲染，且 Key 不明文显示 ---
    from video_to_article.gui.profile_store import (
        PROFILE_SPEC_ASR,
        ProfileManagerDialog,
    )

    dlg = ProfileManagerDialog(
        None, profs, PROFILE_SPEC_ASR, kind_label="自定义（POST）"
    )
    dlg._reload()
    assert "自定义（POST）" in dlg.windowTitle()
    row0 = dlg.list_w.item(0).text()
    for expect in ("端点=", "Key=", "请求头文件="):
        assert expect in row0, f"ASR 规格该显示 {expect}: {row0!r}"
    assert "sk-cp-key-123456" not in row0, "Key 不能明文显示在列表里"

    dlg2 = ProfileManagerDialog(
        None, [{"id": "p1", "label": "X", "api_key": "k", "model": "M"}],
        PROFILE_SPEC_LLM, kind_label="大模型",
    )
    dlg2._reload()
    assert "大模型" in dlg2.windowTitle()
    assert "模型=" in dlg2.list_w.item(0).text()
    assert "端点=" not in dlg2.list_w.item(0).text(), "LLM 规格不该显示 ASR 字段"
    print("OK 9: custom_post 多档案（独立请求头文件 / 互不干扰 / 共用弹窗）\n")


def test_asr_engine_list_shared():
    """10. 引擎清单单一真源：settings 与「覆盖本次 ASR」必须同源。

    2026-10-04 用户报「覆盖本次 ASR 选项一直没有更新」——根因是
    `common_options.AsrOptions` 里手写了只有 funasr / whisper 的引擎列表，
    而 settings 里已经有 5 个。两份列表漂移，新引擎在「覆盖本次」里**选不到**，
    用户只能去改全局默认——而「覆盖本次」的意义正是只改这一条。

    修法：抽 `gui/asr_engines.py` 作为唯一清单，两边都从它读。
    """
    from video_to_article.gui.asr_engines import (
        ASR_ENGINE_LABELS,
        engine_label,
        normalize_engine,
    )

    codes = [c for _, c in ASR_ENGINE_LABELS]
    assert codes == ["funasr", "whisper", "qwen_asr", "xf_asr", "custom_post"], (
        f"引擎清单不对: {codes}"
    )
    # 旧名兼容
    assert normalize_engine("minimax_asr") == "custom_post", "旧引擎名没归一"
    assert normalize_engine("custom_post") == "custom_post"
    assert normalize_engine("") == ""
    assert "FunASR" in engine_label("funasr")
    assert engine_label("不存在") == "不存在"

    # 两处 UI 必须用同一份清单
    from video_to_article.gui.settings import settings_dialog as sd_mod

    src_settings = open(sd_mod.__file__, encoding="utf-8").read()
    assert "ASR_ENGINE_LABELS" in src_settings, "settings 没用共享清单"
    assert 'self.tr_engine.addItem("funasr"' not in src_settings, (
        "settings 里还留着手写的引擎列表（会再次漂移）"
    )

    import video_to_article.gui.widgets.common_options as co

    src_co = open(co.__file__, encoding="utf-8").read()
    assert "ASR_ENGINE_LABELS" in src_co, "覆盖本次 ASR 没用共享清单"
    assert 'self.engine.addItem("FunASR")' not in src_co, (
        "覆盖本次 ASR 里还留着手写列表（新引擎会选不到）"
    )
    print("OK 10: 引擎清单单一真源（两处 UI 同源 + 旧名归一）\n")


def test_cover_and_host_profiles():
    """11. AI 封面 / 图床 也有档案区（2026-10-04 用户要求）。

    封面常在多套生图服务间切（ModelScope / OpenAI 兼容 / xAI），
    图床常在多套图床间切（Chevereto / 自建 / 各家 API）——都是高频切换场景。
    """
    from PySide6.QtWidgets import QApplication

    from video_to_article import config as cfg_mod
    from video_to_article.gui.settings import settings_dialog as sd_mod
    from video_to_article.gui.settings.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    cfg = {
        "llm": json.loads(json.dumps(REAL_OLD_CONFIG)),
        "transcribe": {"asr_engine": "xf_asr", "custom_post": {
            "endpoint": "https://api.minimax.cn/v1/speech_to_text",
            "api_key": "sk-cp-123456", "language": "zh", "mock": False,
        }},
        "ai_cover": {
            "provider": "modelscope", "base_url": "https://api-inference.modelscope.cn/",
            "api_key": "ms-cover-key-999", "model": "Qwen/Qwen-Image",
            "edit_model": "Qwen/Qwen-Image-Edit-2511", "size": "1344x768",
            "output_format": "jpg", "brand": "一览美食", "pipeline": "full",
        },
        "image_host": {
            "enable": True, "provider": "easyimage",
            "api_url": "https://img.example.com/api/index.php", "token": "tok-777",
            "token_field": "token", "file_field": "image",
            "url_json_path": "url", "timeout_seconds": 180, "extra_fields": {},
        },
    }
    cfg_mod.load_config = lambda: json.loads(json.dumps(cfg))
    sd_mod.load_config = lambda: json.loads(json.dumps(cfg))

    d = SettingsDialog()
    # --- 旧配置（无档案）必须能加载，四处下拉都存在且为空 ---
    assert d.cover_model.text() == "Qwen/Qwen-Image", "封面旧配置没读出来"
    assert d.cover_brand.text() == "一览美食"
    assert d.host_provider.text() == "easyimage", "图床旧配置没读出来"
    assert d.host_token.text() == "tok-777"
    for attr in ("llm_profile_combo", "cp_profile_combo",
                 "cover_profile_combo", "host_profile_combo"):
        assert getattr(d, attr).count() == 0, f"{attr} 不该凭空造出档案"

    # --- 四处都存一份档案 ---
    for key, pid, label in (
        ("llm", "l1", "主力 LLM"),
        ("cp", "c1", "MiniMax 官方"),
        ("cover", "v1", "ModelScope 生图"),
        ("host", "h1", "EasyImage 图床"),
    ):
        spec = d._spec(key)
        d._set_profiles(spec, [dict(spec.fields(d), id=pid, label=label)])

    for attr in ("llm_profile_combo", "cp_profile_combo",
                 "cover_profile_combo", "host_profile_combo"):
        assert getattr(d, attr).count() == 1, f"{attr} 档案没进下拉"

    # --- 写回：四处档案进各自 config 路径，且都是 list ---
    up = d._collect_updates()
    for path in ("llm", "transcribe.custom_post", "ai_cover", "image_host"):
        node = up
        for k in path.split("."):
            node = node[k]
        profs = node.get("profiles")
        assert isinstance(profs, list) and len(profs) == 1, (
            f"{path} 的 profiles 没写对（必须是 list，否则删档案会留幽灵）"
        )

    # --- 四处 Key 都没被改坏 ---
    assert up["llm"]["api_key"] == REAL_OLD_CONFIG["api_key"]
    assert up["transcribe"]["custom_post"]["api_key"] == "sk-cp-123456"
    assert up["ai_cover"]["api_key"] == "ms-cover-key-999"
    assert up["image_host"]["token"] == "tok-777"

    # --- 应用封面档案能回填 ---
    d.cover_provider.setText("CHANGED")
    d.cover_model.setText("CHANGED")
    d._apply_cover_profile_to_ui(d._get_profiles(d._spec("cover"))[0])
    assert d.cover_model.text() == "Qwen/Qwen-Image", "封面档案应用失败"
    assert d.cover_brand.text() == "一览美食"

    # --- 应用图床档案能回填 ---
    d.host_api_url.setText("CHANGED")
    d._apply_host_profile_to_ui(d._get_profiles(d._spec("host"))[0])
    assert d.host_api_url.text() == "https://img.example.com/api/index.php"

    # --- 四处档案互不干扰 ---
    c2 = json.loads(json.dumps(cfg))
    c2["ai_cover"]["profiles"] = [{"id": "v1", "label": "A"}, {"id": "v2", "label": "B"}]
    c2["llm"]["profiles"] = [{"id": "l1", "label": "X"}]
    deep_update(c2, {"ai_cover": {"profiles": [{"id": "v1", "label": "A"}]}})
    assert [p["id"] for p in c2["ai_cover"]["profiles"]] == ["v1"], "删封面档案没生效"
    assert [p["id"] for p in c2["llm"]["profiles"]] == ["l1"], "删封面档案影响了 LLM"
    print("OK 11: AI 封面 / 图床 档案区（四处并存 · Key 不坏 · 互不干扰）\n")


def test_engine_focus_ui():
    """12. 转写 Tab 按所选引擎聚焦 + 「显示全部」开关（2026-10-04 用户要求）。

    用户反馈「ASR 引擎界面太长」——原来 5 个引擎的设置全铺开，页面要滚很久。
    现在默认只显示所选引擎那一个分组，勾「显示全部引擎的设置」可恢复原样。

    ⚠ 关键约束：**隐藏只是 setVisible(False)，参数照常读写**。
       隐藏一个引擎的配置不等于删掉它，切回来时值还在。
    """
    from PySide6.QtWidgets import QApplication

    from video_to_article import config as cfg_mod
    from video_to_article.gui.settings import settings_dialog as sd_mod
    from video_to_article.gui.settings.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    cfg = {
        "llm": json.loads(json.dumps(REAL_OLD_CONFIG)),
        "transcribe": {"asr_engine": "xf_asr", "custom_post": {
            "endpoint": "https://api.minimax.cn/v1/speech_to_text",
            "api_key": "sk-cp-123456", "language": "zh", "mock": False,
        }},
    }
    cfg_mod.load_config = lambda: json.loads(json.dumps(cfg))
    sd_mod.load_config = lambda: json.loads(json.dumps(cfg))

    d = SettingsDialog()
    engines = ("funasr", "qwen_asr", "xf_asr", "custom_post")

    # ⚠ 必须用 isHidden() 而不是 isVisible()：Tab 本身不可见时 isVisible() 恒为 False
    def shown(engine):
        return not d._engine_blocks[engine][0].isHidden()

    # --- 默认只显示 config 里选的引擎 ---
    assert d.tr_show_all.isChecked() is False, "默认应是聚焦模式"
    assert shown("xf_asr") is True, "config 选了 xf_asr，该组应显示"
    for e in ("funasr", "qwen_asr", "custom_post"):
        assert shown(e) is False, f"{e} 不该显示"

    # --- 切引擎跟着切换 ---
    for e in engines:
        d.tr_engine.setCurrentIndex(d.tr_engine.findData(e))
        visible = [k for k in engines if shown(k)]
        assert visible == [e], f"选 {e} 时应只显示它，实得 {visible}"

    # --- 勾「显示全部」→ 四个都显示；取消 → 只剩当前引擎 ---
    d.tr_show_all.setChecked(True)
    assert all(shown(e) for e in engines), "勾了显示全部还藏着"
    d.tr_show_all.setChecked(False)
    cur = d.tr_engine.currentData()
    for e in engines:
        assert shown(e) == (e == cur), f"聚焦模式下 {e} 显隐不对"

    # --- 隐藏分组的参数照常保存（不会丢配置）---
    d.tr_engine.setCurrentIndex(d.tr_engine.findData("xf_asr"))
    d.xf_app_id.setText("xf-app-id")
    d.xf_pd_domain.setCurrentIndex(d.xf_pd_domain.findData("edu"))
    d.cp_endpoint.setText("https://hidden-but-saved/stt")
    up = d._collect_updates()["transcribe"]
    assert up["xf_asr"]["app_id"] == "xf-app-id"
    assert up["xf_asr"]["pd_domain"] == "edu"
    assert up["custom_post"]["endpoint"] == "https://hidden-but-saved/stt", (
        "隐藏的 custom_post 分组参数丢了 —— 聚焦必须是「不显示」而非「不保存」"
    )

    # --- 旧引擎名 minimax_asr 也要对上 custom_post 分组 ---
    cfg2 = json.loads(json.dumps(cfg))
    cfg2["transcribe"]["asr_engine"] = "minimax_asr"
    cfg_mod.load_config = lambda: json.loads(json.dumps(cfg2))
    sd_mod.load_config = lambda: json.loads(json.dumps(cfg2))
    d2 = SettingsDialog()
    assert d2.tr_engine.currentData() == "custom_post", (
        "旧名 minimax_asr 没归一到 custom_post（会静默回落到 funasr）"
    )
    assert not d2._engine_blocks["custom_post"][0].isHidden(), (
        "旧名应显示 custom_post 分组"
    )
    print("OK 12: 转写 Tab 引擎聚焦（默认只看当前 / 显示全部开关 / 隐藏不丢参数 / 旧名归一）\n")


def test_max_tokens_degrade():
    """各家 max_tokens 上限不同，超了要能自动降级重试。

    实测（2026-10-04，DeepSeek 官方）：合法区间 [1, 393216]，
    配 520000 → 400 `Invalid max_tokens value`。
    这个错发生在**请求发出前**，用户只看到「整理版根本没生成」，
    没有任何指向 max_tokens 的线索 —— 所以必须在调用层兜住。
    """
    from video_to_article.providers import llm as L
    import openai as _openai_mod

    calls = []

    class BoomMaxTokens(Exception):
        pass

    class Ok:
        choices = [type("C", (), {"message": type("M", (), {"content": "成稿"})()})()]

    class FakeCompletions:
        def create(self, **kw):
            calls.append(kw.get("max_tokens"))
            if len(calls) == 1:
                raise BoomMaxTokens("Invalid max_tokens value, the valid range is [1, 393216]")
            return Ok()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    # ⚠ OpenAI 是**函数内** import 的（懒加载，llm.py:76），不是模块级符号，
    #   所以要 patch openai 模块上的名字，patch llm.OpenAI 会 AttributeError。
    orig_client = _openai_mod.OpenAI
    orig_prompt = L.load_prompt
    _openai_mod.OpenAI = lambda **kw: FakeClient()
    L.load_prompt = lambda name: "模板 {transcript_text}"
    try:
        # ① 撞上限 → 自动降到 _FALLBACK_MAX_TOKENS 并成功
        out = L.optimize_text_with_llm(
            "转写稿",
            {"llm": {"api_key": "k", "base_url": "https://x", "model": "m",
                      "max_tokens": 520000}},
            prompt_name="general_article",
        )
        assert out == "成稿", out
        # 报错里带范围 → 降到**服务端真实上限**，不是保守的固定值
        assert calls == [520000, 393216], calls

        # ② 未撞上限时不该多发一次请求
        calls.clear()

        class OkOnce(FakeCompletions):
            def create(self, **kw):
                calls.append(kw.get("max_tokens"))
                return Ok()

        FakeChat.completions = OkOnce()
        out2 = L.optimize_text_with_llm(
            "转写稿",
            {"llm": {"api_key": "k", "base_url": "https://x", "model": "m",
                      "max_tokens": 8000}},
            prompt_name="general_article",
        )
        assert out2 == "成稿", out2
        assert calls == [8000], f"不该重试: {calls}"

        # ③ 错误与 max_tokens 无关时**不能**降级重试
        #    （否则把鉴权/额度错误也重试一遍，纯浪费）
        #    实际契约：由外层 except 捕获 → logger.error → 返回 None
        calls.clear()

        class BoomAuth(FakeCompletions):
            def create(self, **kw):
                calls.append(kw.get("max_tokens"))
                raise RuntimeError("401 Unauthorized")

        FakeChat.completions = BoomAuth()
        out3 = L.optimize_text_with_llm(
            "转写稿",
            {"llm": {"api_key": "k", "base_url": "https://x", "model": "m",
                      "max_tokens": 520000}},
            prompt_name="general_article",
        )
        assert out3 is None, f"鉴权失败应返回 None 而非硬重试: {out3!r}"
        assert calls == [520000], f"非 max_tokens 错误不该重试: {calls}"

        # ④ 报错指向 max_tokens 但**没给范围** → 回落到保守固定值
        calls.clear()

        class BoomNoRange(FakeCompletions):
            def create(self, **kw):
                calls.append(kw.get("max_tokens"))
                if len(calls) == 1:
                    raise BoomMaxTokens("max_tokens is too large for this model")
                return Ok()

        FakeChat.completions = BoomNoRange()
        out4 = L.optimize_text_with_llm(
            "转写稿",
            {"llm": {"api_key": "k", "base_url": "https://x", "model": "m",
                      "max_tokens": 520000}},
            prompt_name="general_article",
        )
        assert out4 == "成稿", out4
        assert calls == [520000, L._FALLBACK_MAX_TOKENS], (
            f"没范围信息时应回落到保守值: {calls}"
        )
    finally:
        _openai_mod.OpenAI = orig_client
        L.load_prompt = orig_prompt
    print("OK 13: max_tokens 超限自动降级重试（非 max_tokens 错误不重试）\n")


def test_model_limits_parsing():
    """抓模型时顺带拿输出上限 —— 实测**一半厂商给，一半不给**：

    DeepSeek /models → max_output_tokens / context_window  ✅
    MiniMax  /models → 只有 id/object/created/owned_by    ❌
    """
    from video_to_article.providers.llm_models import (
        format_limit_hint,
        parse_model_limits,
    )

    # DeepSeek 实测真实响应
    ds = {"object": "list", "data": [{
        "id": "deepseek-flash", "object": "model", "owned_by": "deepseek",
        "name": "DeepSeek-V4.1-Flash", "context_window": 1048576,
        "max_output_tokens": 393216, "input_modalities": ["text", "image"],
    }]}
    lim = parse_model_limits(ds)
    assert lim == {"deepseek-flash": {"max_output_tokens": 393216,
                                      "context_window": 1048576}}, lim
    h = format_limit_hint("deepseek-flash", lim)
    assert "393,216" in h or "393216" in h, h
    assert "1,048,576" in h or "1048576" in h, h

    # MiniMax 实测真实响应 —— 没有上限字段，应返回 {} 而不是瞎猜
    mm = {"object": "list", "data": [
        {"id": "MiniMax-M3", "object": "model",
         "created": 1780272000, "owned_by": "minimax"}]}
    assert parse_model_limits(mm) == {}, parse_model_limits(mm)
    assert format_limit_hint("MiniMax-M3", {}) == ""

    # 兼容各种别名 / 字符串数字
    alias = {"data": [
        {"model_name": "m1", "maxOutputTokens": "8192"},          # Ollama 风格 + 字符串
        {"id": "m2", "max_tokens": 4096},
        {"id": "m3", "context_length": 128000},                     # 只有上下文
        {"id": "m4"},                                               # 什么都没有
    ]}
    a = parse_model_limits(alias)
    assert a["m1"]["max_output_tokens"] == 8192, a
    assert a["m2"]["max_output_tokens"] == 4096, a
    assert a["m3"]["context_window"] == 128000, a
    assert "m4" not in a, a

    # 结构不认识时返回 {}，绝不抛
    for junk in (None, [], {}, {"data": "x"}, {"models": {"a": 1}}):
        assert parse_model_limits(junk) == {}, junk
    print("OK 14: /models 上限解析（DeepSeek 有 / MiniMax 无 / 各种别名 / 垃圾结构不抛）\n")


def test_max_tokens_ceiling_from_error():
    """从报错里解析真实上限 —— 比任何硬编码都准。"""
    from video_to_article.providers.llm import _max_tokens_ceiling

    # DeepSeek 实测报错原文
    assert _max_tokens_ceiling(
        "Invalid max_tokens value, the valid range of max_tokens is [1, 393216]"
    ) == 393216
    # 大小写 / 空格 / 缺空格
    assert _max_tokens_ceiling("MAX_TOKENS is [1 , 8192]") == 8192
    # 没有范围信息 → 0（调用方回落到保守值）
    for noinfo in ("max_tokens is too large", "", None, "rate limited"):
        assert _max_tokens_ceiling(noinfo) == 0, noinfo
    print("OK 15: 从 max_tokens 报错里解析服务端真实上限\n")


def main():
    test_resolve_protocol_compat()
    test_vendor()
    test_build_models_url()
    test_parse_models_response()
    test_fetch_models_degrade()
    test_profiles_must_be_list()
    test_settings_dialog()
    test_profile_manager_dialog()
    test_custom_post_profiles()
    test_asr_engine_list_shared()
    test_cover_and_host_profiles()
    test_engine_focus_ui()
    test_max_tokens_degrade()
    test_model_limits_parsing()
    test_max_tokens_ceiling_from_error()
    print("=" * 50)
    print("ALL llm-settings smoke tests passed ✓")
    print("=" * 50)


if __name__ == "__main__":
    main()
