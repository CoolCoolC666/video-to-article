"""讯飞听见云端 ASR backend — 长语音转写 REST API。

讯飞开放平台: https://www.xfyun.cn/
长语音转写 API: https://www.xfyun.cn/doc/asr/lfasr/API.html

设计原则（与 qwen_asr.py 对齐）：
- 长音频自动切段（> 7.5 分钟切 5 分钟一段）—— 单文件 ≤500MB，>7.5min 必切
- Mock 模式（凭证缺失 / 显式 mock=true）—— 无网络、无凭证可跑通
- 凭证校验 + 异常路径清理 tempdir
- atexit 兜底清理 tempdir（GUI 强杀 / 进程崩溃场景）
- requests.Session 模块级 cache，连接复用

config.json 用法（transcribe 块加）：
    "asr_engine": "xf_asr",
    "xf_asr": {
        "app_id": "your-app-id",
        "secret_key": "your-secret-key",
        "language": "cn",       # cn / en / ja 等（讯飞自动识别也可省略）
        "mock": false,          # true = mock 模式（无凭证 / 本地调试）
        "max_wait_seconds": 600 # 任务轮询超时
    }

2026-09-27 新增：xf_asr.py（讯飞听见云端识别 backend）
2026-09-27 订正：删 api_key 字段——长语音鉴权只需 APPID + SecretKey 两件套
        （APIKey 是短音频 ifasr 实时接口的字段，长语音 lfasr/ifasr_new 不参与签名）
"""
from __future__ import annotations

import atexit
import base64
import glob
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import List, Optional

from ..logging_config import configure_logging
from ..text_utils import format_time

logger = configure_logging()


# 讯飞长语音转写 v2 API 端点
_UPLOAD_URL = "https://raasr.xfyun.cn/v2/api/upload"
_RESULT_URL = "https://raasr.xfyun.cn/v2/api/getResult"

# 长音频自动切段阈值
CHUNK_THRESHOLD_SEC = 450  # > 7.5 分钟自动切段
CHUNK_SEGMENT_SEC = 300    # 每段 5 分钟
MAX_FILE_BYTES = 500 * 1024 * 1024  # 讯飞单文件 ≤500MB

# ffmpeg/ffprobe 路径（项目内副本 + 已知 skill 路径 + PATH fallback）
_FFMPEG_PATHS = [
    r"E:\000~\YilanChengWen-src\ffmpeg\ffmpeg.exe",
    r"E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\runtime\ffmpeg.exe",
]
_FFPROBE_PATHS = [
    r"E:\000~\YilanChengWen-src\ffmpeg\ffprobe.exe",
    r"E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\runtime\ffprobe.exe",
]

# 讯飞任务状态码
_STATUS_PROCESSING = frozenset({0, 1, 2, 3, 4})  # 仍在处理中
_STATUS_UPLOADED = 9                                # 上传完成未出结果
_STATUS_SUCCESS = 5                                 # 处理完成

# 模块级 cache：HTTP Session 复用（多段上传共用连接池）
_cached_session: Optional[object] = None


# ============ 工具：ffmpeg / ffprobe / 切段 ============

def _find_ffmpeg() -> Optional[str]:
    """找 ffmpeg 可执行文件路径（项目内副本优先，PATH fallback）。"""
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


def _probe_audio_duration(audio_path: str) -> float:
    """用 ffprobe 探测音频时长（秒）。失败返回 -1。"""
    ffprobe = _find_ffprobe()
    if not ffprobe:
        logger.warning("ffprobe 找不到，跳过时长探测（不切段）")
        return -1.0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", audio_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"ffprobe 失败 (code={result.returncode})，不切段")
            return -1.0
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning(f"ffprobe 异常: {e}，不切段")
        return -1.0


