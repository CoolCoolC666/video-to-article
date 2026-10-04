"""DashScope 异步 ASR 协议冒烟测试（2026-10-04 新增 api_style 风格时建立）。

覆盖用户最容易踩的四类坑：
  1. URL 该填到哪一层（拼路径 / 不重复拼）
  2. 提交体字段名对不对（毫秒、URL 编码、parameters 条件分支）
  3. 轮询状态机（整体成功但子任务失败 / 内联结果）
  4. 结果归一（官方 transcripts[].sentences[] schema）

跑法：.venv\\Scripts\\python.exe tests\\smoke_dashscope_asr.py
全程 mock 网络，不产生任何费用。
"""
import os
import sys
import pathlib
import tempfile

sys.path.insert(0, "src")
import video_to_article.media.custom_post_asr as cp
from video_to_article.media.custom_post_asr import (
    API_STYLES,
    _dashscope_headers,
    _dashscope_poll,
    _dashscope_submit,
    _format_segments,
    _normalize_dashscope_result,
    build_base,
    build_stt_endpoint,
    build_task_query_url,
)

BASE = "https://maas.qianwenaiapi.com/api/v1"


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """记录每次调用，按脚本返回预设响应。"""

    def __init__(self, get_script=None, post_script=None):
        self.get_script = list(get_script or [])
        self.post_script = list(post_script or [])
        self.gets = []
        self.posts = []

    def get(self, url, headers=None, timeout=None, **kw):
        self.gets.append({"url": url, "headers": headers or {}})
        return self.get_script.pop(0) if self.get_script else FakeResp()

    def post(self, url, headers=None, data=None, files=None, json=None,
              timeout=None, **kw):
        self.posts.append({
            "url": url, "headers": headers or {},
            "data": data, "files": files, "json": json,
        })
        return self.post_script.pop(0) if self.post_script else FakeResp()

    def close(self):
        pass


def use(session):
    cp._cached_session = session
    return session


def done():
    cp._release_cached_session()


# ---------------------------------------------------------------- 1. 端点拼接
def test_endpoint_building():
    cases = [
        BASE,
        BASE + "/",                                # 多余尾斜杠
        "https://api.minimax.cn/v1",                # OpenAI 兼容
        "https://api.minimax.cn/v1/speech_to_text",  # 用户已填完整路径
        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
    ]
    for raw in cases:
        o = build_stt_endpoint(raw, "openai_compat")
        d = build_stt_endpoint(raw, "dashscope_async")
        assert o.endswith("/speech_to_text"), f"{raw} -> {o}"
        assert d.endswith("/services/audio/asr/transcription"), f"{raw} -> {d}"
        assert not o.endswith("/speech_to_text/speech_to_text"), f"重复拼: {o}"
        assert not d.endswith("transcription/transcription"), f"重复拼: {d}"
    assert build_stt_endpoint("", "openai_compat") == ""
    assert build_stt_endpoint("", "dashscope_async") == ""
    assert build_task_query_url(BASE, "abc-123") == f"{BASE}/tasks/abc-123"
    assert build_task_query_url("", "abc-123") == ""
    # build_base 只做归一，不拼路径
    assert build_base("https://x.com/v1/speech_to_text") == "https://x.com/v1"
    print("OK 1: 端点拼接（尾斜杠 / 已填完整路径 / 空地址都归一，且不重复拼）")


