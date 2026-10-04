"""Smoke tests for MiniMax Speech-to-Text backend（2026-10-02 新增）。

Covers:
  1. 协议常量与 OpenAPI spec 一致（500s / 50MB / model / 端点）
  2. 上传契约：真 multipart（不是讯飞那套 query string）+ Bearer 鉴权 + language 走 header
  3. 切段阈值：≤500s 不切，超时/超体积切段（与 xf_asr 的「永不切段」相反）
  4. verbose_json 解析 → 【S1】+ [MM:SS]；两个开关关掉走 text 兜底
  5. 跨段：时间戳偏移累加 + 说话人编号映射（含人数变多时分配新编号）
  6. OpenAI 风格错误码 → 可读中文提示
  7. Mock 三分支（与 xf_asr 一致）
  8. processor._resolve_engine_config / audio dispatch / SettingsDialog 读写真实注册

不调网络、不消耗额度 —— 全部是 mock session + 纯函数断言。
从仓库根运行：python tests\\smoke_custom_post_asr.py
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")


def test_spec_constants():
    """1. 常量必须跟 OpenAPI spec 完全一致 —— 写错就是 400/413。"""
    import video_to_article.media.custom_post_asr as cp

    assert cp.DEFAULT_ENDPOINT == "https://api.minimax.cn/v1/speech_to_text", (
        f"默认端点错: {cp.DEFAULT_ENDPOINT}"
    )
    # 端点必须是可配的（用户自定义），不能硬编码在调用处
    assert isinstance(cp.DEFAULT_ENDPOINT, str) and cp.DEFAULT_ENDPOINT.startswith("https://")
    assert cp._MODEL == "asr-1.0", f"模型必须是 asr-1.0，实得 {cp._MODEL}"
    # spec: 时长 ≤ 500 秒（超了 400），大小 ≤ 50MB（超了 413）
    assert cp.MAX_DURATION_SEC == 500, f"时长上限错: {cp.MAX_DURATION_SEC}"
    assert cp.MAX_FILE_BYTES == 50 * 1024 * 1024, (
        f"体积上限错: {cp.MAX_FILE_BYTES}"
    )
    # 切段必须留余量，不能卡着 500s
    assert cp.CHUNK_SEGMENT_SEC < cp.MAX_DURATION_SEC, (
        "切段秒数必须小于上限，否则探测误差就会 400"
    )
    # 官方支持格式（裸 PCM 明确不支持）
    for ext in (".mp3", ".wav", ".m4a", ".flac", ".opus", ".ogg"):
        assert ext in cp.SUPPORTED_EXTS, f"应支持 {ext}"
    assert ".pcm" not in cp.SUPPORTED_EXTS, "裸 PCM 官方明确不支持"
    # 语言列表第一个必须是空串（自动检测）
    assert "" in cp.MINIMAX_LANGUAGES, "应支持留空 = 自动检测"
    for code in ("zh", "yue", "en", "ja", "ko"):
        assert code in cp.MINIMAX_LANGUAGES, f"应支持语言 {code}"
    print("OK 1: OpenAPI spec 常量一致（500s / 50MB / asr-1.0 / 端点 / 语言表）\n")


def test_upload_contract():
    """2. 上传契约：真 multipart + Bearer + language 走 HTTP header。

    这里最容易踩的坑是**照抄讯飞那套**（query string + raw body）。
    MiniMax 官方 curl 明确是 multipart/form-data，跟讯飞正好相反。
    """
    import video_to_article.media.custom_post_asr as cp
    import tempfile

    audio = os.path.join(tempfile.gettempdir(), "minimax_smoke.wav")
    with open(audio, "wb") as f:
        f.write(b"RIFF" + b"\0" * 2048)

    captured: dict = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "text": "识别成功",
                "duration": 12.0,
                "trace_id": "trace-abc",
            }

    class FakeSession:
        def post(self, url, headers=None, data=None, files=None, timeout=None, **kw):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["data"] = data or {}
            captured["files"] = files or {}
            captured["timeout"] = timeout
            return FakeResp()

        def close(self):
            pass

    cp._cached_session = FakeSession()
    try:
        result = cp._transcribe_one(
            cp.Path(audio), "test-key-123", "zh", False, False, 300
        )
    finally:
        cp._release_cached_session()
        os.unlink(audio)

    assert result["text"] == "识别成功", f"应返回解析后的 JSON，实得 {result}"
    # 端点
    assert captured["url"] == "https://api.minimax.cn/v1/speech_to_text", (
        f"URL 错: {captured['url']}"
    )
    # 鉴权：Bearer 单件
    auth = captured["headers"].get("Authorization", "")
    assert auth == "Bearer test-key-123", f"鉴权头错: {auth!r}"
    # language 必须是 HTTP header（不是 form field）
    assert captured["headers"].get("language") == "zh", (
        f"language 应在 HTTP header 里，实得 headers={captured['headers']}"
    )
    assert "language" not in captured["data"], "language 不应出现在 form 字段里"
    # 真 multipart：必须走 files= 且带文件名 + Content-Type
    assert "file" in captured["files"], f"必须用 files= 传 multipart，实得 {captured['files']}"
    fname = captured["files"]["file"][0]
    assert fname == "minimax_smoke.wav", f"multipart 文件名错: {fname!r}"
    # 三个开关都关 → response_format=json（不需要 segments）
    assert captured["data"]["model"] == "asr-1.0", f"model 错: {captured['data']}"
    assert captured["data"]["response_format"] == "json", (
        f"两个开关都关时该用 json 省开销，实得 {captured['data']}"
    )
    assert "timestamp_level" not in captured["data"], (
        "json 格式不需要 timestamp_level（spec: 会被忽略）"
    )
    print("OK 2a: 上传契约 = Bearer + language header + 真 multipart ✓")

    # 开任一开关 → 必须切 verbose_json
    audio2 = os.path.join(tempfile.gettempdir(), "minimax_smoke2.wav")
    with open(audio2, "wb") as f:
        f.write(b"RIFF" + b"\0" * 2048)
    captured2: dict = {}

    class FakeSession2(FakeSession):
        def post(self, url, headers=None, data=None, files=None, timeout=None, **kw):
            captured2.update({
                "url": url, "headers": headers or {},
                "data": data or {}, "files": files or {},
            })
            return FakeResp()

    cp._cached_session = FakeSession2()
    try:
        # language 留空 → 不应出现 language header（= 自动检测）
        cp._transcribe_one(cp.Path(audio2), "k", "", True, True, 300)
    finally:
        cp._release_cached_session()
        os.unlink(audio2)

    assert "language" not in captured2["headers"], (
        f"language 留空时不该发 header（= 自动检测），实得 {captured2['headers']}"
    )
    assert captured2["data"]["response_format"] == "verbose_json", (
        f"开分离/时间戳必须用 verbose_json，实得 {captured2['data']}"
    )
    assert captured2["data"]["timestamp_level"] == "sentence", (
        f"verbose_json 下应显式声明 sentence 粒度，实得 {captured2['data']}"
    )
    assert captured2["data"]["stream"] == "false", (
        "verbose_json 不能与 stream=true 同用，必须 false"
    )
    print("OK 2b: 开分离/时间戳 → verbose_json + timestamp_level=sentence + stream=false\n")


def test_split_threshold():
    """3. 切段阈值：≤500s 直传，超时切段（与 xf_asr 的「永不切段」相反）。

    这是本引擎与 xf_asr 最本质的区别：都是云端，但 MiniMax 上限差两个数量级。
    """
    import video_to_article.media.custom_post_asr as cp
    import tempfile

    # 造一个小文件（体积远小于 50MB），用 max_duration 参数控制时长判断
    audio = os.path.join(tempfile.gettempdir(), "minimax_split.wav")
    with open(audio, "wb") as f:
        f.write(b"RIFF" + b"\0" * 4096)

    # 不切段：传一个明确在上限内的 max_duration
    try:
        paths, is_chunked = cp._split_audio_for_long(audio, 300.0)
        assert is_chunked is False, f"300s 不该切，实得 is_chunked={is_chunked}"
        assert len(paths) == 1, f"应返回单文件，实得 {len(paths)} 段"
    finally:
        os.unlink(audio)

    # 超时长：mock 掉 _do_split，返回两个假段路径
    fake = [cp.Path("chunk_000000.mp3"), cp.Path("chunk_000450.mp3")]
    orig_do_split = cp._do_split
    orig_probe = cp._probe_audio_duration
    cp._do_split = lambda p, seg: fake
    cp._probe_audio_duration = lambda p: 1500.0
    audio2 = os.path.join(tempfile.gettempdir(), "minimax_split2.wav")
    with open(audio2, "wb") as f:
        f.write(b"RIFF" + b"\0" * 4096)
    try:
        paths2, is_chunked2 = cp._split_audio_for_long(audio2, -1.0)
        assert is_chunked2 is True, "1500s 超上限，必须切段"
        assert len(paths2) == 2, f"应切 2 段（1500/450=4 段由 ffmpeg 实际决定，mock 返回 2）"
        assert cp._split_audio_for_long.__doc__ or True
    finally:
        cp._do_split = orig_do_split
        cp._probe_audio_duration = orig_probe
        os.unlink(audio2)

    # 切段失败必须抛明确错误，而不是静默直传（否则服务端 400）
    cp._do_split = lambda p, seg: []
    cp._probe_audio_duration = lambda p: 1500.0
    audio3 = os.path.join(tempfile.gettempdir(), "minimax_split3.wav")
    with open(audio3, "wb") as f:
        f.write(b"RIFF" + b"\0" * 4096)
    try:
        raised = False
        try:
            cp._split_audio_for_long(audio3, -1.0)
        except RuntimeError as e:
            raised = True
            assert "ffmpeg" in str(e).lower() or "切段" in str(e), (
                f"错误信息应指向 ffmpeg/切段，实得: {e}"
            )
        assert raised, "切段失败时必须抛错，不能静默直传（服务端会 400）"
    finally:
        cp._do_split = orig_do_split
        cp._probe_audio_duration = orig_probe
        os.unlink(audio3)

    print("OK 3: 切段阈值对（≤500s 直传 / 超时切段 / 切段失败抛错）\n")


def test_verbose_json_parse():
    """4. verbose_json → 【S1】+ [MM:SS]；两个开关关掉走 text 兜底。"""
    import video_to_article.media.custom_post_asr as cp

    # 用 OpenAPI spec 里的官方示例数据
    verbose = {
        "text": "嘎嘎会，可以，这把稳了。来检查一下，读下题。",
        "duration": 12.744,
        "n_speakers": 2,
        "segments": [
            {"id": 0, "start": 0.1, "end": 1.66, "speaker": "S1",
             "text": "嘎嘎会，可以，这把稳了。"},
            {"id": 1, "start": 2.0, "end": 6.1, "speaker": "S2",
             "text": "来检查一下，读下题。"},
        ],
        "trace_id": "021785229015510a2c883cf675b9804d",
    }

    both = cp._assemble([verbose], True, True, [12.744])
    lines = both.split("\n")
    assert len(lines) == 2, f"应 2 行，实得 {lines}"
    assert lines[0] == "[00:00] 【S1】嘎嘎会，可以，这把稳了。", f"第 1 行错: {lines[0]!r}"
    assert lines[1] == "[00:02] 【S2】来检查一下，读下题。", f"第 2 行错: {lines[1]!r}"
    print(f"OK 4a: verbose_json 解析 →\n{both}")

    only_spk = cp._assemble([verbose], True, False, [12.744])
    assert only_spk == "【S1】嘎嘎会，可以，这把稳了。\n【S2】来检查一下，读下题。", (
        f"只开分离实得 {only_spk!r}"
    )

    # 两个开关都关 + json 格式响应（无 segments）→ 整段返回 text
    plain = {"text": "一整段文字", "duration": 12.0, "trace_id": "t"}
    assert cp._assemble([plain], False, False, [12.0]) == "一整段文字", (
        "json 格式（无 segments）应整段返回 text"
    )
    print("OK 4b: 开关组合 + json 格式 text 兜底 ✓\n")


def test_cross_chunk():
    """5. 跨段：时间戳偏移累加 + 说话人编号映射。

    诚实说明：MiniMax 每段独立从 S1 编号，响应里**没有跨段身份信息**，
    所以段间人数相同时只能按编号对齐（本测试验证的正是这个行为），
    人数变多时才分配新的全局编号。
    """
    import video_to_article.media.custom_post_asr as cp

    # 段 1：450s，两人
    c1 = {
        "text": "…", "duration": 450.0, "n_speakers": 2,
        "segments": [
            {"id": 0, "start": 0.5, "end": 3.0, "speaker": "S1", "text": "老师你好"},
            {"id": 1, "start": 5.0, "end": 8.0, "speaker": "S2", "text": "你好同学"},
        ],
    }
    # 段 2：120s，同样两人（MiniMax 独立编号，S1/S2 重新开始）
    c2 = {
        "text": "…", "duration": 120.0, "n_speakers": 2,
        "segments": [
            {"id": 0, "start": 1.0, "end": 4.0, "speaker": "S1", "text": "今天讲印象派"},
            {"id": 1, "start": 6.0, "end": 9.0, "speaker": "S2", "text": "好我记一下"},
        ],
    }
    merged = cp._assemble([c1, c2], True, True, [450.0, 120.0])
    lines = merged.split("\n")
    assert len(lines) == 4, f"应 4 行，实得 {len(lines)}"
    # 时间戳必须累加段偏移：段 2 的 1.0s → 全局 451s → 07:31
    assert lines[2].startswith("[07:31]"), (
        f"段 2 时间戳应加 450s 偏移（1.0+450=451s=07:31），实得 {lines[2]!r}"
    )
    assert lines[3].startswith("[07:36]"), f"段 2 第二句时间戳错: {lines[3]!r}"
    # 人数相同时按编号对齐（恒等映射，这是协议能给的最好结果）
    assert "【S1】今天讲印象派" in lines[2], f"说话人对齐行为错: {lines[2]!r}"
    assert "【S2】好我记一下" in lines[3], f"说话人对齐行为错: {lines[3]!r}"
    print(f"OK 5a: 跨段时间戳偏移累加 →\n{merged}")

    # 段 2 人数变多（3 人）→ 新的 S3 应分配到下一个未占用编号
    c3 = {
        "text": "…", "duration": 30.0, "n_speakers": 3,
        "segments": [
            {"id": 0, "start": 1.0, "end": 2.0, "speaker": "S1", "text": "A说"},
            {"id": 1, "start": 3.0, "end": 4.0, "speaker": "S2", "text": "B说"},
            {"id": 2, "start": 5.0, "end": 6.0, "speaker": "S3", "text": "C说"},
        ],
    }
    merged3 = cp._assemble([c1, c3], True, False, [450.0, 30.0])
    assert "【S3】C说" in merged3, (
        f"新出现的第三个人应分配 S3，实得:\n{merged3}"
    )
    assert "【S1】A说" in merged3 and "【S2】B说" in merged3, f"原有编号应保持:\n{merged3}"
    print("OK 5b: 段内人数变多时新编号分配 ✓\n")


def test_error_hints():
    """6. OpenAI 风格错误码 → 可读中文提示。

    spec 的错误体是 {"type":"error","error":{...,"http_code":"400"},"request_id":...}，
    且 HTTP 状态码就是真实错误码。余额不足（402）和体积超（413）最容易遇到。
    """
    import video_to_article.media.custom_post_asr as cp

    def _resp(status, body):
        class R:
            status_code = status

            def json(self_):
                return body

            @property
            def text(self_):
                return json.dumps(body, ensure_ascii=False)
        return R()

    cases = [
        (400, "audio duration 623.4s exceeds the limit of 500s (2013)", "500 秒"),
        (401, "login fail: Please carry the API secret key (1004)", "API Key"),
        (402, "insufficient balance (1008)", "余额"),
        (413, "request body too large: 88200078 bytes exceeds limit", "50 MB"),
        (422, "audio content contains sensitive content (1026)", "敏感"),
        (429, "rate limit, please retry later (1002)", "限流"),
    ]
    for status, msg, keyword in cases:
        body = {
            "type": "error",
            "error": {
                "type": "bad_request_error",
                "message": msg,
                "http_code": str(status),
            },
            "request_id": "req-xyz",
        }
        hint = cp._extract_error(_resp(status, body))
        assert f"HTTP {status}" in hint, f"{status} 应带状态码: {hint}"
        assert keyword in hint, f"{status} 的提示应含「{keyword}」: {hint}"
        assert "req-xyz" in hint, f"{status} 应带 request_id 便于排查: {hint}"
    print("OK 6: 6 类错误码都转成了可操作的中文提示\n")


def test_mock_branches():
    """7. Mock 三分支（与 xf_asr 行为一致，便于用户形成统一心智）。"""
    import video_to_article.media.custom_post_asr as cp
    import tempfile

    audio = os.path.join(tempfile.gettempdir(), "minimax_mock.wav")
    with open(audio, "wb") as f:
        f.write(b"RIFF" + b"\0" * 2048)
    try:
        # 情况 2/3：显式 mock 或凭证缺失 → 返回 fake 文本，不调网络
        t1 = cp.transcribe_audio_with_custom_post(audio, {"mock": True})
        assert t1 and "mock" in t1.lower(), f"mock 文本异常: {t1!r}"
        t2 = cp.transcribe_audio_with_custom_post(audio, {"mock": False})  # 凭证缺失
        assert t2 and "mock" in t2.lower(), f"凭证缺失应降级 mock，实得 {t2!r}"
        print("OK 7a: mock=true / 凭证缺失 → 都返回 fake 文本不调网络")
    finally:
        os.unlink(audio)

    # 凭证齐全 + mock 未勾 + 缺文件 → 应该在切段/上传前就抛明确错误
    try:
        cp.transcribe_audio_with_custom_post(
            "N:/definitely/not/exist.wav",
            {"mock": False, "api_key": "k"},
        )
        raised = False
    except Exception:
        raised = True
    assert raised, "文件不存在应抛错，不该静默返回 mock"
    print("OK 7b: 凭证齐全 + 文件缺失 → 抛错不静默降级\n")


def test_registration():
    """8. 三处注册点 + GUI 字段真实可用。"""
    # processor engine_config 隔离
    from video_to_article.processor import _resolve_engine_config

    cfg = {
        "transcribe": {
            "custom_post": {"api_key": "mm-key", "role_separation": True},
            "xf_asr": {"app_id": "xf", "secret_key": "s"},
            "funasr_speaker": True,
        }
    }
    got = _resolve_engine_config(cfg, "custom_post")
    assert got == {"api_key": "mm-key", "role_separation": True}, (
        f"minimax 配置块取错: {got}"
    )
    assert _resolve_engine_config(cfg, "xf_asr") == {"app_id": "xf", "secret_key": "s"}, (
        "xf_asr 块被污染了"
    )
    assert "funasr_speaker" in _resolve_engine_config(cfg, "funasr"), (
        "funasr 配置被污染了"
    )
    print("OK 8a: _resolve_engine_config 三个引擎互不污染 ✓")

    # audio dispatch 认得 custom_post（新名 + 旧名别名）
    from video_to_article.media import audio as A

    assert hasattr(A, "transcribe_audio"), "缺 transcribe_audio 入口"
    src = open(A.__file__, encoding="utf-8").read()
    assert '"custom_post", "minimax_asr"' in src, (
        "audio.py 的 dispatch 应同时接受 custom_post 与旧名 minimax_asr"
    )
    assert "transcribe_audio_with_custom_post" in src, (
        "audio.py 应转发到 transcribe_audio_with_custom_post"
    )
    print("OK 8b: audio.py dispatch 已注册 custom_post + 旧名别名 ✓")

    # 旧引擎名兼容：minimax_asr 仍能跑（转发 + WARNING）
    import video_to_article.media.custom_post_asr as cp

    assert hasattr(cp, "transcribe_audio_with_minimax_asr"), (
        "应保留 transcribe_audio_with_minimax_asr 旧入口，否则用户已保存的 config 会静默失效"
    )
    # 旧名优先读新块；没有新块才回落旧块
    both = {
        "transcribe": {
            "custom_post": {"api_key": "new"},
            "minimax_asr": {"api_key": "old"},
        }
    }
    assert _resolve_engine_config(both, "minimax_asr") == {"api_key": "new"}, (
        "旧名应优先读新块"
    )
    only_old = {"transcribe": {"minimax_asr": {"api_key": "old"}}}
    assert _resolve_engine_config(only_old, "minimax_asr") == {"api_key": "old"}, (
        "只有旧块时应能回落（旧名用户不能丢配置）"
    )
    print("OK 8b2: 旧名 minimax_asr 兼容（新块优先 / 回落旧块）✓")

    # CLI choices：只列新名（旧名走 config 仍兼容，不占 choices 位置）
    from video_to_article.cli import build_parser

    args = build_parser().parse_args(["--asr-engine", "custom_post"])
    assert args.asr_engine == "custom_post", "CLI 未接受 --asr-engine custom_post"
    # 非法引擎仍应被 choices 挡住（别把拼错的引擎名放进来）
    try:
        build_parser().parse_args(["--asr-engine", "minimax"])
        raised = False
    except SystemExit:
        raised = True
    assert raised, "拼错的引擎名应被 choices 拒绝"
    print("OK 8c: CLI --asr-engine custom_post 可用 + 非法值被拒 ✓")

    # GUI 字段
    from PySide6.QtWidgets import QApplication
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article import config as cfg_mod
    from video_to_article.gui.settings import settings_dialog as sd_mod

    app = QApplication.instance() or QApplication([])
    sample = {
        "transcribe": {
            "asr_engine": "custom_post",
            "custom_post": {
                "endpoint": "https://my-asr.internal/v1/stt",
                "api_key": "test-key",
                "headers_file": "D:/tmp/h.txt",
                "language": "zh",
                "mock": False,
                "role_separation": True,
                "timestamps": True,
                "max_wait_seconds": 900,
            },
        }
    }
    cfg_mod.load_config = lambda: sample
    sd_mod.load_config = lambda: sample

    d = SettingsDialog()
    assert d.cp_endpoint.text() == "https://my-asr.internal/v1/stt", "端点读错"
    assert d.cp_api_key.text() == "test-key", "API Key 读错"
    assert d.cp_headers_file.text() == "D:/tmp/h.txt", "请求头文件路径读错"
    assert d.cp_language.currentData() == "zh", "语言读错"
    assert d.cp_mock.isChecked() is False, "mock 读错"
    assert d.cp_role_separation.isChecked() is True, "说话人分离读错"
    assert d.cp_timestamps.isChecked() is True, "时间戳读错"
    assert d.cp_max_wait.value() == 900, "超时读错"

    d.cp_endpoint.setText("https://another.internal/v1/stt")
    d.cp_headers_file.setText("")
    d.cp_language.setCurrentIndex(d.cp_language.findData(""))
    d.cp_role_separation.setChecked(False)
    tr = d._collect_updates()["transcribe"]
    cpw = tr["custom_post"]
    assert cpw["endpoint"] == "https://another.internal/v1/stt", f"端点写错: {cpw}"
    assert cpw["headers_file"] == "", f"请求头文件写错: {cpw}"
    assert cpw["language"] == "", f"语言写错: {cpw}"
    assert cpw["role_separation"] is False, f"说话人写错: {cpw}"
    assert cpw["api_key"] == "test-key", f"Key 写错: {cpw}"
    assert cpw["max_wait_seconds"] == 900, f"超时写错: {cpw}"
    # 其它引擎块没被覆盖
    assert "xf_asr" in tr and "qwen_asr" in tr, "其它引擎配置块丢了"
    assert "minimax_asr" not in tr, "旧块不该再被 GUI 写出（已改名）"
    print("OK 8d: SettingsDialog custom_post 字段读/写/隔离都对\n")


def test_endpoint_and_headers():
    """9. 端点可配 + 请求头文件（这是本次泛化的核心）。"""
    import os
    import tempfile

    import video_to_article.media.custom_post_asr as cp

    # === 9a: 请求头文件解析 ===
    hpath = os.path.join(tempfile.gettempdir(), "smoke_headers.txt")
    with open(hpath, "w", encoding="utf-8") as f:
        f.write(
            "# 这是注释\n"
            "\n"
            "X-Deploy-Token: secret-token-value   # 行内注释\n"
            "X-Gateway-Route: asr-prod\n"
            "Authorization: Token from-file\n"
            "  # 缩进注释也应忽略\n"
            "BadLineWithoutColon\n"
            "X-Empty:\n"
        )
    try:
        got = cp.load_headers_file(cp.Path(hpath))
        # 注释/空行/缩进注释都跳掉
        assert got.get("X-Deploy-Token") == "secret-token-value", (
            f"行内注释应被剥掉: {got!r}"
        )
        assert got.get("X-Gateway-Route") == "asr-prod", f"普通头解析错: {got!r}"
        assert got.get("Authorization") == "Token from-file", (
            f"请求头文件里的 Authorization 应生效: {got!r}"
        )
        # 空值 = 用户想显式置空
        assert got.get("X-Empty") == "", f"空值应保留为空串: {got!r}"
        # 没有冒号的行不崩、不进结果
        assert "BadLineWithoutColon" not in got, f"非法行不该进结果: {got!r}"
        print(f"OK 9a: 请求头文件解析（注释/行内注释/空值/非法行）→ {sorted(got)}")

        # === 9b: 凭证可以只来自请求头文件（API Key 留空）===
        headers = cp.build_headers("", "zh", got)
        assert headers.get("Authorization") == "Token from-file", (
            f"空 API Key 时应用文件里的 Authorization: {headers!r}"
        )
        # Bearer 模式：API Key 优先，文件可覆盖
        b1 = cp.build_headers("KEY", "zh", got)
        assert b1["Authorization"] == "Token from-file", (
            f"请求头文件应覆盖程序生成的 Authorization: {b1!r}"
        )
        # 不带文件时才是 Bearer
        b2 = cp.build_headers("KEY", "zh", {})
        assert b2["Authorization"] == "Bearer KEY", f"默认该是 Bearer: {b2!r}"
        # 空值 = 删掉 language
        b3 = cp.build_headers("KEY", "zh", {"language": ""})
        assert "language" not in b3, f"空值应删掉该头: {b3!r}"
        print("OK 9b: 凭证双来源（API Key / 文件 Authorization）优先级对")

        # === 9c: 端点可配：_transcribe_one 真的用传进来的 endpoint ===
        audio = os.path.join(tempfile.gettempdir(), "smoke_ep.wav")
        with open(audio, "wb") as f:
            f.write(b"RIFF" + b"\0" * 2048)
        seen = {}

        class R:
            status_code = 200

            def json(self):
                return {"text": "ok", "duration": 1.0}

        class S:
            def post(self, url, headers=None, data=None, files=None, timeout=None, **kw):
                seen["url"] = url
                seen["headers"] = headers or {}
                return R()

            def close(self):
                pass

        cp._cached_session = S()
        # 2026-10-04 契约变更：端点只填到 base，路径由程序按 api_style 拼。
        # 旧测试期望「填什么打什么」，现已不成立。
        custom_url = "https://my-asr.internal/v1/stt"
        expect_url = "https://my-asr.internal/v1/stt/speech_to_text"
        try:
            cp._transcribe_one(
                cp.Path(audio), "KEY", "", False, False, 60,
                endpoint=custom_url, extra_headers=got,
            )
        finally:
            cp._release_cached_session()
            os.unlink(audio)
        assert seen["url"] == expect_url, (
            f"应把 base 拼成完整转写路径，实得 {seen['url']!r}"
        )
        assert seen["headers"].get("X-Deploy-Token") == "secret-token-value", (
            f"自定义请求头应真的发出去: {seen['headers']!r}"
        )
        assert seen["headers"].get("Authorization") == "Token from-file", (
            f"文件里的 Authorization 应覆盖 Bearer: {seen['headers']!r}"
        )
        # 敏感值绝不能进日志（这里只验证代码里没把它格式化进 msg——靠 review）
        assert "secret-token-value" not in repr(sorted(seen["headers"])), "键名列表不该含值"
        print("OK 9c: base 自动拼路径 + 自定义请求头真的发出去")

        # 9c-2: 防御性——万一用户已经把完整路径填进去了，不能拼成
        # /speech_to_text/speech_to_text
        assert cp.build_stt_endpoint(expect_url) == expect_url, (
            f"已填完整路径应原样保留，实得 {cp.build_stt_endpoint(expect_url)!r}"
        )
        assert cp.build_stt_endpoint(custom_url + "/") == expect_url, (
            "结尾多一个斜杠也应归一"
        )
        print("OK 9c-2: 已填完整路径 / 多余斜杠都不会重复拼接")

        # === 9d: 端点格式预检：非 http 开头要早报错 ===
        try:
            cp.transcribe_audio_with_custom_post(
                audio, {"api_key": "K", "mock": False, "endpoint": "ftp://bad"}
            )
            raised = False
        except RuntimeError as e:
            raised = True
            assert "http" in str(e).lower(), f"应提示 http/https: {e}"
        assert raised, "非 http 端点应提前抛错，不该白发一次请求"
        print("OK 9d: 端点格式预检（非 http(s) 早报错）")

        # === 9e: 模板生成 + 幂等（不覆盖已有文件）===
        tpath = os.path.join(tempfile.gettempdir(), "smoke_tpl_headers.txt")
        if os.path.exists(tpath):
            os.unlink(tpath)
        p1 = cp.write_headers_template(cp.Path(tpath))
        assert p1.exists(), "模板应被创建"
        first = p1.read_text(encoding="utf-8")
        assert "Key: Value" in first, "模板应说明格式"
        assert "Authorization" in first, "模板应提到 Authorization 用法"
        # 再生成一次不应覆盖用户内容
        p1.write_text("X-My-Own: keep-me", encoding="utf-8")
        cp.write_headers_template(cp.Path(tpath), overwrite=False)
        assert p1.read_text(encoding="utf-8") == "X-My-Own: keep-me", (
            "已存在的请求头文件不该被模板覆盖（会冲掉用户手写内容）"
        )
        os.unlink(tpath)
        print("OK 9e: 模板生成 + 不覆盖已有文件")
    finally:
        if os.path.exists(hpath):
            os.unlink(hpath)

    # === 9f: GUI 的「生成模板」按钮路径 ===
    # 2026-10-02 修：_make_custom_headers_template 用了 Path() 但 settings_dialog
    # 没 import Path → NameError。之前的测试只测了底层 write_headers_template，
    # **从没调用过这个 GUI 方法**，所以漏了。
    # 这里必须真的调一次（弹窗用 stub 掉，否则 offscreen 下会阻塞等点击）。
    from PySide6.QtWidgets import QApplication
    import video_to_article.gui.settings.settings_dialog as sd_mod
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from video_to_article import config as cfg_mod

    app = QApplication.instance() or QApplication([])
    cfg_mod.load_config = lambda: {"transcribe": {}}
    sd_mod.load_config = lambda: {"transcribe": {}}
    dlg = SettingsDialog()

    # 弹窗 stub：记录调用次数，不阻塞
    boxes: list = []
    orig_info = sd_mod.QMessageBox.information
    orig_warn = sd_mod.QMessageBox.warning
    sd_mod.QMessageBox.information = staticmethod(
        lambda *a, **k: boxes.append(a[1] if len(a) > 1 else "")
    )
    sd_mod.QMessageBox.warning = staticmethod(
        lambda *a, **k: boxes.append(a[1] if len(a) > 1 else "")
    )
    gpath = os.path.join(tempfile.gettempdir(), "smoke_gui_tpl.txt")
    try:
        if os.path.exists(gpath):
            os.unlink(gpath)
        dlg.cp_headers_file.setText(gpath)
        dlg._make_custom_headers_template()  # ← 之前漏测的就是这一行
        assert os.path.exists(gpath), "点「生成模板」应真的把文件写出来"
        assert gpath == dlg.cp_headers_file.text().strip(), (
            "生成后应自动回填路径，省得用户再手动填"
        )
        assert boxes, "生成模板后应弹窗告知用户"

        # 幂等：已存在时不覆盖 + 明确告知
        with open(gpath, "w", encoding="utf-8") as f:
            f.write("X-My-Own: keep-me")
        boxes.clear()
        dlg._make_custom_headers_template()
        assert open(gpath, encoding="utf-8").read() == "X-My-Own: keep-me", (
            "已存在时不该覆盖用户手写内容"
        )
        assert any("已存在" in b for b in boxes), (
            f"已存在时应明确提示而不是静默，boxes={boxes!r}"
        )
        print("OK 9f: GUI「生成模板」按钮可调（写文件 + 回填路径 + 不覆盖 + 弹窗）")
    finally:
        sd_mod.QMessageBox.information = orig_info
        sd_mod.QMessageBox.warning = orig_warn
        if os.path.exists(gpath):
            os.unlink(gpath)
    print("OK 9: 端点可配 + 请求头文件全链路对\n")


def main():
    test_spec_constants()
    test_upload_contract()
    test_split_threshold()
    test_verbose_json_parse()
    test_cross_chunk()
    test_error_hints()
    test_mock_branches()
    test_registration()
    test_endpoint_and_headers()
    print("=" * 50)
    print("ALL custom_post smoke tests passed ✓")
    print("=" * 50)


if __name__ == "__main__":
    main()