def _do_split(audio_path: str, segment_sec: int, total_duration: float) -> List[Path]:
    """ffmpeg 切段到 temp 目录，返回所有段路径。"""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg 找不到，跳过切段（直接转写原文件）")
        return [Path(audio_path)]
    temp_dir = Path(tempfile.mkdtemp(prefix="xf_asr_chunks_"))
    chunk_paths = []
    i = 0
    while i < int(total_duration):
        out_path = temp_dir / f"chunk_{i:06d}.wav"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", audio_path,
            "-ss", str(i), "-t", str(segment_sec),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.warning(f"ffmpeg 切段失败 (chunk {i}): {result.stderr[:200]}")
                break
        except subprocess.TimeoutExpired:
            logger.warning(f"ffmpeg 切段超时 (chunk {i})")
            break
        if not out_path.exists() or out_path.stat().st_size < 1000:
            # 最后一段可能 < segment_sec，文件太小说明切完了
            break
        chunk_paths.append(out_path)
        i += segment_sec
    if not chunk_paths:
        logger.warning("切段失败，回退到原文件")
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except OSError:
            pass
        return [Path(audio_path)]
    logger.info(f"已切 {len(chunk_paths)} 段到 {temp_dir}")
    return chunk_paths


def _split_audio_for_long(
    audio_path: str,
    segment_sec: int = CHUNK_SEGMENT_SEC,
) -> List[Path]:
    """长音频处理策略。

    2026-09-30 简化：客户端不再切段。
    讯飞 raasr.xfyun.cn/v2/api 服务端自己会处理长音频（官方文档明示 ≤500MB
    单文件直传即可），客户端切段反而引入风险——任何一段失败都整体失败，
    切段边界可能引入静音或 wav 头丢失，且增加 HTTP 请求次数。

    保留函数签名 + threshold 常量以便未来重新启用（如果 server 端改了限制）。

    Returns:
        永远 [Path(audio_path)]——不切段
    """
    audio_path_obj = Path(audio_path)
    duration = _probe_audio_duration(audio_path_obj)
    if duration > 0:
        logger.info(
            f"音频时长 {duration:.1f}s——讯飞 raasr v2 API 服务端自动处理长音频，"
            f"客户端不再切段（{audio_path_obj.name}）"
        )
    else:
        logger.info(f"讯飞 raasr v2 API 直接上传：{audio_path_obj.name}")
    return [audio_path_obj]


def _cleanup_chunks(chunk_paths: List[Path]) -> None:
    """清理切段产生的 temp 目录和文件。"""
    if not chunk_paths:
        return
    temp_dirs = {p.parent for p in chunk_paths if p.parent.name.startswith("xf_asr_chunks_")}
    for d in temp_dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _cleanup_orphaned_chunks_on_exit() -> None:
    """2026-09-27: 进程退出时清残留 xf_asr_chunks_* tempdir。

    正常 finally 块会清，但 GUI 被强杀/任务管理器结束/断电时 finally 不跑，
    tempdir 永久残留 %TEMP%。atexit 在 Python 进程正常退出时一定会跑。
    """
    cleaned = 0
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "xf_asr_chunks_*")):
        try:
            shutil.rmtree(d, ignore_errors=True)
            cleaned += 1
        except Exception:
            pass
    if cleaned:
        logger.info(f"[atexit] 清理残留切段目录: {cleaned} 个")


atexit.register(_cleanup_orphaned_chunks_on_exit)


# ============ 签名 + API 调用 ============

def _sign_request(app_id: str, secret_key: str, ts: int) -> str:
    """生成讯飞长语音 API 签名（MD5 + HMAC-SHA1 + base64）。

    官方算法（参考 caitongbo/Speech-to-Text Ifasr_new.py，URL 是 raasr.xfyun.cn/v2/api）：
        1. md5_hex  = MD5(app_id + ts).hexdigest()           # 32-hex msg
        2. raw     = HMAC-SHA1(secret_key, md5_hex.bytes)    # 用 MD5 hex 当 msg
        3. signa   = base64(raw).decode()

    2026-09-27 订正：之前误写成 base64(HMAC-SHA1(secret, f"{app_id}{ts}"))
    漏了 MD5 一步——讯飞服务端返回 26601 signa verify fail。已按官方 demo 修复。

    注意：baseString 不是 app_id+ts 原串，而是 app_id+ts 先 MD5 一次。
    与「实时语音转写 WebSocket」的 'api_key={key}&timestamp={ts}' 完全不同。
    """
    md5 = hashlib.md5()
    md5.update(f"{app_id}{ts}".encode("utf-8"))
    md5_hex = md5.hexdigest().encode("utf-8")  # 32-hex msg
    sig = hmac.new(secret_key.encode("utf-8"), md5_hex, hashlib.sha1).digest()
    return base64.b64encode(sig).decode("utf-8")


