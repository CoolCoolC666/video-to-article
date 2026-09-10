"""Qwen3-ASR 后端 — 通过 qwen_asr 官方包调用，绕开 funasr 嵌入版 bug。

用 qwen_asr 0.0.6+ 的 Qwen3ASRModel 高层 API（不直接碰 transformers），
绕开 YilanChengWen 0.4.5 PyInstaller 嵌入 funasr 模块的 jit 失败 +
'fun_asr_nano.model' name 'name' is not defined 等 bug。

config.json 用法（transcribe 块加）：
    "asr_engine": "qwen_asr",
    "qwen_asr": {
        "model_id": "Qwen/Qwen3-ASR-0.6B",   # 或 1.7B
        "context_file": "E:/.../starrail.txt", # 可选，专名偏置
        "language": "Chinese",
        "hf_home": "E:/AI_Models/Qwen3-ASR"   # 无空格路径
    }

2026-09-08: 加 module-level cache + _release_cached_model()
- 一次加载、process_batch 末尾释放，13 个视频省 1 分钟加载
- 避免每视频重新加载 5s × 13 = 65s 浪费
- GUI 取消时也调 _release_cached_model() 立即释放
"""
from __future__ import annotations

import atexit
import gc
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from ..logging_config import configure_logging
from ..text_utils import format_time, traditional_to_simplified

logger = configure_logging()


def _cleanup_orphaned_chunks_on_exit() -> None:
    """2026-09-10: 进程退出时清残留 qwen_asr_chunks_* tempdir。

    正常 finally 块会清，但 GUI 被强杀/任务管理器结束/断电时 finally 不跑，
    tempdir 永久残留 %TEMP%。atexit 在 Python 进程正常退出时一定会跑。
    """
    cleaned = 0
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "qwen_asr_chunks_*")):
        try:
            shutil.rmtree(d, ignore_errors=True)
            cleaned += 1
        except Exception:
            pass
    if cleaned:
        logger.info(f"[atexit] 清理残留切段目录: {cleaned} 个")


atexit.register(_cleanup_orphaned_chunks_on_exit)

# 2026-09-08: model-level cache
# 第一次调 transcribe_audio_with_qwen_asr 时加载，后续复用。
# process_batch 末尾调 _release_cached_model() 释放（避免长期占 GPU）。
_cached_model = None
_cached_model_id = None
# 2026-09-09: 记录实际加载用的 device，决定 cache 复用时是否要重载
# （auto → cuda 在用户把 GPU 干掉后是不同 device，cache key 必须包含）
_cached_device = None

# 2026-09-09: GPU 1 掉驱动时的 fallback 策略
# - device: "auto"（默认）→ CUDA 可用就用 CUDA:0 + max_memory={0:"7GiB"}；不可用自动降级 CPU
# - device: "cuda" → 强制 CUDA:0；不可用报错 + 提示降级
# - device: "cpu"  → 强制 CPU；适合 GPU 驱动挂了 / 玩游戏抢卡 / OOM 复测
_VALID_DEVICES = {"auto", "cuda", "cpu"}

# 2026-09-09: Qwen3-ASR 0.0.6 实测支持的语言（model.transcribe 会校验）。
# 历史 GUI 误加的 "Auto" 不在列表中 → 兜底 Chinese。
# 实测不支持 "Auto" / "auto" / "" / 中文别名（"中文"/"英文" 也不行，必须英文名）
_SUPPORTED_LANGS = frozenset({
    "Chinese", "English", "Cantonese", "Arabic", "German", "French",
    "Spanish", "Portuguese", "Indonesian", "Italian", "Korean", "Russian",
    "Thai", "Vietnamese", "Japanese", "Turkish", "Hindi", "Malay",
    "Dutch", "Swedish", "Danish", "Finnish", "Polish", "Czech",
    "Filipino", "Persian", "Greek", "Romanian", "Hungarian", "Macedonian",
})

