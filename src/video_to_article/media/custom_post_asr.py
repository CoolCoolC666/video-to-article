"""自定义 POST 云端 ASR backend —— 泛化的 Speech-to-Text REST 调用（2026-10-02）。

本模块最早是「MiniMax STT 专用」，2026-10-02 泛化成**可自定义端点 + 可自定义请求头**，
这样既能指向 MiniMax 官方，也能指向私有部署 / 网关 / 任何兼容这套请求-响应契约的服务。

    端点 POST {endpoint}                    ← 用户可改（默认 MiniMax 官方）
    鉴权 Authorization: Bearer <API Key>    ← 默认，也可被请求头文件覆盖
    附加请求头                             ← 从程序内 text 文件读，用户可编辑

## 契约基线（照 MiniMax 官方 OpenAPI spec，不猜）

    Content-Type: multipart/form-data        ← 真正的 multipart！
    Authorization: Bearer <API_key>
    language: <BCP-47>                       ← 注意是 HTTP **header**，不是 form field

    form:
      model            = "asr-1.0"           （必填）
      file             = <binary>            （必填）
      response_format  = json | verbose_json | srt | vtt
      timestamp_level  = "" | sentence | word
      stream           = false

    ⚠ 与讯飞 lfasr 的协议正好相反：
      讯飞  → query string + raw body（**不能**用 multipart，否则 26600）
      本模块 → 就是 multipart（form-data）
      写新引擎时别把讯飞那套 query string 习惯带过来。

## 硬限制（默认针对 MiniMax，可被服务端改）

    时长 ≤ 500 秒   超了返回 400，不截断
    大小 ≤ 50 MB    超了返回 413
    格式  wav / aiff / flac / alac(m4a) / mp3 / aac / opus / ogg
    不支持无容器裸 PCM

    500 秒 ≈ 8.3 分钟。讯飞是 5 小时，差了两个数量级。
    所以本模块**必须**切段（见 _split_audio_for_long），跟 xf_asr 的
    「永不切段」策略完全相反——两个都是云端，但上限不同。
    换私有部署时如果限制更宽/更窄，改 MAX_DURATION_SEC / MAX_FILE_BYTES 即可。

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

    "asr_engine": "custom_post",
    "custom_post": {
        "api_key": "your-api-key",
        "endpoint": "https://api.minimax.cn/v1/speech_to_text",  ← 可改
        "headers_file": "",        ← 留空 = 用程序内默认 custom_post_headers.txt
        "language": "zh",          # BCP-47；留空 = 自动检测 + 中英混说
        "mock": false,
        "role_separation": true,   # verbose_json（需配合 timestamps）
        "timestamps": true,
        "max_wait_seconds": 300
    }

2026-10-02 新增：minimax_asr.py → 泛化为 custom_post_asr.py
2026-10-02 兼容：config 里的 "asr_engine": "minimax_asr" 仍可用（打 WARNING 提示迁移）
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
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..logging_config import configure_logging
from ..paths import APP_ROOT
from ..text_utils import format_time, import_required

logger = configure_logging()


# 默认端点（用户可在 GUI 改成任意兼容服务）
DEFAULT_ENDPOINT = "https://api.minimax.cn/v1/speech_to_text"
LEGACY_ENDPOINT = DEFAULT_ENDPOINT
_MODEL = "asr-1.0"

# 请求头文件：程序内，默认文件名。用戶可直接编辑（每行一条 Key: Value）
HEADERS_FILENAME = "custom_post_headers.txt"

# ============ API 风格（2026-10-04 新增）============
# 同一个「自定义 POST」框要接两类**协议完全不同**的服务，所以必须显式选风格。
# 不是「换个 URL」那么简单——请求体、鉴权、音频传递方式、拿结果的方式全都不同。
#   2026-10-04 实测：往 DashScope 走 OpenAI 兼容的 multipart，会被中间层断连（10054）。
API_STYLES = {
    "openai_compat": "OpenAI 兼容（MiniMax / 中转 / vLLM 等）",
    "dashscope_async": "DashScope 异步（阿里云百炼 Qwen3-ASR）",
}

# ---------- 风格 openai_compat：一次性 multipart 上传，直接返回结果 ----------
OPENAI_COMPAT_PATH = "/speech_to_text"
# 单请求 ≤ 500 秒 / 50 MB（OpenAI 兼容侧 OpenAPI 明确写出）
MAX_DURATION_SEC = 500     # 时长上限，超了 400
MAX_FILE_BYTES = 50 * 1024 * 1024  # 大小上限，超了 413
# 切段留 50 秒余量：ffprobe 探测有误差，卡着 500s 切必 400
CHUNK_SEGMENT_SEC = 450

# ---------- 风格 dashscope_async：提交任务 → 轮询 → 下载结果 ----------
# 官方文档（2026-10-04 查证并实测确认）：
#   POST {base}/services/audio/asr/transcription
#     Header: Authorization: Bearer <key>  +  X-DashScope-Async: enable
#     Body:   application/json（**不是 multipart**）
#            {"model": "...", "input": {"file_urls": ["<公网URL>"]},
#             "parameters": {"channel_id": [0], "language_hints": ["zh","en"]}}
#   GET  {base}/tasks/{task_id}   → output.results[].transcription_url
#   GET  {transcription_url}      → 完整结果 JSON（24 小时有效，务必及时下载）
# ⚠ 关键约束：**音频必须是公网 URL**，不接受文件上传。本模块靠图床中转拿 URL。
DASHSCOPE_TASK_PATH = "/services/audio/asr/transcription"
# 异步转写支持单文件 12 小时 / 2 GB —— 比 OpenAI 兼容宽松得多，基本不需要切段
DASHSCOPE_MAX_DURATION_SEC = 12 * 3600
DASHSCOPE_MAX_BYTES = 2 * 1024 * 1024 * 1024
DASHSCOPE_POLL_INTERVAL = 5.0
DASHSCOPE_DONE = {"SUCCEEDED", "SUCCESS", "COMPLETED", "DONE"}
DASHSCOPE_FAILED = {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}

# 支持的音频格式（OpenAI 兼容侧 OpenAPI 列的）
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
    401: "鉴权失败：检查 API Key / 请求头文件里的 Authorization 是否正确",
    402: "账户余额不足：去 https://platform.minimax.cn/user-center/basic-information 充值",
    403: "无权限：该端点拒绝了当前凭证（检查请求头文件里的鉴权信息）",
    404: "端点不存在：检查「接口地址」填的 URL 是否正确",
    413: "音频超过 50 MB 上限：转成单声道 16kHz 或 mp3 压缩后再试",
    422: "音频内容涉及敏感内容，被平台拒绝",
    429: "触发限流：稍后重试",
    500: "服务端错误：稍后重试",
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


# ============ 请求头文件 ============
def default_headers_file() -> Path:
    """程序内默认请求头文件路径（与 exe / 源码同目录）。"""
    return Path(APP_ROOT) / HEADERS_FILENAME


def write_headers_template(path: Optional[Path] = None, overwrite: bool = False) -> Path:
    """生成请求头文件模板（首次用 / 用户点「生成模板」时调用）。

    只写占位说明，**不写入真实密钥**。
    """
    target = Path(path) if path else default_headers_file()
    if target.exists() and not overwrite:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# 自定义 POST ASR — 附加请求头\n"
        "#\n"
        "# 格式：每行一条  Key: Value\n"
        "#  - # 开头是注释，空行忽略\n"
        "#  - 这里的头会与程序自动生成的合并（同名以本文件为准）\n"
        "#  - Authorization 通常在「API Key」输入框里填，不用写这里；\n"
        "#    只有当服务端要求非 Bearer 鉴权时才写在这里\n"
        "#  - 本文件可能含密钥，已加入 .gitignore，不要提交到仓库\n"
        "#\n"
        "# 例：私有部署要额外带一个 token 头\n"
        "# X-Deploy-Token: your-token-here\n"
        "#\n"
        "# 例：走网关要加路由头\n"
        "# X-Gateway-Route: asr-prod\n",
        encoding="utf-8",
    )
    logger.info(f"已生成请求头模板: {target}")
    return target


def load_headers_file(path: Optional[Path] = None) -> Dict[str, str]:
    """读请求头文件 → dict。文件不存在就生成模板并返回空 dict。

    解析规则：
      - 一行一条 `Key: Value`
      - `#` 开头或行内 `  #` 之后是注释
      - 空行忽略
      - 没有冒号的行忽略（并 WARNING，避免静默吃掉用户的输入）
      - 键大小写不敏感去重（后写的覆盖先写的）
    """
    target = Path(path) if path else default_headers_file()
    if not target.exists():
        write_headers_template(target)
        return {}

    headers: Dict[str, str] = {}
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"读取请求头文件失败（已忽略该文件）: {target} — {exc}")
        return {}

    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉行内注释（要求 # 前有空白，避免砍掉 token 里的 #）
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if not line or ":" not in line:
            if line:
                logger.warning(
                    f"请求头文件第 {lineno} 行没有 ':'，已忽略: {line[:60]!r}"
                )
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # 同名（忽略大小写）后写的覆盖
        for existing in list(headers):
            if existing.lower() == key.lower() and existing != key:
                headers[key] = headers.pop(existing)
                break
        headers[key] = value

    if headers:
        # 只打印键名，绝不打印值（值里通常是密钥）
        logger.info(f"已从 {target.name} 加载 {len(headers)} 个自定义请求头: {sorted(headers)}")
        return headers

    # ⚠ 一个 Key: Value 都没解析出来 —— 多半是把**依赖清单 / 需求文档 / 任意文本**
    #   当成请求头文件填了。逐行 WARNING 之外，再给一条整体提示，
    #   否则用户会以为「程序读到了，就是没生效」。
    if target.exists() and target.stat().st_size > 0:
        logger.warning(
            f"请求头文件 {target} 里没有解析出任何 Key: Value —— "
            f"它不是请求头文件（要求每行形如 `Authorization: xxx`）。"
            f"留空「请求头文件」输入框即可改用程序内默认的 {HEADERS_FILENAME}。"
        )
    return headers


def build_headers(
    api_key: str,
    language: str,
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """合并请求头：程序生成的 < 请求头文件 < 用户显式设置。

    优先级（后者覆盖前者）：
      1. `language`（程序按 config 生成；用户文件里写了 language 也以文件为准）
      2. `Authorization`（有 api_key 才加；请求头文件里写了就以文件为准）
      3. 请求头文件内容
    """
    headers: Dict[str, str] = {}
    if language:
        headers["language"] = language
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for k, v in (extra or {}).items():
        # 空值视为「用户想删掉这个头」
        if v == "":
            headers.pop(k, None)
            continue
        headers[k] = v
    return headers



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
    pattern = os.path.join(tempfile.gettempdir(), "custom_post_asr_chunks_*")
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
    temp_dir = Path(tempfile.mkdtemp(prefix="custom_post_asr_chunks_"))
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

def _validate_credentials(has_creds: bool) -> None:
    """凭证兜底校验。调用前已判定过，这里只防逻辑漏改。"""
    if not has_creds:
        raise RuntimeError(
            "自定义 POST ASR 没有可用凭证（API Key 为空，且请求头文件里也没有 Authorization）。"
        )


# ============ API 调用 ============

def _extract_error(resp, url: str = "") -> str:
    """把 OpenAI 风格错误体翻译成可读的中文提示。"""
    hint = _ERROR_HINTS.get(resp.status_code, "")
    if not hint and not url:
        hint = "（非标准错误码；若用的是自定义端点，请对照该服务的文档）"
    detail = ""
    try:
        body = resp.json()
        err = body.get("error") or {}
        detail = str(err.get("message") or body.get("message") or "")
        req_id = body.get("request_id") or ""
    except Exception:
        detail = (resp.text or "")[:300]
        req_id = ""
    parts = [f"自定义 POST ASR HTTP {resp.status_code}"]
    if hint:
        parts.append(hint)
    if detail:
        parts.append(f"服务端信息: {detail}")
    if req_id:
        parts.append(f"request_id={req_id}")
    if url:
        parts.append(f"端点={url}")
    return " | ".join(parts)


def build_base(base_url: str) -> str:
    """把用户填的「接口地址」归一成 base（去结尾斜杠 + 去尾部已知路径）。

    用户最容易填错的就是这一层——GUI 里也提示「只填到 /api/v1」。
    这里做防御性清洗：万一填了完整路径，也认得出来，
    不会拼成 /speech_to_text/speech_to_text 这种鬼东西。
    """
    base = str(base_url or "").strip().rstrip("/")
    for suffix in (OPENAI_COMPAT_PATH, DASHSCOPE_TASK_PATH, "/models"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def build_stt_endpoint(base_url: str, style: str = "openai_compat") -> str:
    """拼出实际调用的转写端点。GUI 用它做实时预览。"""
    base = build_base(base_url)
    if not base:
        return ""
    if style == "dashscope_async":
        return f"{base}{DASHSCOPE_TASK_PATH}"
    return f"{base}{OPENAI_COMPAT_PATH}"


def build_task_query_url(base_url: str, task_id: str) -> str:
    """拼任务查询端点（只有 dashscope_async 用）。"""
    base = build_base(base_url)
    return f"{base}/tasks/{task_id}" if base else ""


def _transcribe_one(
    audio_path: Path,
    api_key: str,
    language: str,
    role_separation: bool,
    timestamps: bool,
    max_wait: int,
    endpoint: str = DEFAULT_ENDPOINT,
    extra_headers: Optional[Dict[str, str]] = None,
) -> dict:
    """单文件真实 API 调用，返回解析后的 JSON dict。

    走真 multipart（与讯飞相反）。
    """
    session = _get_session()
    # verbose_json 同时给 segments[] + n_speakers；只要开分离或时间戳就必须用它
    need_verbose = bool(role_separation or timestamps)
    response_format = "verbose_json" if need_verbose else "json"

    # 统一走 build_stt_endpoint：填完整路径也能归一，不会拼出 /speech_to_text/speech_to_text
    url = build_stt_endpoint(endpoint, "openai_compat")
    headers = build_headers(api_key, language, extra_headers)

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
        f"上传到自定义 POST ASR: {audio_path.name} ({size_mb:.2f}MB, "
        f"format={response_format}, language={language or 'auto'})\n"
        f"  端点: {url}\n"
        f"  请求头键: {sorted(headers)}"
    )

    with open(audio_path, "rb") as f:
        files = {"file": (audio_path.name, f, "application/octet-stream")}
        resp = session.post(url, headers=headers, data=data, files=files,
                            timeout=max(30, max_wait))
    if resp.status_code != 200:
        raise RuntimeError(_extract_error(resp, url))
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"自定义 POST ASR 返回非 JSON (HTTP {resp.status_code}, {url}): "
            f"{(resp.text or '')[:300]}"
        ) from exc


# ============ DashScope 异步流程（2026-10-04 新增）============
# 实测确认：DashScope 的文件级 ASR **不接受文件上传**，必须是公网 URL。
# 所以这里借项目自带的图床（config.image_host）做中转：先把音频传上去拿 URL。

_IMAGE_HOST_MISSING = (
    "DashScope 模式需要音频的公网 URL，但没配置图床。\n"
    "三选一：\n"
    "  ① 到「设置 → 图床」填好 api_url / token（可先点右下「管理…」旁的\n"
    "     启用图床上传试一下能否传 mp3）\n"
    "  ② 或自己把 mp3 传到任意公网可访问的地方，把直链填进 config 的\n"
    "     transcribe.custom_post.audio_url（跳过图床）\n"
    "  ③ 或改用「OpenAI 兼容」风格 + 不传音频的纯文本服务"
)


def _placeholder_host_hint(url: str) -> str:
    """给占位 URL 生成一句 host 侧的说明（不回显整串 URL）。"""
    try:
        host = (urllib.parse.urlsplit(str(url)).hostname or "").strip()
    except Exception:
        return "（无法解析）"
    if not host:
        return "（空）"
    if any(ord(c) > 127 for c in host):
        return f"{host}（含中文 —— 这是占位符，没换成真实域名）"
    return f"{host}（看着像模板/示例域名）"


def _looks_like_placeholder_url(url: str) -> bool:
    """判断 URL 是不是「模板里没替换的占位符」而不是真实地址。

    ⚠ 这是实测踩到的坑：用户 config 里存着模板原样的
    `https://你的图床域名/api/index.php`，非空、能通过所有「有没有配」检查，
    于是程序去请求它 —— 而中文域名会被 requests 自动 punycode 编码成
    `xn--6qqv7isyczza37vgq1b`，报出来一堆人看不懂的东西：
        Failed to resolve 'xn--6qqv7isyczza37vgq1b' ([Errno 11001])
    真正的原因只是「这个占位符没换」。

    判据只认**域名部分**：URL 的 path 完全可以合法含中文。
    """
    raw = str(url or "").strip()
    if not raw:
        return True
    try:
        host = (urllib.parse.urlsplit(raw).hostname or "").strip()
    except Exception:
        return True
    if not host:
        return True
    # ① 域名含非 ASCII —— 真实服务不会用中文域名，几乎必然是没替换的占位
    if any(ord(c) > 127 for c in host):
        return True
    h = host.lower()
    # ② 文档/模板里最常见的占位域名
    if h in {
        "example.com", "example.org", "example.net", "your-domain.com",
        "yourdomain.com", "your-site.com", "yourdomain", "domain.com",
        "test.com", "changeme.com", "placeholder.com", "todo.com",
    }:
        return True
    # ③ your-xxx / my-domain 这类明显占位前缀
    if h.startswith(("your-", "your_", "your.", "my-domain", "mydomain", "xxx.")):
        return True
    # ④ xn-- 是 punycode 前缀；若整体像一个被随机化的字符串也算可疑
    if "xn--" in h and h.replace("-", "").replace("xn", "").replace(".", "").strip(
        "0123456789abcdef"
    ) == "":
        return True
    return False


def upload_audio_for_public_url(audio_path: Path, image_host_config: dict) -> str:
    """把音频借图床传成公网 URL（DashScope 必需）。

    复用 `cover.upload_image_to_host`（本质是通用 multipart 上传，EasyImage 图床
    本身能收任意文件类型，mime 由 mimetypes 猜）。
    """
    api_url = str((image_host_config or {}).get("api_url") or "").strip()
    if not image_host_config or not api_url:
        raise RuntimeError(_IMAGE_HOST_MISSING)
    if _looks_like_placeholder_url(api_url):
        # ⚠ 不要把原始 URL 原样打出来——那正是报错的根源，重复一遍只会
        # 让用户对着同一个看不懂的 punycode 字符串发呆。只说清「哪里没填」。
        host_hint = _placeholder_host_hint(api_url)
        raise RuntimeError(
            "DashScope 模式需要音频的公网 URL，但图床地址还是**模板里的占位符**，"
            "从没换成真实域名。\n"
            f"当前 host 部分：{host_hint}\n\n"
            "这不是网络问题 —— 那个中文占位符被自动转成了 punycode，"
            "报出来是 'Failed to resolve xn--...'，看着像 DNS 故障，其实是没填。\n\n"
            "三选一：\n"
            "  ① 到「设置 → 图床」把 api_url 换成你的**真实图床地址**\n"
            "     （EasyImage 系的接口形如 https://你的域名/api/index.php），\n"
            "     并确认已启用\n"
            "  ② 自己把 mp3 传到任意公网可访问处，把直链填进 config 的\n"
            "     transcribe.custom_post.audio_url（跳过图床）\n"
            "  ③ 改用「OpenAI 兼容」风格 + 不传音频的纯文本服务"
        )
    from ..cover import upload_image_to_host

    result = upload_image_to_host(audio_path, image_host_config)
    url = str((result or {}).get("url") or "")
    if not url:
        raise RuntimeError(f"图床上传成功但没拿到 URL: {result}")
    logger.info(f"音频已借图床转为公网 URL: {url}")
    return url


def _dashscope_headers(api_key: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable"}
    for k, v in (extra or {}).items():
        if v:
            headers[k] = v
    return headers


def _dashscope_submit(
    base_url: str,
    api_key: str,
    model: str,
    audio_url: str,
    language: str,
    extra_headers: Optional[Dict[str, str]] = None,
    role_separation: bool = False,
    speaker_count: int = 2,
    colloquial_proc: bool = False,
) -> str:
    """提交异步转写任务，返回 task_id。

    parameters 字段全部来自官方「任务提交接口」文档（2026-10-04 查证）：
      channel_id                  音轨索引（多声道会**分别计费**，默认只取 [0]）
      language_hints              仅 paraformer-v2 支持；其他模型传了可能报错
      diarization_enabled         自动说话人分离；**仅单声道**，多声道不支持
      speaker_count               说话人数参考值（2-100），只是提示不保证
      disfluency_removal_enabled  过滤语气词（嗯/啊/呃）——等价于 xf_asr 的口语规整
    """
    requests = import_required("requests", "requests")
    session = _get_session()
    url = build_stt_endpoint(base_url, "dashscope_async")
    if not model:
        raise RuntimeError(
            "DashScope 模式必须填模型名（如 paraformer-v2 / qwen3-asr-flash-filetrans / "
            "qwen-audio-3.1-asr-flash-filetrans）。"
        )
    # 官方明确警告：URL 含空格/中文必须先 percent-encode，
    # 否则报 InvalidFile.DownloadFailed。用户的课件文件名几乎都是中文。
    safe_url = urllib.parse.quote(audio_url, safe=":/?#[]@!$&'()*+,;=")

    params: Dict[str, object] = {"channel_id": [0]}
    if language and "paraformer" in str(model).lower():
        # language_hints 只对 paraformer-v2 系列有效，其他模型传了会 400
        params["language_hints"] = [language]
    if colloquial_proc:
        params["disfluency_removal_enabled"] = True
    if role_separation:
        params["diarization_enabled"] = True
        if 2 <= int(speaker_count or 2) <= 100:
            params["speaker_count"] = int(speaker_count)

    body = {
        "model": model,
        "input": {"file_urls": [safe_url]},
        "parameters": params,
    }
    logger.info(
        f"提交 DashScope 异步任务: {url}\n"
        f"  model={model}  分离={bool(role_separation)}  人数={speaker_count}\n"
        f"  音频 URL={safe_url[:110]}"
    )
    resp = session.post(
        url, headers=_dashscope_headers(api_key, extra_headers), json=body, timeout=60
    )
    if resp.status_code != 200:
        raise RuntimeError(_extract_error(resp, url))
    payload = resp.json()
    task_id = str(((payload or {}).get("output") or {}).get("task_id") or "")
    if not task_id:
        raise RuntimeError(f"DashScope 没返回 task_id: {str(payload)[:300]}")
    logger.info(f"任务已提交: {task_id}")
    return task_id


def _dashscope_poll(
    base_url: str,
    api_key: str,
    task_id: str,
    max_wait: int,
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """轮询任务直到完成，返回 transcription_url。"""
    requests = import_required("requests", "requests")
    session = _get_session()
    url = build_task_query_url(base_url, task_id)
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        resp = session.get(
            url, headers=_dashscope_headers(api_key, extra_headers), timeout=30
        )
        if resp.status_code != 200:
            raise RuntimeError(_extract_error(resp, url))
        payload = resp.json() or {}
        out = payload.get("output") or {}
        status = str(out.get("task_status") or "").upper()
        if status in DASHSCOPE_DONE:
            # ⚠ 官方明确：整体 SUCCEEDED 时**子任务仍可能 FAILED**，
            #    必须看 results[].subtask_status，否则会拿着一个不存在的 URL 去下载。
            results = out.get("results") or []
            for item in results:
                if not isinstance(item, dict):
                    continue
                sub = str(item.get("subtask_status") or "SUCCEEDED").upper()
                if sub != "SUCCEEDED":
                    code = item.get("code") or ""
                    msg = item.get("message") or ""
                    hint = ""
                    if code == "InvalidFile.DownloadFailed":
                        hint = (
                            "\n常见原因：音频 URL 里有中文/空格没编码好，或图床不允许外网访问。"
                        )
                    raise RuntimeError(
                        f"DashScope 子任务失败（{sub}，code={code}）: {msg}{hint}"
                    )
                link = str(item.get("transcription_url") or "").strip()
                if link:
                    logger.info(f"任务完成（第 {attempt} 次轮询），结果链接已拿到")
                    return link
            inline = out.get("transcription")
            if inline:
                logger.info("任务完成且结果内联，无需二次下载")
                return f"inline:{json.dumps(inline, ensure_ascii=False)}"
            raise RuntimeError(
                f"任务状态是 {status}，但结果里既没有 transcription_url 也没有内联内容: "
                f"{str(payload)[:300]}"
            )
        if status in DASHSCOPE_FAILED:
            raise RuntimeError(
                f"DashScope 任务失败（status={status}）: {str(payload)[:300]}"
            )
        logger.info(f"轮询 #{attempt}: status={status or 'PENDING'}")
        time.sleep(DASHSCOPE_POLL_INTERVAL)
    raise RuntimeError(
        f"DashScope 任务轮询超时（>{max_wait}s, {attempt} 次），task_id={task_id}"
    )


def _dashscope_download(link: str, timeout: int = 120) -> dict:
    """下载 transcription_url 指向的结果 JSON（24 小时有效，务必及时取）。"""
    requests = import_required("requests", "requests")
    session = _get_session()
    if link.startswith("inline:"):
        return json.loads(link[len("inline:"):])
    resp = session.get(link, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(
            f"下载转写结果失败 HTTP {resp.status_code}: {link[:120]}"
        )
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"转写结果不是 JSON（链接可能已过期 24h）: {link[:120]}"
        ) from exc


def _dashscope_transcribe(
    audio_path: Path,
    base_url: str,
    api_key: str,
    model: str,
    language: str,
    max_wait: int,
    audio_url: str = "",
    image_host_config: Optional[dict] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    role_separation: bool = False,
    speaker_count: int = 2,
    colloquial_proc: bool = False,
) -> dict:
    """DashScope 异步转写全流程：拿 URL → 提交 → 轮询 → 下载。

    Returns:
        归一化成 {text, segments(秒), n_speakers} 的 dict，交给 _format_segments 用。
    """
    url = audio_url or upload_audio_for_public_url(audio_path, image_host_config or {})
    task_id = _dashscope_submit(
        base_url, api_key, model, url, language, extra_headers,
        role_separation=role_separation,
        speaker_count=speaker_count,
        colloquial_proc=colloquial_proc,
    )
    link = _dashscope_poll(base_url, api_key, task_id, max_wait, extra_headers)
    payload = _dashscope_download(link)
    return _normalize_dashscope_result(payload)


def _normalize_dashscope_result(payload) -> dict:
    """把 DashScope 结果 JSON 归一成 {text, segments, n_speakers}（**秒**）。

    schema 依据官方「识别结果说明」（2026-10-04 查证原文）：

        {"file_url": ..., "properties": {...},
         "transcripts": [{
            "channel_id": 0,
            "content_duration_in_milliseconds": 3720,
            "text": "整段文本",
            "sentences": [
               {"begin_time": 100, "end_time": 3820,   <-- 毫秒
                "text": "...", "sentence_id": 1,
                "speaker_id": 0,                     <-- 仅开启说话人分离时才有
                "words": [...]}
            ]}]}

    三处最容易踩的：
      1. 顶层是 transcripts[]，sentences 在里面一层
      2. **时间戳单位是毫秒**（官方原文 "Timestamps are in milliseconds"），
         归一时统一除 1000 转成秒。否则 3.2 秒会被显示成 53 分 20 秒。
      3. speaker_id **只在开了 diarization_enabled 时返回**
    """
    if isinstance(payload, str):
        return {"text": payload, "segments": [], "n_speakers": 0}
    if not isinstance(payload, dict):
        return {"text": str(payload), "segments": [], "n_speakers": 0}

    sentences: list = []
    plain = ""
    for tr in (payload.get("transcripts") or []):
        if not isinstance(tr, dict):
            continue
        if not plain:
            plain = str(tr.get("text") or "").strip()
        for sent in (tr.get("sentences") or []):
            if isinstance(sent, dict):
                sentences.append(sent)

    # 兜底：某些部署可能直接给 sentences / 顶层 text
    if not sentences:
        for key in ("sentences", "sentences_list", "segments", "sentence_info"):
            v = payload.get(key)
            if isinstance(v, list) and v:
                sentences = [x for x in v if isinstance(x, dict)]
                break
    if not plain:
        for src in (payload, payload.get("output") or {}):
            for key in ("text", "transcription", "content"):
                v = src.get(key) if isinstance(src, dict) else None
                if isinstance(v, str) and v.strip():
                    plain = v.strip()
                    break
            if plain:
                break

    def _sec(item, *keys) -> float:
        """取毫秒字段并转成秒。"""
        for k in keys:
            if item.get(k) is not None:
                try:
                    return float(item[k]) / 1000.0
                except (TypeError, ValueError):
                    continue
        return 0.0

    segments = []
    for sent in sentences:
        text = str(
            sent.get("text") or sent.get("sentence") or sent.get("content") or ""
        ).strip()
        if not text:
            continue
        spk = sent.get("speaker_id")
        if spk in (None, ""):
            for k in ("speaker", "spk", "speakerId", "role_id"):
                if sent.get(k) not in (None, ""):
                    spk = sent.get(k)
                    break
        segments.append({
            "text": text,
            "start": _sec(sent, "begin_time", "start_time", "begin", "start", "offset"),
            "end": _sec(sent, "end_time", "end"),
            "speaker": (f"spk{spk}" if spk not in (None, "") else ""),
        })

    if not plain and segments:
        plain = "".join(s["text"] for s in segments)
    if not plain and not segments:
        raise RuntimeError(
            "认不出 DashScope 返回结果的结构（可能该部署用别的字段名）。"
            f"顶层键: {sorted(payload)[:12]}"
        )
    speakers = {s["speaker"] for s in segments if s["speaker"]}
    return {"text": plain, "segments": segments, "n_speakers": len(speakers)}



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
    """把 segments[]（或 DashScope 归一后的同构 dict）格式化成行。

    DashScope 那条路经 `_normalize_dashscope_result` 归一后，
    segments 项的键是 text / start / end / speaker（**秒**），
    与这里的 OpenAI 兼容形态不同，故先按是否已有 start 判定。
    两种形态字段名基本同名（speaker vs 无），见下方取值优先级。

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