# ---------------------------------------------------------------- 2. 提交体
def test_submit_body():
    s = use(FakeSession(post_script=[
        FakeResp(200, {"output": {"task_id": "task-999", "task_status": "PENDING"}}),
    ]))
    try:
        task_id = _dashscope_submit(
            BASE, "KEY", "paraformer-v2",
            "https://img.example.com/1-课件/a b.mp3",  # 含中文 + 空格
            "zh", role_separation=True, speaker_count=3, colloquial_proc=True,
        )
    finally:
        done()
    assert task_id == "task-999", task_id

    req = s.posts[0]
    assert req["url"] == f"{BASE}/services/audio/asr/transcription", req["url"]
    # 关键：DashScope 只收 JSON，不收 multipart
    assert req["json"] is not None and req["files"] is None, "应发 JSON body"
    assert req["data"] is None, "不该同时发 data"
    body = req["json"]
    assert body["model"] == "paraformer-v2"
    assert body["input"]["file_urls"][0] == (
        "https://img.example.com/1-%E8%AF%BE%E4%BB%B6/a%20b.mp3"
    ), f"URL 未编码: {body['input']['file_urls'][0]}"
    p = body["parameters"]
    assert p["channel_id"] == [0], p
    assert p["diarization_enabled"] is True, p
    assert p["speaker_count"] == 3, p
    assert p["disfluency_removal_enabled"] is True, p
    assert p["language_hints"] == ["zh"], p
    print("OK 2a: 提交体字段 + 中文/空格 URL 编码（没编码会 InvalidFile.DownloadFailed）")

    # 请求头：Authorization + 异步开关
    h = req["headers"]
    assert h["Authorization"] == "Bearer KEY", h
    assert h["X-DashScope-Async"] == "enable", h

    # 额外请求头应覆盖同名（不覆盖 Authorization，除非文件里显式给了）
    h2 = _dashscope_headers("KEY", {"X-Tok": "abc", "X-Empty": ""})
    assert h2["X-Tok"] == "abc" and "X-Empty" not in h2, h2
    print("OK 2b: 请求头（Bearer + X-DashScope-Async + 自定义头，空值不透传）")


def test_submit_branches():
    # 不开任何开关时只带 channel_id
    s = use(FakeSession(post_script=[
        FakeResp(200, {"output": {"task_id": "t1"}}),
        FakeResp(200, {"output": {"task_id": "t2"}}),
        FakeResp(200, {"output": {"task_id": "t3"}}),
    ]))
    try:
        _dashscope_submit(BASE, "K", "qwen3-asr-flash-filetrans",
                           "https://x.com/a.mp3", "zh", role_separation=False)
        p1 = s.posts[0]["json"]["parameters"]
        # language_hints 只 paraformer 支持，传给 qwen3 会 400
        _dashscope_submit(BASE, "K", "qwen3-asr-flash-filetrans",
                          "https://x.com/a.mp3", "zh", role_separation=True,
                          speaker_count=999)
        p2 = s.posts[1]["json"]["parameters"]
        _dashscope_submit(BASE, "K", "paraformer-v2", "https://x.com/a.mp3", "")
        p3 = s.posts[2]["json"]["parameters"]
    finally:
        done()
    assert p1 == {"channel_id": [0]}, p1
    assert "language_hints" not in p1, "非 paraformer 不该带 language_hints"
    assert "speaker_count" not in p2, f"越界值不该下发: {p2}"
    assert p2["diarization_enabled"] is True
    assert "language_hints" not in p3, "未指定语言就不该带"
    print("OK 2c: parameters 条件分支（language_hints 只 paraformer / 人数越界不下发）")

    # 缺模型名要在发请求前就报错
    try:
        _dashscope_submit(BASE, "K", "", "https://x.com/a.mp3", "")
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "模型名" in str(e), str(e)
    print("OK 2d: 缺模型名提前报错（DashScope 必填）")

    # 缺 task_id 也要报错
    s = use(FakeSession(post_script=[FakeResp(200, {"output": {}})]))
    try:
        _dashscope_submit(BASE, "K", "paraformer-v2", "https://x.com/a.mp3", "")
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "task_id" in str(e), str(e)
    finally:
        done()
    print("OK 2e: 200 但没给 task_id 也算失败")