# 2026-09-08: 长音频自动切段
# 阈值：超过 CHUNK_THRESHOLD_SEC 的音频自动切段
# 默认 7.5 分钟 (450 秒) — 12+ 分钟长音频触发，< 7.5 分钟不切
CHUNK_THRESHOLD_SEC = 450
# 每段最大时长：5 分钟 (300 秒) — 切段后单段不再触发 OOM
CHUNK_SEGMENT_SEC = 300
# ffmpeg/ffprobe 路径（项目内副本 + skill runtime 副本）
_FFMPEG_PATHS = [
    r"E:\000~\YilanChengWen-src\ffmpeg\ffmpeg.exe",
    r"E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\runtime\ffmpeg.exe",
]
_FFPROBE_PATHS = [
    r"E:\000~\YilanChengWen-src\ffmpeg\ffprobe.exe",
    r"E:\AI Agent\Data list\.minimax\skills\yilan-chengwen-asr\runtime\ffprobe.exe",
]


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


def _split_audio_for_long(audio_path: str, segment_sec: int = CHUNK_SEGMENT_SEC) -> List[Path]:
    """长音频自动切段（ffmpeg）。

    - 时长 <= CHUNK_THRESHOLD_SEC (7.5 分钟) → 不切段，返回 [audio_path]
    - 时长 > CHUNK_THRESHOLD_SEC → 切到 temp 目录，每段 segment_sec (5 分钟)
    - 输出 wav 16kHz 单声道（Qwen3-ASR 期望格式）
    - 切完返回所有段路径（调用方转写后清理 temp 目录）
    """
    duration = _probe_audio_duration(audio_path)
    if duration <= 0:
        # 探测失败：保险起见**仍切**（防止 60 分钟 OOM），用 5 分钟段
        logger.warning(f"无法探测时长，强制按 {segment_sec}s 切段（防 OOM）")
        return _do_split(audio_path, segment_sec, 99999)  # 上限 99999s
    if duration <= CHUNK_THRESHOLD_SEC:
        logger.info(f"音频时长 {duration:.1f}s <= {CHUNK_THRESHOLD_SEC}s，不切段")
        return [Path(audio_path)]
    logger.info(f"音频时长 {duration:.1f}s > {CHUNK_THRESHOLD_SEC}s，自动切段（每段 {segment_sec}s）")
    return _do_split(audio_path, segment_sec, duration)


def _do_split(audio_path: str, segment_sec: int, total_duration: float) -> List[Path]:
    """ffmpeg 切段到 temp 目录，返回所有段路径。"""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg 找不到，跳过切段（直接转写原文件）")
        return [Path(audio_path)]
    temp_dir = Path(tempfile.mkdtemp(prefix="qwen_asr_chunks_"))
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
            # 文件太小说明音频已切完（最后一段可能 < segment_sec）
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


def _cleanup_chunks(chunk_paths: List[Path]) -> None:
    """清理切段产生的 temp 目录和文件。"""
    if not chunk_paths:
        return
    # 找到 temp_dir（所有 chunk 都在同一个 mkdtemp 目录下）
    temp_dirs = {p.parent for p in chunk_paths if p.parent.name.startswith("qwen_asr_chunks_")}
    for d in temp_dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _read_context(context_file: Optional[str]) -> Optional[str]:
    if not context_file:
        return None
    p = Path(context_file)
    if not p.exists():
        logger.warning(f"context_file 不存在: {context_file}  →  跑无 context 模式")
        return None
    text = p.read_text(encoding="utf-8").strip()
    logger.info(f"加载 context: {context_file}  ({len(text)} 字符)")
    return text