def transcribe_audio_with_custom_post(audio_path: str, config: dict) -> str:
    """自定义 POST 云端 ASR 主入口（默认端点 = MiniMax 官方，可改成任意兼容服务）。

    Args:
        audio_path: 音频文件路径
        config:     config.json 中 transcribe.custom_post 块

    Returns:
        转写文本

    config 字段：
      - api_key         API Key（写入 `Authorization: Bearer <key>`）
      - endpoint        接口地址，默认 MiniMax 官方
      - headers_file    请求头文件路径；留空 = 程序内 custom_post_headers.txt
      - language        BCP-47；留空 = 自动检测
      - role_separation / timestamps / max_wait_seconds / mock

    Raises:
        RuntimeError: 凭证缺失 / API 调用失败 / 切段失败

    Mock 行为（与 xf_asr 一致的三分支日志）：
        1. mock=true + 凭证齐全 → WARNING（可能忘了取消勾选）
        2. mock=true + 凭证缺失 → INFO
        3. mock=false + 凭证缺失 → WARNING + 自动降级 mock

    切段行为（与 xf_asr **相反**）：
        默认单请求 ≤500 秒 / 50 MB，超了必须切；讯飞是 5 小时不用切。
        换私有部署时若限制不同，改模块顶部 MAX_DURATION_SEC / MAX_FILE_BYTES。
    """
    cfg = config or {}
    mock = bool(cfg.get("mock", False))
    api_key = (cfg.get("api_key") or "").strip()
    endpoint = (cfg.get("endpoint") or DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    language = (cfg.get("language") or "").strip()
    role_separation = bool(cfg.get("role_separation", False))
    timestamps = bool(cfg.get("timestamps", True))
    max_wait = int(cfg.get("max_wait_seconds") or 300)
    # 2026-10-04 新增：API 风格 + DashScope 专用字段
    api_style = str(cfg.get("api_style") or "openai_compat").strip()
    if api_style not in API_STYLES:
        logger.warning(
            f"未知的 API 风格 {api_style!r}，回落到 openai_compat"
            f"（可选：{list(API_STYLES)}）"
        )
        api_style = "openai_compat"
    model = str(cfg.get("model") or "").strip()
    audio_url = str(cfg.get("audio_url") or "").strip()
    # DashScope 要公网 URL：优先用户直填，其次借图床中转
    image_host_config: Optional[dict] = None
    if api_style == "dashscope_async" and not audio_url:
        try:
            from ..config import load_config

            image_host_config = (load_config() or {}).get("image_host") or {}
        except Exception as e:
            logger.warning(f"读图床配置失败（DashScope 模式需要）: {e}")

    # 请求头文件：留空用程序内默认文件
    headers_path_raw = (cfg.get("headers_file") or "").strip()
    headers_path = Path(headers_path_raw) if headers_path_raw else default_headers_file()
    # 不存在会自动生成模板
    extra_headers = load_headers_file(headers_path)
    if extra_headers and api_style == "dashscope_async":
        logger.info("DashScope 模式也会带上请求头文件里的自定义头")

    # 凭证可以来自两处：API Key 输入框，或请求头文件里的 Authorization。
    # 后者支持非 Bearer 鉴权（私有部署常见），所以空 API Key 不等于没凭证。
    def _has_auth(h: Dict[str, str]) -> bool:
        return any(k.lower() == "authorization" and v.strip() for k, v in h.items())

    has_creds = bool(api_key) or _has_auth(extra_headers)

    if mock or not has_creds:
        if mock and has_creds:
            logger.warning(
                "custom_post 凭证已配置（API Key 或请求头文件里的 Authorization）"
                "但 Mock 模式仍勾选——将走 Mock 不调真实 API。"
                "如需真实 API，请取消 Mock 勾选。"
            )
        elif mock:
            logger.info("custom_post Mock 模式（显式勾选）—— 不调真实 API。")
        else:
            logger.warning(
                "custom_post 凭证缺失，自动降级 Mock 模式（不调网络、不需要 key）。\n"
                f"  填「API Key」输入框，或在请求头文件 {headers_path} 里写 "
                "Authorization: <你的凭据>，然后取消 mock 勾选。"
            )
        return _mock_transcribe(audio_path)

    _validate_credentials(has_creds)
    audio_path_obj = Path(audio_path)

    # 端点格式预检：早点报，比服务端 404 好查
    if not endpoint.lower().startswith(("http://", "https://")):
        hint = (
            f"DashScope 示例: https://dashscope.aliyuncs.com/api/v1"
            if api_style == "dashscope_async"
            else f"MiniMax 官方示例: {DEFAULT_ENDPOINT}"
        )
        raise RuntimeError(
            f"接口地址必须以 http:// 或 https:// 开头，实得: {endpoint!r}。\n"
            f"⚠ 只填到「/api/v1」这一层，后面的路径程序自己拼。{hint}"
        )

    # 格式预检：裸 PCM 会被 400 拒，MP4/MKV 这类容器也不支持
    ext = audio_path_obj.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        logger.warning(
            f"文件后缀 {ext} 不在支持列表（{'/'.join(sorted(SUPPORTED_EXTS))}）——"
            "若报 400 请先转成 mp3/wav（程序下载音轨时默认就是 mp3）"
        )

    # ===== DashScope 异步：单独一条路（音频必须先变成公网 URL）=====
    if api_style == "dashscope_async":
        if not model:
            raise RuntimeError(
                "DashScope 模式必须填「模型名」（如 qwen3-asr-flash-filetrans / "
                "qwen-audio-3.1-asr-flash-filetrans / paraformer-v2）。\n"
                "在「设置 → 转写 → 自定义（POST）高级 → 模型名」填写。"
            )
        t0 = time.time()
        result = _dashscope_transcribe(
            audio_path_obj,
            base_url=endpoint,
            api_key=api_key,
            model=model,
            language=language,
            max_wait=max_wait,
            audio_url=audio_url,
            image_host_config=image_host_config,
            extra_headers=extra_headers,
            role_separation=role_separation,
            speaker_count=int(cfg.get("speaker_count") or 2),
            colloquial_proc=bool(cfg.get("colloquial_proc", False)),
        )
        text = _assemble(
            [result], role_separation, timestamps,
            chunk_durations=[duration if duration > 0 else 0.0],
        )
        logger.info(
            f"DashScope 转写完成 (耗时: {format_time(time.time() - t0)}，"
            f"共 {len(text)} 字符)"
        )
        _release_cached_session()
        return text

    start = time.time()
    duration = _probe_audio_duration(audio_path_obj)
    chunk_paths, is_chunked = _split_audio_for_long(audio_path, duration)
    temp_dir = chunk_paths[0].parent if is_chunked else None

    try:
        if not is_chunked:
            result = _transcribe_one(
                audio_path_obj, api_key, language, role_separation, timestamps,
                max_wait, endpoint=endpoint, extra_headers=extra_headers,
            )
            text = _assemble(
                [result], role_separation, timestamps,
                chunk_durations=[duration if duration > 0 else 0.0],
            )
            logger.info(
                f"自定义 POST ASR 转写完成 (耗时: {format_time(time.time() - start)}，"
                f"共 {len(text)} 字符)"
            )
            return text

        # === 切段路径：需要累加时间偏移 + 说话人编号对齐 ===
        logger.info(f"长音频转写: {audio_path}（已切 {len(chunk_paths)} 段）")
        results: List[dict] = []
        chunk_durations: List[float] = []
        for i, chunk in enumerate(chunk_paths, 1):
            logger.info(f"转写第 {i}/{len(chunk_paths)} 段: {chunk.name}")
            results.append(
                _transcribe_one(chunk, api_key, language, role_separation, timestamps,
                                max_wait, endpoint=endpoint, extra_headers=extra_headers)
            )
            chunk_durations.append(_probe_audio_duration(chunk))

        text = _assemble(
            results, role_separation, timestamps, chunk_durations=chunk_durations
        )
        if role_separation and is_chunked:
            logger.info(
                "注意：切段后服务端每段独立重编 S1/S2，响应里没有跨段身份信息——"
                "人数相同时程序只能按编号对齐（可能认错人）。"
                "需要严格的跨段说话人身份请用讯飞（5 小时单文件，不用切段）"
            )
        logger.info(
            f"转写完成（{len(chunk_paths)} 段拼接, "
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
        "（这是自定义 POST ASR 的 mock 转写文本。"
        "在「设置 → 转写 → 自定义（POST）高级」填入 API Key（或在请求头文件里写 "
        "Authorization）并取消 mock 勾选即可调用真实接口。）"
    )
    return "\n".join([sentence] * min(n, 50))


# ============ 旧引擎名兼容 ============

def transcribe_audio_with_minimax_asr(audio_path: str, config: dict) -> str:
    """**已弃用**的旧入口，转发到 transcribe_audio_with_custom_post()。

    2026-10-02：本引擎从「MiniMax 专用」泛化成「自定义 POST」后保留此别名，
    避免用户 config.json 里已保存的 "asr_engine": "minimax_asr" 静默失效
    （那会回落到 funasr，用户以为在用云端其实在本地跑，很难查）。
    """
    logger.warning(
        'asr_engine="minimax_asr" 已改名为 "custom_post"（本引擎已泛化为自定义 POST）。'
        '本次仍按旧名执行，请把 config.json 里的 asr_engine 改成 "custom_post"，'
        "并把 transcribe.minimax_asr 块改名为 transcribe.custom_post。"
    )
    return transcribe_audio_with_custom_post(audio_path, config)