# ---------------------------------------------------------------- 3. 轮询
def test_poll():
    # 正常：PENDING → SUCCEEDED + transcription_url
    s = use(FakeSession(get_script=[
        FakeResp(200, {"output": {"task_status": "PENDING"}}),
        FakeResp(200, {"output": {
            "task_status": "SUCCEEDED",
            "results": [{"subtask_status": "SUCCEEDED",
                         "transcription_url": "https://dashscope.oss/r.json"}],
        }}),
    ]))
    try:
        link = _dashscope_poll(BASE, "K", "t1", 60)
    finally:
        done()
    assert link == "https://dashscope.oss/r.json", link
    assert s.gets[0]["url"] == f"{BASE}/tasks/t1", s.gets[0]["url"]
    assert s.gets[0]["headers"]["X-DashScope-Async"] == "enable"
    print("OK 3a: 轮询用 GET + 拿 transcription_url")

    # ⚠ 整体成功但子任务失败 —— 官方明确会这样，不查就会拿着不存在的 URL 去下载
    s = use(FakeSession(get_script=[
        FakeResp(200, {"output": {
            "task_status": "SUCCEEDED",
            "results": [{"subtask_status": "FAILED",
                         "code": "InvalidFile.DownloadFailed",
                         "message": "download failed"}],
        }}),
    ]))
    try:
        _dashscope_poll(BASE, "K", "t1", 60)
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "子任务失败" in str(e) and "DownloadFailed" in str(e), str(e)
        assert "中文" in str(e), "应给出 URL 编码/图床外网的排查提示"
    finally:
        done()
    print("OK 3b: 整体 SUCCEEDED 但子任务 FAILED 会被拦住（否则下载一个不存在的 URL）")

    # 整体失败
    s = use(FakeSession(get_script=[
        FakeResp(200, {"output": {"task_status": "FAILED", "message": "boom"}}),
    ]))
    try:
        _dashscope_poll(BASE, "K", "t1", 60)
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "任务失败" in str(e), str(e)
    finally:
        done()
    print("OK 3c: 任务整体 FAILED 报错")

    # 内联结果（没有 transcription_url 时的兜底）
    s = use(FakeSession(get_script=[
        FakeResp(200, {"output": {
            "task_status": "SUCCEEDED",
            "results": [{"subtask_status": "SUCCEEDED"}],
            "transcription": {"transcripts": [{"text": "内联"}]},
        }}),
    ]))
    try:
        link = _dashscope_poll(BASE, "K", "t1", 60)
    finally:
        done()
    assert link.startswith("inline:"), link
    print("OK 3d: 无 URL 但有内联结果时走 inline: 兜底")

    # 成功但两者都没有 —— 必须报错，不能返回空串让下游崩
    s = use(FakeSession(get_script=[
        FakeResp(200, {"output": {"task_status": "SUCCEEDED", "results": []}}),
    ]))
    try:
        _dashscope_poll(BASE, "K", "t1", 60)
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "既没有" in str(e), str(e)
    finally:
        done()
    print("OK 3e: 声称成功却无任何结果时明确报错")

    # 超时
    s = use(FakeSession(get_script=[
        FakeResp(200, {"output": {"task_status": "RUNNING"}}) for _ in range(40)
    ]))
    old = cp.DASHSCOPE_POLL_INTERVAL
    cp.DASHSCOPE_POLL_INTERVAL = 0
    try:
        _dashscope_poll(BASE, "K", "t1", 0)
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "超时" in str(e), str(e)
    finally:
        cp.DASHSCOPE_POLL_INTERVAL = old
        done()
    print("OK 3f: 轮询超时报错（带 task_id 便于排查）")

    # HTTP 错误
    s = use(FakeSession(get_script=[FakeResp(401, {"message": "bad key"})]))
    try:
        _dashscope_poll(BASE, "K", "t1", 60)
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "401" in str(e), str(e)
    finally:
        done()
    print("OK 3g: 轮询 HTTP 错误转成中文提示")