def _get_session():
    """获取 / 创建 requests.Session（连接池复用）。"""
    global _cached_session
    if _cached_session is None:
        import requests  # 延迟 import：避免未装 requests 时启动报错
        _cached_session = requests.Session()
    return _cached_session


def _release_cached_session() -> None:
    """释放 Session（多段转写完成后调用，避免连接占资源）。"""
    global _cached_session
    if _cached_session is not None:
        try:
            _cached_session.close()
        except Exception:
            pass
        _cached_session = None


def _upload_audio(
    audio_path: str,
    app_id: str,
    secret_key: str,
    language: str = "cn",
) -> str:
    """上传音频到讯飞 API，返回 task_id。

    讯飞 raasr.xfyun.cn/v2/api/upload 的真实协议 —— 与 caitongbo/Speech-to-Text
    Ifasr_new.py 等官方/民间 Python demo 一致：

      1. 所有鉴权/元参数 (appId / signa / ts / fileSize / fileName /
         duration / language / sliceSize / ...) 走 URL **query string**，
         拼成 `…/upload?appId=xxx&signa=yyy&ts=zzz&...`
      2. 音频二进制 bytes 直接走 HTTP body（`requests.post(data=...)`）
      3. Content-Type header = `application/json`

    **不要**用 `requests.post(files={"data": ...})`！requests 会自动生成
    `multipart/form-data; boundary=xxx` 格式，与讯飞期望的「query string +
    raw body」完全不一致 → 服务端返回 code=26600
    「转写业务通用错误 | 音频需上传二进制音频流数据 | 音频使用表单方式上传」。

    与实时语音识别（WebSocket）鉴权不同，HTTP 长语音 REST API 走 query string。

    历史：2026-09-30 修正。之前用 multipart files，被 26600 挡住。
    参照多 demo（caitongbo/Speech-to-Text Ifasr_new.py / richice/-/xf_lfasr.py /
    JianyaoChen/video-carrier/xf_lfasr.py / lanbinleo/bili2text/xunfei.py）
    一致方案。

    Raises:
        RuntimeError: 上传失败（凭证错 / 文件过大 / 网络问题）
    """
    session = _get_session()
    ts = int(time.time())
    signa = _sign_request(app_id, secret_key, ts)

    audio_size = Path(audio_path).stat().st_size
    if audio_size > MAX_FILE_BYTES:
        raise RuntimeError(
            f"音频文件过大 ({audio_size / 1024 / 1024:.0f}MB)，"
            f"讯飞长语音 API 单文件上限 500MB（应自动切段却失败？检查 ffmpeg）"
        )

    # === 探测时长（用于 form_data 的 duration 字段）===
    # demo 一致必传 duration 参数（单位秒，估算转写耗时用）
    # ⚠️ duration 不准可能让 server 误判超时
    audio_duration = _probe_audio_duration(Path(audio_path))

    # === 关键：所有鉴权/元参数走 URL query string，不要放 body ===
    form_data = {
        "appId": app_id,
        "signa": signa,
        "ts": str(ts),
        "fileSize": str(audio_size),
        "fileName": Path(audio_path).name,
        "sliceSize": str(audio_size),
        "language": language,
        "duration": str(int(audio_duration)) if audio_duration > 0 else "60",
    }
    upload_url_with_query = f"{_UPLOAD_URL}?{urllib.parse.urlencode(form_data)}"

    logger.info(
        f"上传音频到讯飞: {audio_path} "
        f"({audio_size / 1024 / 1024:.2f}MB, language={language})"
    )

    # === 音频二进制走 raw body（不是 multipart files）===
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    resp = session.post(
        upload_url_with_query,
        headers={"Content-Type": "application/json"},
        data=audio_bytes,
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"讯飞 upload HTTP {resp.status_code}: {resp.text[:300]}"
        )
    try:
        result = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"讯飞 upload 返回非 JSON (HTTP {resp.status_code}): {resp.text[:300]}"
        ) from exc
    if result.get("code") != "000000":
        raise RuntimeError(
            f"讯飞 upload 失败 (code={result.get('code')}): "
            f"{result.get('descInfo', '')}"
        )
    task_id = result["content"]["taskId"]
    logger.info(f"上传成功，task_id={task_id}")
    return task_id


