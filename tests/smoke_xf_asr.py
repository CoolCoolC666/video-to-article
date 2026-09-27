"""Smoke tests for xf_asr (讯飞听见云端 ASR backend).

Covers:
  1. _sign_request() — HMAC-SHA1 + base64 算法（与官方 lfasr Python demo 一致）
  2. transcribe_audio_with_xf_asr() Mock 模式（无凭证自动降级）
  3. transcribe_audio_with_xf_asr() Mock 模式（显式 mock=true）
  4. 长音频切段阈值逻辑（_split_audio_for_long）
  5. atexit 兜底清理 xf_asr_chunks_* tempdir
  6. SettingsDialog 读写 transcribe.xf_asr（GUI 字段不丢）

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
    """5. 长音频切段阈值：> 7.5 分钟必切，< 不切"""
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

    # 长音频（无法实测 7.5 分钟音频文件，patch _probe_audio_duration 模拟）
    import unittest.mock
    long_audio = _make_test_audio(seconds=10)
    try:
        with unittest.mock.patch.object(
            xf, "_probe_audio_duration", return_value=600.0
        ):
            # 强制 mock 切段（避免真切 600s 的长音频）
            with unittest.mock.patch.object(
                xf, "_do_split", wraps=xf._do_split
            ) as mock_split:
                # _do_split 需要 ffmpeg；用 mock 拦截
                mock_split.return_value = [
                    Path(tempfile.mkdtemp(prefix="xf_asr_chunks_")) / "chunk_000000.wav",
                    Path(tempfile.mkdtemp(prefix="xf_asr_chunks_")) / "chunk_300000.wav",
                ]
                chunks = xf._split_audio_for_long(long_audio)
                assert len(chunks) == 2, f"长音频应切 2 段，实际 {len(chunks)} 段"
                print(f"OK 5b: 长音频 600s 自动切 2 段\n")
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
    print("=" * 50)
    print("ALL xf_asr smoke tests passed ✓")
    print("=" * 50)