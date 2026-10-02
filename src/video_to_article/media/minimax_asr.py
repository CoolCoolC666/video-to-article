"""MiniMax 云端 ASR backend — Speech-to-Text REST API（2026-10-02 新增）。

MiniMax 开放平台: https://platform.minimax.cn/
接口文档: https://platform.minimax.cn/docs/api-reference/speech-to-text
OpenAPI: https://platform.minimax.cn/docs/api-reference/speech/speech-to-text/api/openapi.json

端点: POST https://api.minimax.cn/v1/speech_to_text
鉴权: Authorization: Bearer <API_key>（单件，不像讯飞要 APPID+SecretKey 两件套）

## 协议要点（照 OpenAPI spec 抄，不猜）

    Content-Type: multipart/form-data        ← 真正的 multipart！
    Authorization: Bearer <API_key>
    language: <BCP-47>                       ← 注意是 HTTP **header**，不是 form field

    form:
      model            = "asr-1.0"           （必填，当前只有这一个模型）
      file             = <binary>            （必填）
      response_format  = json | verbose_json | srt | vtt
      timestamp_level  = "" | sentence | word
      stream           = false

    ⚠ 与讯飞 lfasr 的协议正好相反：
      讯飞 → query string + raw body（**不能**用 multipart，否则 26600）
      MiniMax → 就是 multipart（form-data），跟官方 curl 示例一致
      写新引擎时别把讯飞那套 query string 习惯带过来。

## 硬限制（决定了本模块的核心设计：必须切段）

    时长 ≤ 500 秒   超了返回 400，不截断
    大小 ≤ 50 MB    超了返回 413
    格式  wav / aiff / flac / alac(m4a) / mp3 / aac / opus / ogg
    不支持无容器裸 PCM

    500 秒 ≈ 8.3 分钟。讯飞是 5 小时，MiniMax 差了两个数量级。
    所以本模块**必须**切段（见 _split_audio_for_long），跟 xf_asr 的
    「永不切段」策略完全相反——两个都是云端，但上限不同。
    （呼应 user memory：切段策略要按引擎的**真实上限**定，不是按「云端/本地」二分）

## 说话人分离 + 时间戳

    response_format=verbose_json 才返回 segments[] 和 n_speakers
    verbose_json / srt / vtt 会启用分离与时间戳对齐 → **不能**与 stream=true 同用

    {"text": "...", "duration": 12.744, "n_speakers": 2,
     "segments": [{"id": 0, "start": 0.1, "end": 1.66,
                   "speaker": "S1", "text": "..."}],
     "trace_id": "..."}

    ⚠ start/end 单位是**秒**（float），不是毫秒 —— 与讯飞相反。

## 错误响应（OpenAI 风格，HTTP 状态码即真实错误码）

    {"type": "error",
     "error": {"type": "bad_request_error",
               "message": "invalid params, audio duration 623.4s exceeds the limit of 500s (2013)",
               "http_code": "400"},
     "request_id": "..."}

    400 参数错 / 401 鉴权 / 402 余额不足 / 413 体积超 / 422 敏感内容 / 429 限流 / 500 服务端

## config.json 用法（transcribe 块加）

    "asr_engine": "minimax_asr",
    "minimax_asr": {
        "api_key": "your-api-key",
        "language": "zh",          # BCP-47；留空 = 自动检测 + 中英混说
        "mock": false,
        "role_separation": true,   # verbose_json（需配合 timestamps）
        "timestamps": true,
        "max_wait_seconds": 300
    }

2026-10-02 新增：minimax_asr.py
"""
from __future__ import annotations

import atexit
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ..logging_config import configure_logging
from ..text_utils import format_time

logger = configure_logging()


# MiniMax Speech-to-Text 端点
_STT_URL = "https://api.minimax.cn/v1/speech_to_text"
_MODEL = "asr-1.0"

# === 硬限制（OpenAPI spec 明确写出）===
MAX_DURATION_SEC = 500     # 时长上限，超了 400
MAX_FILE_BYTES = 50 * 1024 * 1024  # 大小上限，超了 413