# ---------------------------------------------------------------- 4. 结果归一
def test_normalize():
    # 官方「识别结果说明」示例 schema
    official = {
        "file_url": "https://example.com/a.mp3",
        "properties": {"audio_format": "mp3", "channels": [0],
                       "original_sampling_rate": 44100,
                       "original_duration_in_milliseconds": 3834},
        "transcripts": [{
            "channel_id": 0,
            "content_duration_in_milliseconds": 3720,
            "text": "Hello world, 这里是阿里巴巴语音实验室。",
            "sentences": [
                {"begin_time": 100, "end_time": 3820,
                 "text": "Hello world, 这里是阿里巴巴语音实验室。",
                 "sentence_id": 1, "speaker_id": 0},
            ],
        }],
    }
    r = _normalize_dashscope_result(official)
    assert r["segments"][0]["start"] == 0.1, f"毫秒应转秒: {r['segments'][0]}"
    assert r["segments"][0]["end"] == 3.82
    assert r["segments"][0]["speaker"] == "spk0"
    assert r["n_speakers"] == 1
    assert "阿里" in r["text"]
    print("OK 4a: 官方 transcripts[].sentences[] 归一 + 毫秒转秒")

    # 没开分离 → 无 speaker_id
    r2 = _normalize_dashscope_result({"transcripts": [{"text": "整段", "sentences": [
        {"begin_time": 0, "end_time": 2000, "text": "整段"}]}]})
    assert r2["n_speakers"] == 0, r2["n_speakers"]
    assert r2["segments"][0]["speaker"] == "", r2["segments"][0]
    print("OK 4b: 未开分离时 n_speakers=0 且 speaker 为空")

    # 兜底：顶层 sentences
    r3 = _normalize_dashscope_result({"sentences": [
        {"begin_time": 1200, "end_time": 3500, "text": "你好", "speaker_id": 0},
        {"begin_time": 4000, "end_time": 5500, "text": "在吗", "speaker_id": 1},
    ]})
    assert r3["segments"][0]["start"] == 1.2, f"毫秒应转秒: {r3['segments'][0]}"
    assert r3["n_speakers"] == 2, r3["n_speakers"]
    assert r3["text"] == "你好在吗", r3["text"]
    print("OK 4c: 顶层 sentences 兜底 + 多说话人")

    # 归一结果能直接喂格式化
    lines = _format_segments(r, role_separation=True, timestamps=True)
    assert lines[0] == "[00:00] 【spk0】Hello world, 这里是阿里巴巴语音实验室。", lines[0]
    plain = _format_segments(r, role_separation=False, timestamps=False)
    assert plain[0] == "Hello world, 这里是阿里巴巴语音实验室。", plain[0]
    print("OK 4d: 归一结果直接喂 _format_segments（分离/时间戳两路）")

    # 认不出结构
    try:
        _normalize_dashscope_result({"totally": "unknown"})
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "认不出" in str(e), str(e)
    print("OK 4e: 认不出结构时给可读错误（附顶层键名）")


# ---------------------------------------------------------------- 5. 常量
def test_constants():
    assert set(API_STYLES) == {"openai_compat", "dashscope_async"}, API_STYLES
    assert cp.DASHSCOPE_MAX_DURATION_SEC > cp.MAX_DURATION_SEC, (
        "DashScope 12h 上限应比 openai 兼容 500s 宽松得多"
    )
    assert cp.DASHSCOPE_MAX_BYTES > cp.MAX_FILE_BYTES
    # 两条路径的常量互不串味
    assert cp.MAX_DURATION_SEC == 500, cp.MAX_DURATION_SEC
    assert cp.CHUNK_SEGMENT_SEC < cp.MAX_DURATION_SEC, "切段应短于单请求上限"
    print(f"OK 5: 风格常量 + 分风格切段阈值 "
          f"(openai {cp.MAX_DURATION_SEC}s/{cp.MAX_FILE_BYTES // 1024 // 1024}MB, "
          f"dashscope {cp.DASHSCOPE_MAX_DURATION_SEC // 3600}h/"
          f"{cp.DASHSCOPE_MAX_BYTES // 1024 // 1024 // 1024}GB)")


# ---------------------------------------------------------------- 6. 图床
def test_image_host_guard():
    p = os.path.join(tempfile.gettempdir(), "smoke_ds_audio.mp3")
    with open(p, "wb") as f:
        f.write(b"ID3" + b"\0" * 512)
    try:
        cp.upload_audio_for_public_url(cp.Path(p), {})
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "图床" in str(e), str(e)
    finally:
        os.unlink(p)
    print("OK 6: 没配图床时给可操作的三选一提示（DashScope 硬性要公网 URL）")


