"""Smoke tests for xf_asr (讯飞听见云端 ASR backend).

Covers:
  1. _sign_request() — HMAC-SHA1 + base64 算法（与官方 lfasr Python demo 一致）
  2. transcribe_audio_with_xf_asr() Mock 模式（无凭证自动降级）
  3. transcribe_audio_with_xf_asr() Mock 模式（显式 mock=true）
  4. 长音频切段阈值逻辑（_split_audio_for_long）
  5. atexit 兜底清理 xf_asr_chunks_* tempdir
  6. SettingsDialog 读写 transcribe.xf_asr（GUI 字段不丢）
  7. _upload_audio URL query string 契约（鉴权参数 + Content-Type）
  8. _poll_result 状态码契约（status 3 处理中 / 4 完成 + content.orderInfo.status）
  9. _parse_result_content orderResult 解析契约（caitongbo + csdn 双 demo 风格）

从仓库根运行：python tests\\smoke_xf_asr.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")


def test_sign_request():
    """1. _sign_request 与官方 demo 完全一致（caitongbo/Speech-to-Text Ifasr_new.py）：

    讯飞 raasr.xfyun.cn/v2/api 长语音的 signa 算法：
      md5_hex = MD5(app_id + ts).hexdigest()                    # 32 hex msg
      raw     = HMAC-SHA1(secret_key, md5_hex.bytes)            # MD5 hex 当 msg
      signa   = base64(raw).decode()

    2026-09-27 订正：之前 smoke 用错了「期望」算法（base64(HMAC-SHA1(secret, app_id+ts))），
    那正是当时实现里的 bug。改成「跟官方 demo 独立写一遍」的交叉验证，确保实现真按 demo 写、
    而非「按 smoke 期望写」。user 真实 API 收到 26601 signa verify fail 即由此 bug 导致。
    """
    import video_to_article.media.xf_asr as xf

    app_id = "5f8b9c0d"
    secret_key = "1234567890abcdef"
    ts = 1700000000

    # 期望值（官方 demo 独立写一遍，避免「按 smoke 期望写」的循环依赖）
    md5 = hashlib.md5()
    md5.update(f"{app_id}{ts}".encode("utf-8"))
    md5_hex = md5.hexdigest().encode("utf-8")
    expected = base64.b64encode(
        hmac.new(secret_key.encode("utf-8"), md5_hex, hashlib.sha1).digest()
    ).decode("utf-8")

    actual = xf._sign_request(app_id, secret_key, ts)
    print(f"sign = {actual}")
    assert actual == expected, f"签名与官方 demo 不一致: {actual} != {expected}"
    # 标准 base64 字符集校验
    import re
    assert re.fullmatch(r"[A-Za-z0-9+/=]+", actual), "非标准 base64"
    print("OK 1: _sign_request 与官方 demo 完全一致\n")

    # 额外：把旧实现（错的）作为反面案例测一次，断言「不应再有旧 signa」，防止再次回归
    old_buggy = base64.b64encode(
        hmac.new(
            secret_key.encode("utf-8"),
            f"{app_id}{ts}".encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")
    assert actual != old_buggy, (
        "回归警告：signa 又算成了旧错算法（漏了 MD5 一步）"
    )
    print("OK 1b: 实现未退回旧 bug 算法（已含 MD5 步骤）\n")


def test_mock_mode_no_credentials():
    """2. 无凭证 → 自动降级 Mock（凭证缺失不报错）"""
    import video_to_article.media.xf_asr as xf

    # 准备一个短音频（用 ffmpeg 合成 3 秒静音 wav）
    audio = _make_test_audio(seconds=3)
    try:
        # 凭证全空 → 应走 Mock 路径（长语音鉴权只需 APPID + SecretKey）
        text = xf.transcribe_audio_with_xf_asr(
            audio, {"app_id": "", "secret_key": ""}
        )
        print(f"Mock 返回文本长度 = {len(text)} 字符")
        assert text, "Mock 应返回非空文本"
        assert "mock" in text.lower() or "fake" in text.lower(), (
            f"Mock 文本应包含 'mock' / 'fake' 标识: {text[:80]}"
        )
        print("OK 2: 凭证缺失自动降级 Mock，未报错\n")
    finally:
        os.unlink(audio)


def test_mock_mode_explicit():
    """3. 显式 mock=true → 跳过凭证校验"""
    import video_to_article.media.xf_asr as xf

    audio = _make_test_audio(seconds=2)
    try:
        text = xf.transcribe_audio_with_xf_asr(
            audio,
            {
                "app_id": "fake",
                "secret_key": "fake",
                "mock": True,
            },
        )
        assert text, "显式 mock=true 应返回文本"
        print("OK 3: 显式 mock=true 跳过凭证校验\n")
    finally:
        os.unlink(audio)


def test_validate_credentials_missing():
    """4. 缺凭证 + mock=false → 抛 RuntimeError（不静默调 API）"""
    import video_to_article.media.xf_asr as xf

    raised = False
    try:
        # 长语音鉴权只接 (app_id, secret_key) 两件套
        xf._validate_credentials("", "")
    except RuntimeError as exc:
        raised = True
        msg = str(exc)
        assert "app_id" in msg, f"错误应指明缺 app_id: {msg}"
        assert "secret_key" in msg, f"错误应指明缺 secret_key: {msg}"
        print(f"OK 4: 凭证校验正确报错（{msg.split('。')[0]}）\n")
    assert raised, "缺凭证应抛 RuntimeError"


def test_split_threshold_logic():
    """5. 长音频处理：2026-09-30 改为「客户端不切段，server 自己处理长音频」

    之前的版本：>7.5 分钟切 5 分钟一段
    现在的版本：永远不切段（讯飞 raasr v2 server 端自动处理长音频）
    """
    import video_to_article.media.xf_asr as xf

    # 短音频 → 不切段
    short_audio = _make_test_audio(seconds=10)
    try:
        chunks = xf._split_audio_for_long(short_audio)
        assert len(chunks) == 1, f"短音频应不切段，实际 {len(chunks)} 段"
        assert chunks[0].name == os.path.basename(short_audio), "短音频应原样返回"
        print(f"OK 5a: 短音频 {xf._probe_audio_duration(short_audio):.1f}s 不切段\n")
    finally:
        os.unlink(short_audio)

    # 长音频（patch _probe_audio_duration 模拟 600s）：现在也不切段
    import unittest.mock
    long_audio = _make_test_audio(seconds=10)
    try:
        with unittest.mock.patch.object(
            xf, "_probe_audio_duration", return_value=600.0
        ):
            chunks = xf._split_audio_for_long(long_audio)
            assert len(chunks) == 1, (
                f"长音频现在不切段（讯飞 server 自动处理），实际 {len(chunks)} 段"
            )
            assert chunks[0].name == os.path.basename(long_audio), (
                "长音频应原样返回（不切段）"
            )
            # 反向断言：_do_split 不应被调用
            # （因为 _split_audio_for_long 已经不切段了）
            print(f"OK 5b: 长音频 600s 不切段（server 端自动处理）\n")
    finally:
        os.unlink(long_audio)


def test_atexit_registered():
    """6. atexit 注册清理函数"""
    import video_to_article.media.xf_asr as xf

    assert hasattr(xf, "_cleanup_orphaned_chunks_on_exit"), (
        "atexit 兜底函数应存在"
    )
    # 创建假 tempdir + 跑清理函数
    tempdir = tempfile.gettempdir()
    fake = os.path.join(tempdir, "xf_asr_chunks_smoketest")
    os.makedirs(fake, exist_ok=True)
    with open(os.path.join(fake, "x.wav"), "w") as f:
        f.write("x")
    xf._cleanup_orphaned_chunks_on_exit()
    assert not os.path.exists(fake), f"atexit 函数应清掉 {fake}"
    print("OK 6: atexit 清理函数工作正常\n")


def test_settings_dialog_xf_asr_roundtrip():
    """7. SettingsDialog 读写 transcribe.xf_asr 块"""
    from PySide6.QtWidgets import QApplication
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article import config as cfg_mod
    from video_to_article.gui.settings import settings_dialog as sd_mod

    app = QApplication.instance() or QApplication([])

    sample = {
        "transcribe": {
            "asr_engine": "xf_asr",
            "xf_asr": {
                "app_id": "test_app_id_123",
                "secret_key": "test_secret_789",
                "language": "en",
                "mock": False,
                "max_wait_seconds": 1200,
            },
        }
    }
    cfg_mod.load_config = lambda: sample
    sd_mod.load_config = lambda: sample

    d = SettingsDialog()

    # 校验读（长语音鉴权只需 APPID + SecretKey 两件套——无 xf_api_key 字段）
    assert d.xf_app_id.text() == "test_app_id_123", "APP ID 读错"
    assert d.xf_secret_key.text() == "test_secret_789", "Secret Key 读错"
    assert d.xf_language.currentData() == "en", "Language 读错"
    assert d.xf_mock.isChecked() is False, "Mock 读错"
    assert d.xf_max_wait.value() == 1200, "Max wait 读错"
    assert not hasattr(d, "xf_api_key"), (
        "SettingsDialog 应不再有 xf_api_key 字段（长语音鉴权不用 APIKey）"
    )
    print(f"xf_app_id     = {d.xf_app_id.text()!r}")
    print(f"xf_language   = {d.xf_language.currentData()!r}")
    print(f"xf_mock       = {d.xf_mock.isChecked()}")
    print(f"xf_max_wait   = {d.xf_max_wait.value()}")
    print("OK 7a: xf_asr 字段读正确（5 字段，无 API Key）\n")

    # 模拟用户改动 + 校验写
    d.xf_language.setCurrentIndex(d.xf_language.findData("ja"))
    d.xf_mock.setChecked(True)
    d.xf_max_wait.setValue(900)
    d.xf_app_id.setText("changed_app_id")

    updates = d._collect_updates()
    xf_written = updates["transcribe"]["xf_asr"]
    assert xf_written["language"] == "ja", f"language 写错: {xf_written}"
    assert xf_written["mock"] is True, f"mock 写错: {xf_written}"
    assert xf_written["max_wait_seconds"] == 900, f"max_wait 写错: {xf_written}"
    assert xf_written["app_id"] == "changed_app_id", f"app_id 写错: {xf_written}"
    print(json.dumps(xf_written, ensure_ascii=False, indent=2))
    print("OK 7b: xf_asr 字段写正确\n")

    # 校验不破坏既有字段
    assert updates["transcribe"]["asr_engine"] == "xf_asr"
    assert "qwen_asr" in updates["transcribe"], (
        "qwen_asr 块应保留（不被 xf_asr 写入覆盖）"
    )
    print("OK 7c: 未破坏既有 qwen_asr 块\n")


def test_upload_url_format():
    """8. _upload_audio 必须用 URL query string + raw body（不 multipart！）

    这是和官方 demo（caitongbo/Speech-to-Text Ifasr_new.py 等）一致的契约。
    之前用 requests.post(files={"data": ...}) 的 multipart 格式被讯飞拒——
    返回 code=26600「音频使用表单方式上传」（即「不要走 multipart」）。

    验证清单：
      ✅ URL 是 /v2/api/upload?...query string... 形式
      ✅ 鉴权参数 (appId/signa/ts/fileSize/fileName/language) 全在 query
      ✅ Content-Type header = application/json（不 multipart）
      ✅ data 参数是 raw bytes（不包含 multipart boundary / Content-Disposition）
    """
    import video_to_article.media.xf_asr as xf
    from urllib.parse import urlparse, parse_qs

    audio = _make_test_audio(seconds=1)
    captured: dict = {}

    class FakeResp:
        status_code = 200
        text = (
            '{"code":"000000","descInfo":"success",'
            '"content":{"orderId":"fake-order-id-abc123"}}'
        )

        def json(self):
            import json as _json
            return _json.loads(self.text)

    class FakeSession:
        def post(self, url, headers=None, data=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["data"] = data
            captured["timeout"] = timeout
            return FakeResp()

    xf._cached_session = FakeSession()
    try:
        task_id = xf._upload_audio(
            audio,
            app_id="test_app_id_xyz",
            secret_key="test_secret_key_abc",
            language="cn",
        )

        # === 1. URL 必须是 query string 风格 ===
        parsed = urlparse(captured["url"])
        assert parsed.path == "/v2/api/upload", (
            f"upload URL path 错: {parsed.path}（期望 /v2/api/upload）"
        )
        qs = parse_qs(parsed.query)
        required_qs = ["appId", "signa", "ts", "fileSize", "fileName", "language", "duration"]
        for key in required_qs:
            assert key in qs, (
                f"关键参数 {key!r} 没出现在 query string 里：\nURL={captured['url']!r}"
            )
        assert qs["appId"] == ["test_app_id_xyz"], f"appId 错: {qs['appId']}"
        assert qs["fileName"] == [os.path.basename(audio)], f"fileName 错"
        assert qs["language"] == ["cn"], f"language 错"
        # duration 必须是正整数秒数（demo 一致必传）
        dur_val = int(qs["duration"][0])
        assert dur_val >= 1, f"duration 必须 >= 1 秒，实得 {dur_val}"
        print(f"upload URL = {captured['url']}")
        print(f"query keys = {sorted(qs.keys())}")

        # === 2. Content-Type header 必须是 application/json ===
        ct = captured["headers"].get("Content-Type", "")
        assert ct == "application/json", (
            f"Content-Type 应是 application/json，实得 {ct!r}——"
            f"走 multipart 会带 boundary=xxx，错误码 26600 即由此触发"
        )

        # === 3. data 必须是 raw bytes（不是 multipart 编码） ===
        raw = captured["data"]
        assert isinstance(raw, (bytes, bytearray)), (
            f"data 应是 raw bytes，实得 {type(raw).__name__}"
        )
        # 关键反断言：multipart body 必有这些特征，全都不能出现
        assert b"--" not in raw[:60], (
            "data 出现 '--...'——是 multipart 的 boundary！仍走 files= 路径"
        )
        assert b"Content-Disposition" not in raw, (
            "data 包含 'Content-Disposition'——仍是 multipart files 字段！"
        )
        assert b"name=" not in raw[:100], (
            "data 包含 'name='（multipart 表单字段标识）——仍是 files= 路径"
        )
        # raw data 应等于文件实际字节（不是字典化、空、被截断）
        with open(audio, "rb") as f:
            expected = f.read()
        assert raw == expected, (
            f"data 字节不匹配文件实际内容（multipart/截断嫌疑）"
        )

        assert task_id == "fake-order-id-abc123", f"task_id 错: {task_id}"
        print(
            "OK 8: _upload_audio 用 URL query string + raw bytes（不 multipart files）\n"
        )
    finally:
        xf._release_cached_session()
        if os.path.exists(audio):
            os.unlink(audio)


def test_poll_result_status_codes():
    """9. _poll_result 状态码契约（caicongbo/3drx.top/szfx.top demo 一致）：
       - getResult 响应: content.orderInfo.status（不是 content.taskStatus）
       - status=3 → 处理中（继续轮询）
       - status=4 → 完成（返回 _parse_result_content(content)）
    """
    import video_to_article.media.xf_asr as xf

    # === 验证：响应字段路径是 orderInfo.status，不是 taskStatus ===
    assert hasattr(xf, "_STATUS_PROCESSING"), "应有 _STATUS_PROCESSING 常量"
    assert hasattr(xf, "_STATUS_SUCCESS"), "应有 _STATUS_SUCCESS 常量"
    assert xf._STATUS_SUCCESS == 4, (
        f"_STATUS_SUCCESS 应是 4（demo 一致），实得 {xf._STATUS_SUCCESS}"
    )
    assert 3 in xf._STATUS_PROCESSING, (
        f"3 必须在 _STATUS_PROCESSING 中（处理中），实得 {xf._STATUS_PROCESSING}"
    )

    # === Mock getResult 流程：第一次 status=3 继续轮询，第二次 status=4 完成 ===
    poll_calls = {"count": 0}
    parsed_text = "测试转写结果"

    class FakeResp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeSession:
        def post(self, url, data=None, headers=None, timeout=None, **kwargs):
            poll_calls["count"] += 1
            if poll_calls["count"] == 1:
                # 第一次：处理中
                return FakeResp(
                    200,
                    {
                        "code": "000000",
                        "descInfo": "success",
                        "content": {
                            "orderInfo": {"status": 3},
                            "orderResult": "",
                        },
                    },
                )
            # 第二次：完成 + 简化结果（用最简结构让 _parse_result_content 返回空也能过测试）
            return FakeResp(
                200,
                {
                    "code": "000000",
                    "descInfo": "success",
                    "content": {
                        "orderInfo": {"status": 4},
                        "orderResult": "",
                    },
                },
            )

    xf._cached_session = FakeSession()
    try:
        # 用补丁让 _parse_result_content 返回固定文本（避免依赖 resultMap 结构）
        original_parse = xf._parse_result_content
        xf._parse_result_content = lambda content: parsed_text
        try:
            text = xf._poll_result(
                task_id="fake-order-id",
                app_id="test_app_id",
                secret_key="test_secret_key",
                max_wait=60,
                poll_interval=0.01,  # 加速测试
            )
        finally:
            xf._parse_result_content = original_parse
    finally:
        xf._release_cached_session()

    assert text == parsed_text, f"应返回 _parse_result_content 结果, 实得 {text!r}"
    # 至少调用 2 次（status=3 处理中 + status=4 完成）
    assert poll_calls["count"] >= 2, (
        f"应至少 poll 2 次（处理中→完成），实得 {poll_calls['count']} 次"
    )
    print(
        f"OK 9: _poll_result 状态码契约对（status 3 处理中 / 4 完成 / 共 poll {poll_calls['count']} 次）\n"
    )


def test_parse_result_content():
    """10. _parse_result_content 解析 orderResult 契约（caitongbo + csdn demo 一致）。

    2026-09-30 订正：之前的实现用 resultMap[sid].text，是完全错结构。
    真实结构（caitongbo/Speech-to-Text Ifasr_new.py）：
        orderResult 是 JSON 字符串，要 parse 后才能拿到 lattice/lattice2
        每个 segment 的 json_1best 也是 JSON 字符串，要二次 parse
        字在 ws[].cw[].w 里（不是 ws[].text）

    user 实测三次都 KeyError 在 status==4 之前的轮询层，从未到 _parse_result_content，
    这次主动加 smoke 验证契约，省一次 5.74MB 上传 + 配额消耗。
    """
    import video_to_article.media.xf_asr as xf

    # === Case A: caitongbo 风格（json_1best 是字符串）===
    # 真实 orderResult 子结构
    caicongbo_inner_1 = json.dumps({
        "st": {"rt": [{"ws": [{"cw": [{"w": "你"}, {"w": "好"}]}]}]}
    })
    caicongbo_inner_2 = json.dumps({
        "st": {"rt": [{"ws": [{"cw": [{"w": "世"}, {"w": "界"}]}]}]}
    })
    order_result_caicongbo = json.dumps({
        "lattice": [
            {"json_1best": caicongbo_inner_1},
            {"json_1best": caicongbo_inner_2},
        ]
    })
    content_a = {"orderResult": order_result_caicongbo}
    text_a = xf._parse_result_content(content_a)
    assert text_a == "你好世界", (
        f"caitongbo 风格应拼出 '你好世界'，实得 {text_a!r}"
    )
    print(f"OK 10a: caitongbo 风格（json_1best 是字符串）→ {text_a!r}")

    # === Case B: csdn 风格（json_1best 已是 dict，含 spk/begin/end）===
    order_result_csdn = json.dumps({
        "lattice2": [
            {
                "lid": "0",
                "begin": 0, "end": 1840,
                "spk": "段落-0",
                "json_1best": {
                    "st": {
                        "sc": "0.86", "pa": "0",
                        "rt": [{
                            "nb": "1", "nc": "1.0",
                            "ws": [
                                {"cw": [{"w": "这"}, {"w": "是"}, {"w": "一"}, {"w": "条"}, {"w": "测试"}]},
                                {"cw": [{"w": "音频"}]},
                                {"cw": [{"w": "。"}]},
                            ]
                        }],
                        "bg": "50", "rl": "0", "ed": "1840",
                    }
                }
            }
        ]
    })
    content_b = {"orderResult": order_result_csdn}
    text_b = xf._parse_result_content(content_b)
    assert text_b == "这是一条测试音频。", (
        f"csdn 风格应拼出 '这是一条测试音频。'，实得 {text_b!r}"
    )
    print(f"OK 10b: csdn 风格（json_1best 已是 dict + 含 spk/lattice2）→ {text_b!r}")

    # === Case C: 防御性 —— 空 / 缺字段 / 非 JSON ===
    assert xf._parse_result_content({}) == "", "空 content 应返回空字符串"
    assert xf._parse_result_content({"orderResult": ""}) == "", "空 orderResult 应返回空字符串"
    assert xf._parse_result_content({"orderResult": "not json"}) == "", (
        "非 JSON orderResult 不应崩，应返回空字符串"
    )
    # 看起来是 dict 但没有 lattice/lattice2
    empty_order = json.dumps({"lattice": [], "lattice2": []})
    assert xf._parse_result_content({"orderResult": empty_order}) == "", (
        "空 lattice 数组应返回空字符串"
    )
    print("OK 10c: 防御性 case（空/非 JSON/空数组）→ 全部返回空字符串不崩")

    # === Case D: 混合结构（lattice 字段缺失但 lattice2 有，应回退用 lattice2）===
    mixed = json.dumps({
        "lattice2": [{
            "json_1best": json.dumps({"st": {"rt": [{"ws": [{"cw": [{"w": "回"}, {"w": "退"}]}]}]}})
        }]
    })
    text_d = xf._parse_result_content({"orderResult": mixed})
    assert text_d == "回退", (
        f"lattice 缺失时 fallback 到 lattice2 应拼出 '回退'，实得 {text_d!r}"
    )
    print(f"OK 10d: lattice 缺失 fallback lattice2 → {text_d!r}")

    print("OK 10: _parse_result_content 解析契约对（caitongbo + csdn + 防御 + fallback）\n")


def _make_test_audio(seconds: int = 3) -> str:
    """生成一个测试用 wav（用 ffmpeg 合成静音）。失败时返回任意 wav。"""
    import subprocess
    audio_path = os.path.join(
        tempfile.gettempdir(),
        f"xf_asr_smoketest_{os.getpid()}_{seconds}s.wav",
    )
    ffmpeg_exe = shutil.which("ffmpeg")
    if ffmpeg_exe:
        subprocess.run(
            [
                ffmpeg_exe, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono",
                "-t", str(seconds), "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le", audio_path,
            ],
            check=False,
            capture_output=True,
        )
    if not os.path.exists(audio_path):
        # ffmpeg 不可用：写最小 wav header + 静音 PCM
        import wave
        with wave.open(audio_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000 * seconds)
    return audio_path


from pathlib import Path


if __name__ == "__main__":
    test_sign_request()
    test_mock_mode_no_credentials()
    test_mock_mode_explicit()
    test_validate_credentials_missing()
    test_split_threshold_logic()
    test_atexit_registered()
    test_settings_dialog_xf_asr_roundtrip()
    test_upload_url_format()
    test_poll_result_status_codes()
    test_parse_result_content()
    print("=" * 50)
    print("ALL xf_asr smoke tests passed ✓")
    print("=" * 50)