# 切段策略：留 50 秒余量给 ffprobe 误差 + 服务端宽容度
# 绝不能卡着 500s 切 —— 探测值略偏就会 400
CHUNK_SEGMENT_SEC = 450

# 支持的音频格式（OpenAPI 列的）
SUPPORTED_EXTS = {".wav", ".aiff", ".aif", ".flac", ".alac", ".m4a",
                  ".mp3", ".aac", ".opus", ".ogg"}

# 官方支持的语言（BCP-47）；留空 = 自动检测 + 中英混说
MINIMAX_LANGUAGES = {
    "": "自动检测（含中英混说）",
    "zh": "中文（zh）",
    "yue": "粤语（yue）",
    "en": "英语（en）",
    "ja": "日语（ja）",
    "ko": "韩语（ko）",
    "th": "泰语（th）",
    "vi": "越南语（vi）",
    "id": "印尼语（id）",
    "ms": "马来语（ms）",
    "fil": "菲律宾语（fil）",
    "ar": "阿拉伯语（ar）",
    "tr": "土耳其语（tr）",
    "fr": "法语（fr）",
    "de": "德语（de）",
    "es": "西班牙语（es）",
    "it": "意大利语（it）",
    "pt": "葡萄牙语（pt）",
    "pl": "波兰语（pl）",
    "ru": "俄语（ru）",
    "uk": "乌克兰语（uk）",
}

# 错误码 → 人话（用于把 OpenAI 风格的 error.message 翻译成可操作提示）
_ERROR_HINTS = {
    400: "请求参数错误（最常见：音频超过 500 秒或格式不支持）",
    401: "鉴权失败：检查 API Key 是否正确（控制台 → 账户管理 → 接口密钥）",
    402: "账户余额不足：去 https://platform.minimax.cn/user-center/basic-information 充值",
    413: "音频超过 50 MB 上限：转成单声道 16kHz 或 mp3 压缩后再试",
    422: "音频内容涉及敏感内容，被平台拒绝",
    429: "触发限流：稍后重试",
    500: "MiniMax 服务端错误：稍后重试",
}

# ffmpeg/ffprobe 路径（与 xf_asr 同款，优先项目内副本）
_FFMPEG_PATHS = [
    r"E:\000~\YilanChengWen-src\ffmpeg\ffmpeg.exe",
    r"E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\runtime\ffmpeg.exe",
]
_FFPROBE_PATHS = [
    r"E:\000~\YilanChengWen-src\ffmpeg\ffprobe.exe",
    r"E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\runtime\ffprobe.exe",
]

_cached_session: Optional[object] = None


# ============ 工具：ffmpeg / ffprobe / 切段 / 清理 ============

def _find_ffmpeg() -> Optional[str]:
    for p in _FFMPEG_PATHS:
        if Path(p).exists():
            return p
    from shutil import which
    return which("ffmpeg")


def _find_ffprobe() -> Optional[str]:
    for p in _FFPROBE_PATHS:
        if Path(p).exists():
            return p
    from shutil import which
    return which("ffprobe")


def _get_session():
    global _cached_session
    if _cached_session is None:
        import requests

        _cached_session = requests.Session()
    return _cached_session


def _release_cached_session() -> None:
    global _cached_session
    if _cached_session is not None:
        try:
            _cached_session.close()
        except Exception:
            pass
        _cached_session = None


def _cleanup_stale_chunk_dirs() -> None:
    """进程启动时清残留切段目录（GUI 强杀 / 崩溃场景）。"""
    pattern = os.path.join(tempfile.gettempdir(), "minimax_asr_chunks_*")
    for d in glob.glob(pattern):
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_stale_chunk_dirs)


def _probe_audio_duration(audio_path: Path) -> float:
    """ffprobe 探测时长（秒）。失败返回 -1。"""
    ffprobe = _find_ffprobe()
    if not ffprobe:
        logger.warning("ffprobe 找不到（无法判断是否需要切段）")
        return -1.0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"ffprobe 失败 (code={result.returncode})")
            return -1.0
        return float(json.loads(result.stdout)["format"]["duration"])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning(f"ffprobe 异常: {e}")
        return -1.0