# ------------------------------------------------- 7. 占位符识别（实测踩到）
def test_placeholder_detection():
    # 本轮真实踩到：用户 config 里存着模板原样的中文占位域名，
    # 非空、能过所有「有没有配」检查，一跑就变成看不懂的 punycode DNS 错。
    placeholders = [
        "https://你的图床域名/api/index.php",      # ← 用户真实踩到的那个
        "https://example.com/api/index.php",
        "https://your-domain.com/up",
        "https://YOUR-DOMAIN.COM/up",
        "https://your-site.com/up",
        "https://placeholder.com/up",
        "https://mydomain.com/up",
        "  https://你的图床域名/api/index.php  ",   # 前后空格也认
        "", "   ", "not-a-url", "https://",
    ]
    for u in placeholders:
        assert cp._looks_like_placeholder_url(u), f"应判为占位: {u!r}"

    # 真地址不能被误杀 —— path 里含中文是合法的（用户课件名几乎都是中文）
    reals = [
        "https://img.example.cn/upload/1-课件/a.mp3",  # host 是中文 TLD? 否 -> .cn 正常
        "https://img.cdn.com/1-%E8%AF%BE%E4%BB%B6/a.mp3",
        "https://i0.wp.com/picui.cn/abc.jpg",
        "https://api.minimax.cn/v1",
        "https://maas.qianwenaiapi.com/api/v1",
        "https://127.0.0.1:8000/v1",
        "https://picui.cn/upload",
    ]
    for u in reals:
        assert not cp._looks_like_placeholder_url(u), f"不该误杀真地址: {u!r}"
    print("OK 7a: 占位符识别（中文占位域名 / example.com / your-* …）不误杀真地址")

    # 占位 URL 走上传时要给「是没填」而不是「DNS 坏了」的提示
    p = os.path.join(tempfile.gettempdir(), "smoke_ds_audio2.mp3")
    with open(p, "wb") as f:
        f.write(b"ID3" + b"\0" * 512)
    try:
        cp.upload_audio_for_public_url(
            cp.Path(p), {"api_url": "https://你的图床域名/api/index.php", "token": "x"}
        )
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        msg = str(e)
        assert "占位符" in msg, msg
        assert "没填" in msg or "没换成真实域名" in msg, msg
        assert "xn--" in msg, "应点明 punycode 才是根因"
        # ⚠ 关键：别把整串占位 URL 回显到错误里，否则用户又对着一堆乱码发呆
        assert "你的图床域名/api/index.php" not in msg, "不该回显整串占位 URL"
    finally:
        os.unlink(p)
    print("OK 7b: 占位图床地址报「是没填」+ 点明 punycode 根因（不再回显整串 URL）")


# --------------------------------------------- 8. audio_url 优先于图床（方案②）
def test_audio_url_skips_image_host():
    s = use(FakeSession(post_script=[
        FakeResp(200, {"output": {"task_id": "t-direct"}}),
    ]))
    try:
        # image_host_config 传垃圾也无所谓：显式给了 audio_url 就不该碰图床
        tid = cp._dashscope_transcribe(
            cp.Path("irrelevant.mp3"),
            base_url=BASE, api_key="K", model="paraformer-v2",
            language="", max_wait=60,
            audio_url="https://cdn.example.com/1-课件/a.mp3",
            image_host_config={"api_url": "https://你的图床域名/api/index.php"},
        )
    except Exception as e:
        # 提交成功后会因缺轮询响应而失败，这里只关心它没先去找图床
        msg = str(e)
        assert "占位符" not in msg, f"显式给了 audio_url 还去借图床: {msg}"
        assert "图床" not in msg, f"显式给了 audio_url 还去借图床: {msg}"
    finally:
        done()
    assert s.posts and s.posts[0]["url"].endswith("/transcription"), "应直接提交任务"
    assert s.posts[0]["json"]["input"]["file_urls"][0].endswith(
        "/1-%E8%AF%BE%E4%BB%B6/a.mp3"
    ), s.posts[0]["json"]["input"]["file_urls"][0]
    print("OK 8: 显式填 audio_url 时完全跳过图床（图床是垃圾配置也不影响）")


# ----------------------------------------- 9. S3 兼容对象存储（R2 / OSS）
def test_s3_object_key_is_ascii():
    """object key 绝不能含中文 —— 用户的课件名几乎全是中文。

    一旦进了公网 URL 就得靠 percent-encode 兜底，任何一层漏掉都是 404 /
    InvalidFile.DownloadFailed。所以 key 用时间戳+哈希，原名只做展示。
    """
    from video_to_article.cover import _s3_object_key

    class P:
        def __init__(self, name):
            self._s = name

        def __str__(self):
            return self._s

        @property
        def suffix(self):
            return self._s[self._s.rfind("."):] if "." in self._s else ""

        @property
        def name(self):
            return self._s

    for name in ("1.54美术论文的写作与评点.mp3", "第 1 课 - 色彩 (一).mp3", "a.mp3"):
        key = _s3_object_key(P(name))
        assert key.isascii(), f"key 含非 ASCII: {key!r} (原名 {name!r})"
        assert key.startswith("asr/")
        assert key.endswith(".mp3"), key
    # 同一路径两次生成应不同（时间戳或路径不同）—— 只验格式稳定即可
    assert _s3_object_key(P("x.mp3"), "custom/prefix").startswith("custom/prefix/")
    print("OK 9a: S3 object key 恒为纯 ASCII（中文文件名不会污染公网 URL）")