def _resolve_device(device_pref: str) -> tuple[str, dict | None]:
    """根据 user 偏好 + 实际 GPU 可用性，返回 (effective_device, max_memory)。

    2026-09-09 加：GPU 驱动掉了 / CUDA 不可用时自动降级。

    返回：
    - effective_device: "cuda:0" / "cpu"
    - max_memory: {0: "7GiB"} / None
        - None = 不传 max_memory（CPU 模式 + 强制全 GPU 都行）
        - {0: "7GiB"} = 限 GPU 0 最多 7 GiB（防 RTX 5070 Ti 12GB OOM）

    规则：
    - device_pref="auto":
        - CUDA 可用 + device_count > 0 → cuda:0 + {0: "7GiB"}
        - 否则 → 自动降级 cpu + max_memory=None + WARNING 日志
    - device_pref="cuda":
        - CUDA 可用 + device_count > 0 → cuda:0 + {0: "7GiB"}
        - 否则 → 抛 RuntimeError（让上层 _get_or_load_model 决定是否重试 cpu）
    - device_pref="cpu":
        - 强制 cpu + max_memory=None
    """
    import torch

    pref = (device_pref or "auto").lower()
    if pref not in _VALID_DEVICES:
        logger.warning(f"未知 device={device_pref}，回退到 auto")
        pref = "auto"

    cuda_ok = False
    device_count = 0
    try:
        cuda_ok = bool(torch.cuda.is_available())
        device_count = int(torch.cuda.device_count()) if cuda_ok else 0
    except Exception as e:
        # 驱动挂了 / CUDA init 失败时 torch.cuda.is_available() 可能直接抛
        logger.warning(f"探测 CUDA 失败: {e} → 视作 CUDA 不可用")
        cuda_ok = False
        device_count = 0

    if pref in ("auto", "cuda"):
        if cuda_ok and device_count > 0:
            return "cuda:0", {0: "7GiB"}
        if pref == "cuda":
            raise RuntimeError(
                "device='cuda' 但 CUDA 不可用（驱动掉了？torch 没装 CUDA？）。\n"
                "修复：1) nvidia-smi 查 GPU 状态  2) 重装 NVIDIA 驱动  "
                "3) 或临时把 device 改成 'auto' / 'cpu'"
            )
        # auto + 不可用 → 静默降级 + WARNING
        logger.warning(
            "⚠ Qwen3-ASR device='auto' 但 CUDA 不可用（驱动掉了？），"
            "自动降级到 CPU 模式。1.7B 在 CPU 上会比 GPU 慢约 5-10x，"
            "建议切到 0.6B 模型以提速。"
        )
        return "cpu", None

    # pref == "cpu"
    return "cpu", None