def _parse_result_content(content: dict) -> str:
    """解析 getResult 返回的 content，按时间顺序拼接文本。

    讯飞 v2 API 的 content.resultMap 是个 dict：
    - key = 句子 ID（"1", "2", "3", ... 按时间排序）
    - value = {"text": "...", "begin_time": ms, "end_time": ms, ...}
    """
    result_map = content.get("resultMap", {})
    text_parts = []
    for sid in sorted(result_map.keys(), key=lambda k: int(k) if str(k).isdigit() else 0):
        text_parts.append(result_map[sid].get("text", ""))
    return "\n".join(text_parts)


def _poll_result(
    task_id: str,
    app_id: str,
    secret_key: str,
    max_wait: int = 600,
    poll_interval: float = 5.0,
) -> str:
    """轮询讯飞结果直到完成，返回转写文本。

    任务状态码：
    - 0-4: 处理中
    - 5:   处理完成（成功）
    - 9:   上传完成但未出结果（继续轮询）

    Raises:
        RuntimeError: 超时 / 任务失败
    """
    session = _get_session()
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        ts = int(time.time())
        signa = _sign_request(app_id, secret_key, ts)
        form_data = {
            "appId": app_id,
            "signa": signa,
            "ts": str(ts),
            "taskId": task_id,
        }
        try:
            resp = session.post(_RESULT_URL, data=form_data, timeout=30)
        except Exception as exc:
            # 网络抖动：WARNING + 重试，不立即抛
            logger.warning(f"poll #{attempt} 网络异常: {exc}，重试")
            time.sleep(poll_interval)
            continue
        if resp.status_code != 200:
            logger.warning(f"poll #{attempt} HTTP {resp.status_code}，重试")
            time.sleep(poll_interval)
            continue
        try:
            result = resp.json()
        except json.JSONDecodeError:
            logger.warning(f"poll #{attempt} 非 JSON 响应，重试")
            time.sleep(poll_interval)
            continue
        if result.get("code") != "000000":
            raise RuntimeError(
                f"讯飞 getResult 失败 (code={result.get('code')}): "
                f"{result.get('descInfo', '')}"
            )
        status = int(result["content"]["taskStatus"])
        logger.info(f"poll #{attempt}: status={status}")
        if status in _STATUS_PROCESSING or status == _STATUS_UPLOADED:
            time.sleep(poll_interval)
            continue
        if status == _STATUS_SUCCESS:
            return _parse_result_content(result["content"])
        raise RuntimeError(f"未知任务状态: status={status}")
    raise RuntimeError(
        f"讯飞轮询超时 ({max_wait}s, {attempt} 次尝试)，"
        f"task_id={task_id}"
    )


def _validate_credentials(app_id: str, secret_key: str) -> None:
    """校验讯飞凭证非空。

    长语音转写鉴权只需 APPID + SecretKey 两件套（短音频才要 APIKey）。
    """
    missing = []
    if not app_id:
        missing.append("app_id")
    if not secret_key:
        missing.append("secret_key")
    if missing:
        raise RuntimeError(
            f"讯飞长语音鉴权需要 2 项凭证（APPID + SecretKey），缺少: {', '.join(missing)}。"
            f"请到 https://www.xfyun.cn/ 注册 → 控制台 → 「语音转写」服务创建应用获取。"
            f"或勾「Mock 模式」跳过真实 API（用于本地调试）。"
        )


def _mock_transcribe(audio_path: str) -> str:
    """Mock 转写：返回 fake 中文文本（基于音频时长生成）。

    仅用于本地开发 / UI 验证：
    - 不调网络、不需要凭证
    - 文本内容固定，每 30 秒重复一行
    - 真实接入凭证后，config 设 mock=false 即可切换真实 API
    """
    duration = _probe_audio_duration(audio_path)
    if duration > 0:
        n = max(1, int(duration / 30))
    else:
        # 探测失败（如没有 ffprobe）：默认 5 行
        n = 5
    sentence = (
        "（这是讯飞听见 mock 模式的 fake 转写文本。"
        "真实接入凭证后关闭 mock 即可调用云端 API。）"
    )
    return "\n".join([sentence] * min(n, 50))  # 上限 50 行


# ============ 主入口 ============