def test_s3_missing_config_guided():
    p = os.path.join(tempfile.gettempdir(), "smoke_ds_audio3.mp3")

    def _fresh():
        with open(p, "wb") as f:
            f.write(b"ID3" + b"\0" * 512)

    # r2 什么都没填 —— 要一次说清缺哪些，而不是逐个字段报
    _fresh()
    try:
        cp.upload_audio_for_public_url(cp.Path(p), {"provider": "r2"})
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        msg = str(e)
        for field in ("bucket", "endpoint", "access_key_id", "access_key_secret"):
            assert field in msg, f"应点名缺 {field}: {msg}"
        assert "r2.cloudflarestorage.com" in msg, "应直接给出 endpoint 写法"
        assert "oss-cn-" in msg, "应顺带说明 OSS 怎么写"
    print("OK 9b: R2/OSS 缺配置时一次列全缺失项 + 直接给出 endpoint 写法")

    # endpoint 是占位符
    _fresh()
    try:
        cp.upload_audio_for_public_url(cp.Path(p), {
            "provider": "oss", "bucket": "b", "endpoint": "https://你的OSS域名",
            "access_key_id": "k", "access_key_secret": "s",
        })
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        assert "占位符" in str(e), str(e)

    # 未知 provider
    _fresh()
    try:
        cp.upload_audio_for_public_url(cp.Path(p), {"provider": "dropbox"})
        raise SystemExit("应该抛错但没抛")
    except ValueError as e:
        assert "easyimage" in str(e) and "r2" in str(e) and "oss" in str(e), str(e)
    finally:
        if os.path.exists(p):
            os.unlink(p)
    print("OK 9c: endpoint 占位符 / 未知 provider 都被挡住并说清可选值")


def test_s3_dispatch_exists():
    """upload_image_to_host 要认 r2/oss，且 boto3 是懒加载的。"""
    from video_to_article import cover

    assert "r2" in cover.S3_PROVIDER_DEFAULTS
    assert "oss" in cover.S3_PROVIDER_DEFAULTS
    # R2 不认 ACL（传了会 AccessControlListNotSupportedError）
    assert cover.S3_PROVIDER_DEFAULTS["r2"]["region"] == "auto"
    assert cover.S3_PROVIDER_DEFAULTS["oss"]["region"] == ""
    # boto3/botocore 绝不能是顶层 import —— 否则没装 boto3 的人
    # 连 AI 封面都用不了（cover.py 是封面主模块）
    src = pathlib.Path(cover.__file__).read_text(encoding="utf-8")
    head = src.split("def ")[0]
    assert "import boto3" not in head, "boto3 不该在模块顶层 import"
    assert "from botocore" not in head, "botocore 不该在模块顶层 import"
    print("OK 9d: r2/oss 分发就位 + boto3 确认为懒加载（不拖累封面功能）")