def _get_or_load_model(model_id: str, hf_home: str, device: str = "auto"):
    """获取或加载 Qwen3-ASR 模型。model_id + device 不变时复用 cache。

    行为：
    - cache 空：加载 + 存 cache
    - cache 命中（model_id + device 一致）：直接返回，不重新加载
    - cache 不命中（model_id 或 device 变了）：释放旧 cache + 重新加载

    2026-09-09 加：device fallback
    - device="auto" + CUDA 不可用 → 自动降级 cpu
    - device="cuda" + CUDA 不可用 → 抛错
    - device="cpu" → 强制 CPU（适合 GPU 驱动掉了 / 玩游戏抢卡）
    - 加载失败（CUDA OOM / 驱动问题）→ 自动重试 cpu 一次 + WARNING
    """
    global _cached_model, _cached_model_id, _cached_device

    if (
        _cached_model is not None
        and _cached_model_id == model_id
        and _cached_device == device
    ):
        logger.info(
            f"复用 Qwen3-ASR cache: {model_id} (device={device}, 跳过 5s 加载)"
        )
        return _cached_model

    # 释放旧 cache（model_id 或 device 切换时）
    if _cached_model is not None:
        logger.warning(
            f"Qwen3-ASR 配置切换: {_cached_model_id}/{_cached_device} → "
            f"{model_id}/{device}，释放旧 cache"
        )
        _release_cached_model()

    # qwen_asr 0.0.6 顶层直接 export 了 Qwen3ASRModel
    from qwen_asr import Qwen3ASRModel

    # 模型下到无空格纯英文路径（绕开之前 funasr 的空格坑）
    os.environ.setdefault("HF_HOME", hf_home)
    os.environ.setdefault("TRANSFORMERS_CACHE", str(Path(hf_home) / "hub"))
    # 切到国内 HF 镜像：避免 huggingface.co SSL 证书问题 + 加快下载
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    # 预下载所有模型权重到本地缓存（避免 from_pretrained 下载 + device_map 分配混合的延迟）
    local_dir = str(Path(hf_home) / model_id.replace("/", "_"))
    # 2026-09-07 增强：如果 local_dir 已有完整 safetensors，跳过 snapshot_download
    # （之前 huggingface_hub 校验会卡 92%，浪费 5+ 分钟）
    safetensors_files = list(Path(local_dir).glob("*.safetensors"))
    if safetensors_files and all(f.stat().st_size > 100_000_000 for f in safetensors_files):
        logger.info(
            f"local_dir 已有 {len(safetensors_files)} 个 safetensors "
            f"({sum(f.stat().st_size for f in safetensors_files) / 1024 / 1024:.0f} MB)，"
            f"跳过 snapshot_download"
        )
    else:
        start = time.time()
        logger.info(f"预下载 Qwen3-ASR ({model_id}) 所有权重到 {hf_home} ...")
        from huggingface_hub import snapshot_download
        local_dir = snapshot_download(
            repo_id=model_id,
            cache_dir=str(Path(hf_home) / "hub"),
            local_dir=local_dir,
            # 不要 symlinks（Windows 不支持）
            local_dir_use_symlinks=False,
            # 显式关掉进度条干扰
            tqdm_class=None,
        )
        logger.info(f"预下载完成 (耗时: {format_time(time.time() - start)})，本地路径: {local_dir}")

    # 解析 device + max_memory（含 fallback 探测）
    import torch
    effective_device, max_mem = _resolve_device(device)

    # 用本地路径加载（不再走网络）
    start = time.time()
    logger.info(
        f"加载 Qwen3-ASR (本地 {local_dir}, device={effective_device}, "
        f"max_memory={max_mem})..."
    )
    # from_pretrained 是类方法，内部走 transformers AutoModel.from_pretrained
    # device_map / dtype 通过 **kwargs 透传给底层 transformers
    load_kwargs = {
        "device_map": effective_device,
        "dtype": torch.bfloat16,
        "max_inference_batch_size": 1,  # 短音频不开 batch，避免 OOM
    }
    if max_mem is not None:
        load_kwargs["max_memory"] = max_mem

    try:
        model = Qwen3ASRModel.from_pretrained(local_dir, **load_kwargs)
    except Exception as primary_err:
        # 2026-09-09 错误兜底：CUDA 加载失败（驱动挂 / OOM / no device）→ 重试 CPU
        if effective_device != "cpu" and device in ("auto", "cuda"):
            logger.warning(
                f"⚠ Qwen3-ASR 在 {effective_device} 上加载失败: {primary_err}\n"
                f"自动重试 CPU 模式（user 已设 device='{device}'）..."
            )
            try:
                _release_cached_model()  # 释放部分加载残留
            except Exception:
                pass
            try:
                model = Qwen3ASRModel.from_pretrained(
                    local_dir,
                    device_map="cpu",
                    dtype=torch.bfloat16,
                    max_inference_batch_size=1,
                )
                effective_device = "cpu"
                logger.info("CPU 重试成功（已静默降级）")
            except Exception as cpu_err:
                # CPU 也不行 → 把 primary 错也带出来方便诊断
                raise RuntimeError(
                    f"Qwen3-ASR 加载失败：GPU 模式 {primary_err}；CPU 重试 {cpu_err}"
                ) from cpu_err
        else:
            raise
    logger.info(f"模型加载完成 (耗时: {format_time(time.time() - start)}, device={effective_device})")

    _cached_model = model
    _cached_model_id = model_id
    _cached_device = device
    return model


def _release_cached_model():
    """释放 Qwen3-ASR cache + 清空 CUDA 缓存。

    调用时机：
    - process_batch 末尾（try/finally 保证异常时也释放）
    - GUI 取消时（用户点停止按钮）
    - model_id 切换时（_get_or_load_model 内部）
    - 2026-09-10: GUI 手动点「释放 ASR 模型」按钮（让出显存给游戏/OCR/SD）
    """
    global _cached_model, _cached_model_id
    if _cached_model is None:
        return
    try:
        del _cached_model
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info("已释放 Qwen3-ASR cache + 清空 CUDA 缓存")
    except Exception as e:
        logger.warning(f"释放 Qwen3-ASR cache 失败: {e}")
    finally:
        _cached_model = None
        _cached_model_id = None