def _transcribe_one(
    audio_path: str,
    app_id: str,
    secret_key: str,
    language: str,
    max_wait: int,
) -> str:
    """单文件真实 API：upload + poll。"""
    task_id = _upload_audio(
        audio_path, app_id, secret_key, language=language
    )
    return _poll_result(
        task_id, app_id, secret_key, max_wait=max_wait
    )


def transcribe_audio_with_xf_asr(audio_path: str, config: dict) -> str:
    """讯飞听见云端 ASR 主入口。

    Args:
        audio_path: 音频文件路径（wav/mp3/m4a/flac 等）
        config: config.json 中 transcribe.xf_asr 块

    Returns:
        转写文本

    Raises:
        RuntimeError: 凭证缺失 / API 调用失败 / 文件过大

    Mock 行为：
        - mock=true 或凭证缺失 → _mock_transcribe(audio_path)
        - 真实凭证 + mock=false → 调讯飞 API

    长音频处理：
        - 时长 ≤ 7.5 分钟 → 单文件上传
        - 时长 > 7.5 分钟 → 自动切 5 分钟一段，**逐段** upload+poll，最后拼接
    """
    cfg = config or {}
    mock = bool(cfg.get("mock", False))
    app_id = (cfg.get("app_id") or "").strip()
    secret_key = (cfg.get("secret_key") or "").strip()
    language = (cfg.get("language") or "cn").strip()
    max_wait = int(cfg.get("max_wait_seconds") or 600)

    # Mock 模式：分三种情况打不同日志（让用户一眼看出走了哪条分支）
    #   1. mock=true + 凭证齐全 → 用户显式勾选 mock（可能忘了取消）→ WARNING
    #   2. mock=true + 凭证缺失 → 用户显式勾选 mock → INFO
    #   3. mock=false + 凭证缺失 → 自动降级 mock（凭证缺失自动 fallback）→ WARNING
    if mock or not (app_id and secret_key):
        if mock and app_id and secret_key:
            # 情况 1：凭证齐全但勾了 mock（用户可能忘了取消勾选）
            logger.warning(
                "xf_asr 凭证已配置（APPID + SecretKey）但 Mock 模式仍勾选——"
                "将走 Mock 不调真实 API。如需真实 API，请取消 Mock 勾选。"
            )
        elif mock:
            # 情况 2：用户显式勾选 mock（无论凭证）
            logger.info(
                "xf_asr Mock 模式（显式勾选）—— 不调真实 API，返回 fake 中文文本。"
            )
        else:
            # 情况 3：凭证缺失自动降级
            logger.warning(
                "xf_asr 凭证缺失，自动降级 Mock 模式（不调网络、不需要 key）。"
                "如需真实 API，请配置 app_id + secret_key 并取消 mock 勾选。"
            )
        return _mock_transcribe(audio_path)

    # 真实 API 模式
    _validate_credentials(app_id, secret_key)
    audio_path_obj = Path(audio_path)
    chunk_paths = _split_audio_for_long(audio_path)
    is_chunked = len(chunk_paths) > 1 or chunk_paths[0] != audio_path_obj
    start = time.time()
    try:
        if is_chunked:
            logger.info(
                f"讯飞长音频转写: {audio_path}（已切 {len(chunk_paths)} 段）"
            )
            texts = []
            for i, chunk in enumerate(chunk_paths, 1):
                logger.info(
                    f"转写第 {i}/{len(chunk_paths)} 段: {chunk.name}"
                )
                texts.append(
                    _transcribe_one(
                        str(chunk),
                        app_id,
                        secret_key,
                        language,
                        max_wait,
                    )
                )
            result = "\n".join(texts)
            logger.info(
                f"讯飞转写完成（{len(chunk_paths)} 段拼接, "
                f"耗时 {format_time(time.time() - start)}，共 {len(result)} 字符）"
            )
            return result
        result = _transcribe_one(
            audio_path, app_id, secret_key, language, max_wait
        )
        logger.info(
            f"讯飞转写完成 (耗时 {format_time(time.time() - start)}，"
            f"共 {len(result)} 字符)"
        )
        return result
    finally:
        if is_chunked:
            _cleanup_chunks(chunk_paths)
        # 短音频不释放 session（单次任务复用即可）；长音频末尾释放更稳
        if is_chunked:
            _release_cached_session()