# ------------------- 10. 图床 Tab 字段组互斥（用户实测卡住的地方）
def test_host_field_groups_mutually_exclusive():
    """r2/oss 时 easyimage 那组必须收起来，反之亦然。

    本轮用户把 endpoint 填进了「API URL」——因为两套字段平铺在一起，
    标签（API URL / Token）长得和 S3 的（endpoint / 密钥）很像但不是一回事。
    一次只显示该填的那组，才不会有人填错。
    """
    from video_to_article.gui.settings.settings_dialog import SettingsDialog
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    d = SettingsDialog()

    # ⚠ 用 isHidden() 而不是 isVisible()/isVisibleTo()：
    #   isVisible 要求**所有祖先**都可见，而这里对话框只构造没 show()；
    #   isVisibleTo(d) 同理依赖 d 自身 visible。用它们会得到「全都 False」，
    #   分不清「被代码收起来了」和「祖先没显示」。
    #   isHidden() 只反映 setVisible(False) 这个动作本身，正是要断言的东西。
    d.host_provider.setText("easyimage")
    assert d._host_s3_box.isHidden() is True, "easyimage 不该露出 S3 组"
    assert d._host_easy_box.isHidden() is False, "easyimage 应显示传统图床组"

    d.host_provider.setText("r2")
    assert d._host_s3_box.isHidden() is False, "r2 应显示 S3 组"
    assert d._host_easy_box.isHidden() is True, (
        "r2 时「API URL / Token」那组必须收起来（否则用户会填错地方）"
    )

    d.host_provider.setText("oss")
    assert d._host_s3_box.isHidden() is False, "oss 应显示 S3 组"
    assert d._host_easy_box.isHidden() is True, "oss 时也应收起 easyimage 组"

    # 大小写/空格要归一（用户手敲很容易带）
    for v in ("R2", " oss ", "r2"):
        d.host_provider.setText(v)
        assert d._host_s3_box.isHidden() is False, f"{v!r} 应识别为 S3"

    # hint 要跟着变，且要明确说「别填 API URL」
    d.host_provider.setText("r2")
    assert "API URL" in d._host_hint.text(), d._host_hint.text()
    d.host_provider.setText("easyimage")
    assert "easyimage" in d._host_hint.text(), d._host_hint.text()
    d.close()
    print("OK 10: 图床 Tab 两组字段互斥显示（r2/oss 收起 easyimage 组）+ hint 跟随")


# ------------------- 11. 密钥形态预检（R2 Token 三元组拿错）
def test_credential_shape_warning():
    """R2 的 API Token 是三元组，最容易把 Token value 当成 Access Key ID。

    服务端只回一句英文 `Credential access key has length 16, should be 32`，
    完全看不出是拿错字段。所以本地要提前用中文点明。
    ⚠ 关键：只**提示不阻断**——形态判据是启发式的，不能让合法密钥用不了。
    """
    from video_to_article.cover import _warn_if_credential_shape_off

    # 正确形态：不该有任何告警
    good_id = "a" * 32
    good_secret = "b" * 64
    # ❌ 断言「不抛异常」+ 不产生 warning 日志
    logs = []
    import video_to_article.cover as cover
    old = cover.logger.warning
    cover.logger.warning = lambda m: logs.append(str(m))
    try:
        _warn_if_credential_shape_off("r2", good_id, good_secret)
        assert not logs, f"正确密钥不该告警: {logs}"

        # 用户实测的那一形态：16 位 + 非 hex
        logs.clear()
        _warn_if_credential_shape_off("r2", "abcdef0123456789", "x" * 53)
        assert logs, "16 位密钥应告警"
        msg = logs[0]
        assert "Token value" in msg, msg
        assert "32" in msg and "64" in msg, msg
        # ⚠ 不得打印任何密钥内容
        assert "abcdef0123456789" not in msg, "告警里绝不能回显密钥"

        # 大小写 hex 也算合法
        logs.clear()
        _warn_if_credential_shape_off("r2", "A" * 32, "F" * 64)
        assert not logs, f"大写 hex 应视为合法: {logs}"

        # OSS 不做长度判据（AWS 兼容各家规格不一）
        logs.clear()
        _warn_if_credential_shape_off("oss", "short", "short")
        assert not logs, "OSS 不该按 R2 的长度判据告警"
    finally:
        cover.logger.warning = old
    print("OK 11: 密钥形态预检（正确密钥不打扰 / 拿错字段中文点明 / 不回显密钥 / OSS 不误报）")


