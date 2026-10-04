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


def main():
    test_endpoint_building()
    test_submit_body()
    test_submit_branches()
    test_poll()
    test_normalize()
    test_constants()
    test_image_host_guard()
    print()
    print("smoke_dashscope_asr: 全部通过 ✓")


if __name__ == "__main__":
    main()