def _do_split(audio_path: Path, segment_sec: int) -> List[Path]:
    """ffmpeg 切段到 tempdir，返回所有段路径。"""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg 找不到，无法切段（超过 500 秒会被 MiniMax 拒绝）")
        return []
    temp_dir = Path(tempfile.mkdtemp(prefix="minimax_asr_chunks_"))
    chunk_paths: List[Path] = []
    i = 0
    while True:
        out_path = temp_dir / f"chunk_{i:06d}.mp3"
        # 转成单声道 16kHz mp3：既压体积（50MB 上限）又压时长余量
        # 官方明说「识别不依赖高采样率与立体声，用 mp3 精度不受影响」
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(audio_path),
            "-ss", str(i), "-t", str(segment_sec),
            "-ar", "16000", "-ac", "1",
            "-b:a", "32k",
            str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                logger.warning(f"ffmpeg 切段失败 (chunk {i}): {result.stderr[:200]}")
                break
        except subprocess.TimeoutExpired:
            logger.warning(f"ffmpeg 切段超时 (chunk {i})")
            break
        if not out_path.exists() or out_path.stat().st_size < 1000:
            break
        chunk_paths.append(out_path)
        i += segment_sec
    return chunk_paths


def _cleanup_dir(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _split_audio_for_long(audio_path: str, max_duration: float) -> Tuple[List[Path], bool]:
    """长音频切段策略 —— 与 xf_asr 相反，这里**必须**切。

    MiniMax 单请求上限 500 秒 / 50 MB，讯飞是 5 小时 / 500 MB。
    所以：
      - 时长 ≤ 500s 且体积 ≤ 50MB → 单文件直传
      - 超任一上限 → ffmpeg 切 450 秒一段，转单声道 16kHz mp3 压缩

    Returns:
        (段路径列表, 是否切了段)
    """
    audio_path_obj = Path(audio_path)
    duration = max_duration if max_duration > 0 else _probe_audio_duration(audio_path_obj)
    size = audio_path_obj.stat().st_size

    over_duration = duration > 0 and duration > MAX_DURATION_SEC
    over_size = size > MAX_FILE_BYTES
    if not over_duration and not over_size:
        if duration > 0:
            logger.info(
                f"音频 {duration:.1f}s / {size / 1024 / 1024:.2f}MB —— "
                f"在 MiniMax 单请求上限内（{MAX_DURATION_SEC}s / 50MB），整段直传"
            )
        return [audio_path_obj], False

    reason = []
    if over_duration:
        reason.append(f"时长 {duration:.1f}s > {MAX_DURATION_SEC}s")
    if over_size:
        reason.append(f"体积 {size / 1024 / 1024:.1f}MB > 50MB")
    logger.info(
        f"超过 MiniMax 单请求上限（{'、'.join(reason)}），"
        f"自动切 {CHUNK_SEGMENT_SEC} 秒一段并压成单声道 16kHz mp3"
    )
    chunks = _do_split(audio_path_obj, CHUNK_SEGMENT_SEC)
    if not chunks:
        raise RuntimeError(
            f"音频超过 MiniMax 上限（{'、'.join(reason)}）但自动切段失败。"
            f"请检查 ffmpeg 是否可用（程序目录 ffmpeg\\ffmpeg.exe 或 PATH）。"
        )
    logger.info(f"已切 {len(chunks)} 段到 {chunks[0].parent}")
    return chunks, True


# ============ 鉴权 / 参数校验 ============

def _validate_credentials(api_key: str) -> None:
    if not api_key:
        raise RuntimeError(
            "MiniMax ASR 需要 API Key。请到 "
            "https://platform.minimax.cn/user-center/basic-information/interface-key 查看，"
            "填入「设置 → 转写 → MiniMax 高级」。"
            "或勾「Mock 模式」跳过真实 API（用于本地调试）。"
        )


# ============ API 调用 ============

def _extract_error(resp) -> str:
    """把 OpenAI 风格错误体翻译成可读的中文提示。"""
    hint = _ERROR_HINTS.get(resp.status_code, "")
    detail = ""
    try:
        body = resp.json()
        err = body.get("error") or {}
        detail = str(err.get("message") or body.get("message") or "")
        req_id = body.get("request_id") or ""
    except Exception:
        detail = (resp.text or "")[:300]
        req_id = ""
    parts = [f"MiniMax STT HTTP {resp.status_code}"]
    if hint:
        parts.append(hint)
    if detail:
        parts.append(f"服务端信息: {detail}")
    if req_id:
        parts.append(f"request_id={req_id}")
    return " | ".join(parts)


def _transcribe_one(
    audio_path: Path,
    api_key: str,
    language: str,
    role_separation: bool,
    timestamps: bool,
    max_wait: int,
) -> dict:
    """单文件真实 API 调用，返回解析后的 JSON dict。

    走真 multipart（与讯飞相反）。
    """
    import requests  # noqa: F401  (确保 requests 存在，延迟 import 友好)

    session = _get_session()
    # verbose_json 同时给 segments[] + n_speakers；只要开分离或时间戳就必须用它
    need_verbose = bool(role_separation or timestamps)
    response_format = "verbose_json" if need_verbose else "json"

    headers = {"Authorization": f"Bearer {api_key}"}
    # language 是 HTTP header（不是 form field）—— 空值 = 自动检测 + 中英混说
    if language:
        headers["language"] = language

    data = {
        "model": _MODEL,
        "response_format": response_format,
        "stream": "false",
    }
    if need_verbose:
        # sentence = 句段时间戳（默认粒度）；word = 中文按字/英文按词
        data["timestamp_level"] = "sentence"

    size_mb = audio_path.stat().st_size / 1024 / 1024
    logger.info(
        f"上传到 MiniMax: {audio_path.name} ({size_mb:.2f}MB, "
        f"format={response_format}, language={language or 'auto'})"
    )

    with open(audio_path, "rb") as f:
        files = {"file": (audio_path.name, f, "application/octet-stream")}
        resp = session.post(_STT_URL, headers=headers, data=data, files=files,
                            timeout=max(30, max_wait))
    if resp.status_code != 200:
        raise RuntimeError(_extract_error(resp))
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"MiniMax 返回非 JSON (HTTP {resp.status_code}): {(resp.text or '')[:300]}"
        ) from exc