# ------------------- 12. 同步模型误用于异步端点（用户实测卡住的地方）
def test_sync_only_model_rejected():
    """`qwen3-asr-flash` 和 `qwen3-asr-flash-filetrans` 只差一个后缀，
    但前者是**同步**模型（≤5 分钟），打异步端点会被服务端拒：
        current user api does not support asynchronous calls
    这句英文完全看不出原因，必须在本地拦下来并说清该用哪个。
    """
    # 必须拦下并给出替代名
    for bad, good in (
        ("qwen3-asr-flash", "qwen3-asr-flash-filetrans"),
        ("Qwen3-ASR-Flash", "qwen3-asr-flash-filetrans"),   # 大小写
        ("  qwen3-asr-flash  ", "qwen3-asr-flash-filetrans"),  # 空格
        ("qwen-audio-3.0-asr-flash", "qwen-audio-3.0-asr-flash-filetrans"),
        ("fun-asr-flash", "fun-asr"),
        ("paraformer-realtime-v2", "paraformer-v2"),
    ):
        s = use(FakeSession(post_script=[FakeResp(200, {"output": {"task_id": "t"}})]))
        try:
            _dashscope_submit(BASE, "K", bad, "https://x.com/a.mp3", "")
            raise SystemExit(f"{bad!r} 应该被拦下但没拦")
        except RuntimeError as e:
            msg = str(e)
            assert good in msg, f"应直接给出正确模型名 {good}: {msg}"
            assert "同步" in msg, msg
        finally:
            done()
        # ⚠ 关键：拦下时**不能已经发出请求**（否则照样被服务端拒、还白花钱）
        assert not s.posts, f"{bad!r} 应在发请求前就被拦下，却已发出 {len(s.posts)} 个请求"

    # 正确模型必须放行
    for ok in ("qwen3-asr-flash-filetrans", "qwen-audio-3.0-asr-flash-filetrans",
               "fun-asr", "paraformer-v2", "某个将来新增的异步模型"):
        s = use(FakeSession(post_script=[FakeResp(200, {"output": {"task_id": "t"}})]))
        try:
            _dashscope_submit(BASE, "K", ok, "https://x.com/a.mp3", "")
        finally:
            done()
        assert s.posts, f"{ok!r} 是合法异步模型，不该被拦"
    print("OK 12: 同步模型名（qwen3-asr-flash）发请求前拦下并指向 filetrans；异步模型放行")


# ------- 12b: 「不支持异步」报错的官方含义是**部署形态**，不只模型名
def test_dedicated_deployment_hint():
    """这句报错极易被一律当成「模型名填错」，但官方给的是部署形态的解释：

    「如果您调用独享部署的模型服务时收到报错
      current user api does not support asynchronous calls，
      表示该部署仅支持同步调用」

    端点形如 <自定义>.maas.aliyuncs.com / qianwenaiapi.com 的都是**独享部署**
    （公共端点是 dashscope.aliyuncs.com），异步能力由控制台决定，程序改不了。
    所以提示必须同时给出「换模型」和「去控制台确认部署」两条路，
    只说前者会让用户在换完模型后再次撞墙且更困惑。
    """
    from video_to_article.media.custom_post_asr import _raise_dashscope_submit_error

    resp = FakeResp(403, {
        "code": "InvalidApi",
        "message": "current user api does not support asynchronous calls",
    })
    try:
        _raise_dashscope_submit_error(resp, f"{BASE}/services/audio/asr/transcription",
                                      "qwen3-asr-flash-filetrans")
        raise SystemExit("应该抛错但没抛")
    except RuntimeError as e:
        msg = str(e)
        # 主因必须是模型名（实测：换对模型名后同一部署就能 200）
        assert "qwen-audio-3.0-asr-flash-filetrans" in msg, "应给出实测可用的模型名"
        assert "qwen3-asr-flash-filetrans" in msg, msg
        assert "qwen3-asr-flash-filetrans" in msg, "应回显当前模型名供核对"
        assert "绝大多数" in msg, "应说明主因是模型名，而非把部署问题摆在前面"
        # 部署形态作为次要可能保留（官方文档确实这么写）
        assert "独享部署" in msg, "官方文档提到的部署形态仍应保留"
        assert "换完仍报" in msg, "应说明何时才需要去查部署"
    print("OK 12b: 「不支持异步」提示以模型名为主因（附实测结论），部署形态作为次要可能")


def main():
    test_endpoint_building()
    test_submit_body()
    test_submit_branches()
    test_poll()
    test_normalize()
    test_constants()
    test_image_host_guard()
    test_placeholder_detection()
    test_audio_url_skips_image_host()
    test_s3_object_key_is_ascii()
    test_s3_missing_config_guided()
    test_s3_dispatch_exists()
    test_host_field_groups_mutually_exclusive()
    test_credential_shape_warning()
    test_sync_only_model_rejected()
    test_dedicated_deployment_hint()
    print()
    print("smoke_dashscope_asr: 全部通过 ✓")


if __name__ == "__main__":
    main()