def get_cached_model_id() -> Optional[str]:
    """返回当前缓存的 Qwen3-ASR 模型 ID（None = 未加载）。

    2026-09-10 加：GUI 状态栏用，显示模型是否在显存里。
    不返回模型对象本身（避免外部误释放/误用），只返回 ID 字符串。
    """
    return _cached_model_id if _cached_model is not None else None


def transcribe_audio_with_qwen_asr(audio_path: str, config: dict) -> str:
    """直接调 Qwen3-ASR（通过 qwen_asr 包），不经过 funasr。

    参数 config 是 config.json 里 transcribe.qwen_asr 整块，可选。

    2026-09-08 改：model 走 cache（_get_or_load_model），不每视频都重加载。
    释放由 process_batch 末尾的 _release_cached_model() 统一处理。

    2026-09-08 改：长音频自动切段（> 7.5 分钟切 5 分钟一段）— 解决 12+ 分钟 OOM

    2026-09-09 改：device 透传（auto / cuda / cpu），支持 GPU 驱动挂了时静默降级
    """
    cfg = config or {}
    model_id = cfg.get("model_id", "Qwen/Qwen3-ASR-0.6B")
    language = cfg.get("language", "Chinese")
    # 2026-09-09: Qwen3-ASR 0.0.6 不支持 "Auto"（实测会报 Unsupported language），
    # 历史 GUI 误加了 Auto 选项，磁盘上若有此值必须兜底为 Chinese + WARNING
    if language not in _SUPPORTED_LANGS:
        logger.warning(
            f"⚠ qwen_asr.language='{language}' 不在 Qwen3-ASR 支持列表中，"
            f"已兜底回 'Chinese'。支持列表见 _SUPPORTED_LANGS。"
        )
        language = "Chinese"
    context_file = cfg.get("context_file")
    hf_home = cfg.get("hf_home", r"E:\AI_Models\Qwen3-ASR")
    device = cfg.get("device", "auto")

    model = _get_or_load_model(model_id, hf_home, device=device)
    context = _read_context(context_file)

    # 2026-09-08: 长音频自动切段 (> 7.5 分钟切 5 分钟一段)
    audio_path_obj = Path(audio_path)
    chunk_paths = _split_audio_for_long(audio_path)
    is_chunked = len(chunk_paths) > 1 or chunk_paths[0] != audio_path_obj
    should_cleanup = is_chunked

    try:
        if is_chunked:
            logger.info(f"开始转写: {audio_path}（已切 {len(chunk_paths)} 段）")
        else:
            logger.info(f"开始转写: {audio_path}")
        t0 = time.time()
        text_parts: list[str] = []
        for idx, chunk_path in enumerate(chunk_paths, 1):
            if is_chunked:
                logger.info(f"  转写段 {idx}/{len(chunk_paths)}: {chunk_path.name}")
            # transcribe 返回 List[ASRTranscription]，每条 .text 是转写文本
            results = model.transcribe(
                audio=str(chunk_path),
                context=context or "",
                language=language,
            )
            raw = results[0].text if results else ""
            text_parts.append(raw)
        # 合并所有段
        raw = " ".join(text_parts)
        # 保险清洗：去掉 FunASR 风格 <|...|> 标签（Qwen3-ASR 通常不带）
        text = re.sub(r"<\|[^|]*?\|>", "", raw).strip()
        text = traditional_to_simplified(text)
        logger.info(f"转写完成 (耗时: {format_time(time.time() - t0)}，{len(chunk_paths)} 段)")
    finally:
        if should_cleanup:
            _cleanup_chunks(chunk_paths)

    # 注意：不在这里释放 model（由 process_batch 末尾的 _release_cached_model 统一处理）
    return text