# ============ 响应解析 ============

def _format_ts(sec: float) -> str:
    """秒 → [MM:SS]（超 1 小时带小时位）。与 xf_asr / funasr 格式一致。"""
    if sec is None or sec < 0:
        return "00:00"
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_segments(
    result: dict,
    role_separation: bool,
    timestamps: bool,
    time_offset: float = 0.0,
    speaker_offset: Optional[dict] = None,
) -> List[str]:
    """把 verbose_json 的 segments[] 格式化成行。

    Args:
        time_offset:     段起始时间偏移（秒）—— 切段后每段 start 都从 0 重新计
        speaker_offset:  跨段说话人重映射表 {本段 S1: 全局 S1, ...}。
                         MiniMax 每个独立请求都会重新编号 S1/S2，
                         切段拼接时必须映射，否则第二段又冒出个"说话人1"。
                         传入 None 表示不重映射（单文件时用不到）。
    """
    segments = result.get("segments")
    if not isinstance(segments, list) or not segments:
        return []

    lines: List[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        prefix = ""
        if timestamps:
            try:
                start = float(seg.get("start") or 0.0) + time_offset
            except (TypeError, ValueError):
                start = time_offset
            if start > 0:
                prefix += f"[{_format_ts(start)}] "
        if role_separation:
            spk = str(seg.get("speaker") or "")
            if spk:
                if speaker_offset is not None:
                    spk = speaker_offset.get(spk, spk)
                prefix += f"【{spk}】"
        lines.append(f"{prefix}{text}" if prefix else text)
    return lines


def _build_speaker_map(
    prev_map: dict,
    chunk_speakers: List[str],
) -> Tuple[dict, dict]:
    """跨段说话人编号映射（诚实版：多数情况下是恒等映射）。

    **必须先说清楚这个能力的天花板**：MiniMax 每个独立请求都从 S1 重新编号，
    响应里**没有任何跨段身份信息**。所以：

      - 段间人数相同时 → 恒等映射（S1→S1、S2→S2），程序做不了更多
      - 某段人数变多时 → 新出现的编号分配下一个未占用的全局编号（如 S3）
      - 段间人数变少时 → 复用已有编号，不回收

    **不能保证的事**：如果两个人音色接近、在不同段落里的编号互换了
    （第一段 S1 是老师、第二段 S1 是学生），程序无从判断，会认错。
    想要严格一致的跨段说话人身份，只能用单文件长音频的引擎
    （讯飞 lfasr 支持 5 小时单文件）或自己接 pyannote 之类的带全局聚类的方案。

    保留这个函数的价值：让「后一段出现第 3 个人」时不至于撞号，
    并且提供一个将来换成真正全局聚类时的挂载点。
    """
    global_map = dict(prev_map)
    used = set(global_map.values())
    for spk in chunk_speakers:
        if spk in global_map:
            continue
        idx = 1
        while f"S{idx}" in used:
            idx += 1
        global_map[spk] = f"S{idx}"
        used.add(f"S{idx}")
    return global_map, global_map


# ============ 主入口 ============

def transcribe_audio_with_minimax_asr(audio_path: str, config: dict) -> str:
    """MiniMax 云端 ASR 主入口。

    Args:
        audio_path: 音频文件路径
        config:     config.json 中 transcribe.minimax_asr 块

    Returns:
        转写文本

    Raises:
        RuntimeError: 凭证缺失 / API 调用失败 / 切段失败

    Mock 行为（与 xf_asr 一致的三分支日志）：
        1. mock=true + 凭证齐全 → WARNING（可能忘了取消勾选）
        2. mock=true + 凭证缺失 → INFO
        3. mock=false + 凭证缺失 → WARNING + 自动降级 mock

    切段行为（与 xf_asr **相反**）：
        MiniMax 单请求 ≤500 秒 / 50 MB，超了必须切；讯飞是 5 小时不用切。
    """
    cfg = config or {}
    mock = bool(cfg.get("mock", False))
    api_key = (cfg.get("api_key") or "").strip()
    language = (cfg.get("language") or "").strip()
    role_separation = bool(cfg.get("role_separation", False))
    timestamps = bool(cfg.get("timestamps", True))
    max_wait = int(cfg.get("max_wait_seconds") or 300)

    if mock or not api_key:
        if mock and api_key:
            logger.warning(
                "minimax_asr 凭证已配置（API Key）但 Mock 模式仍勾选——"
                "将走 Mock 不调真实 API。如需真实 API，请取消 Mock 勾选。"
            )
        elif mock:
            logger.info("minimax_asr Mock 模式（显式勾选）—— 不调真实 API。")
        else:
            logger.warning(
                "minimax_asr 凭证缺失，自动降级 Mock 模式（不调网络、不需要 key）。"
                "如需真实 API，请配置 api_key 并取消 mock 勾选。"
            )
        return _mock_transcribe(audio_path)

    _validate_credentials(api_key)
    audio_path_obj = Path(audio_path)

    # 格式预检：裸 PCM 会被 400 拒，MP4/MKV 这类容器也不支持
    ext = audio_path_obj.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        logger.warning(
            f"文件后缀 {ext} 不在 MiniMax 支持列表（{'/'.join(sorted(SUPPORTED_EXTS))}）——"
            "若报 400 请先转成 mp3/wav（程序下载音轨时默认就是 mp3）"
        )

    start = time.time()
    duration = _probe_audio_duration(audio_path_obj)
    chunk_paths, is_chunked = _split_audio_for_long(audio_path, duration)
    temp_dir = chunk_paths[0].parent if is_chunked else None

    try:
        if not is_chunked:
            result = _transcribe_one(
                audio_path_obj, api_key, language, role_separation, timestamps, max_wait
            )
            text = _assemble(
                [result], role_separation, timestamps, chunk_durations=[duration if duration > 0 else 0.0]
            )
            logger.info(
                f"MiniMax 转写完成 (耗时: {format_time(time.time() - start)}，"
                f"共 {len(text)} 字符)"
            )
            return text

        # === 切段路径：需要累加时间偏移 + 重映射说话人编号 ===
        logger.info(f"MiniMax 长音频转写: {audio_path}（已切 {len(chunk_paths)} 段）")
        results: List[dict] = []
        chunk_durations: List[float] = []
        for i, chunk in enumerate(chunk_paths, 1):
            logger.info(f"转写第 {i}/{len(chunk_paths)} 段: {chunk.name}")
            results.append(
                _transcribe_one(chunk, api_key, language, role_separation, timestamps, max_wait)
            )
            chunk_durations.append(_probe_audio_duration(chunk))

        text = _assemble(
            results, role_separation, timestamps, chunk_durations=chunk_durations
        )
        if role_separation and is_chunked:
            logger.info(
                "注意：切段后 MiniMax 每段独立重编 S1/S2，响应里没有跨段身份信息——"
                "人数相同时程序只能按编号对齐（可能认错人）。"
                "需要严格的跨段说话人身份请用讯飞（5 小时单文件，不用切段）"
            )
        logger.info(
            f"MiniMax 转写完成（{len(chunk_paths)} 段拼接, "
            f"耗时 {format_time(time.time() - start)}，共 {len(text)} 字符）"
        )
        return text
    finally:
        if temp_dir is not None:
            _cleanup_dir(temp_dir)
        _release_cached_session()


def _collect_chunk_speakers(result: dict) -> List[str]:
    """按出现顺序收集本段出现过的说话人编号（S1 / S2 …）。"""
    speakers: List[str] = []
    for seg in result.get("segments") or []:
        if isinstance(seg, dict):
            spk = str(seg.get("speaker") or "")
            if spk and spk not in speakers:
                speakers.append(spk)
    return speakers


def _assemble(
    results: List[dict],
    role_separation: bool,
    timestamps: bool,
    chunk_durations: List[float],
) -> str:
    """把多个请求的结果拼成一份文本。

    - timestamps=True 时累加段偏移，得到全局时间轴
    - role_separation=True 时做跨段说话人重映射
    - 拿不到 segments（用 json 格式 / 老响应）→ 回落到各段 text 直接拼
    """
    lines: List[str] = []
    global_speaker_map: dict = {}
    time_offset = 0.0

    for idx, result in enumerate(results):
        if role_separation:
            # 先扩映射表，再格式化——这样本段新出现的说话人也能拿到全局编号
            _, global_speaker_map = _build_speaker_map(
                global_speaker_map, _collect_chunk_speakers(result)
            )

        seg_lines = _format_segments(
            result,
            role_separation=role_separation,
            timestamps=timestamps,
            time_offset=time_offset,
            speaker_offset=global_speaker_map if (role_separation and global_speaker_map) else None,
        )
        if seg_lines:
            lines.extend(seg_lines)
        else:
            # 没 segments（response_format=json 或服务端没给）→ 整段 text 兜底
            plain = str(result.get("text") or "").strip()
            if plain:
                lines.append(plain)

        if idx < len(chunk_durations):
            time_offset += max(0.0, float(chunk_durations[idx] or 0.0))

    return "\n".join(lines)


def _mock_transcribe(audio_path: str) -> str:
    """Mock 转写：返回 fake 中文文本（基于音频时长）。"""
    duration = _probe_audio_duration(Path(audio_path))
    n = max(5, int(duration // 30) + 1) if duration > 0 else 5
    sentence = (
        "（这是 MiniMax mock 模式的 fake 转写文本。"
        "在「设置 → 转写 → MiniMax 高级」填入 API Key 并取消 mock 勾选即可调用真实 API。）"
    )
    return "\n".join([sentence] * min(n, 50))
