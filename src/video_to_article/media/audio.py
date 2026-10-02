import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ..data_paths import local_audio_dir
from ..logging_config import configure_logging
from ..paths import AUDIO_EXTENSIONS, FUNASR_MODEL_DIR, VIDEO_EXTENSIONS
from ..text_utils import format_time, import_required, traditional_to_simplified
from ..paths import MODEL_DIR
from .download import download_audio  # re-export for existing imports

logger = configure_logging()

__all__ = [
    "download_audio",
    "prepare_local_audio",
    "extract_audio_from_local_video",
    "transcribe_audio",
    "transcribe_audio_with_whisper",
    "transcribe_audio_with_funasr",
    "transcribe_audio_with_qwen_asr",
    "transcribe_audio_with_xf_asr",
    "extract_funasr_text",
    "format_funasr_speaker_text",
    "resolve_funasr_model_name",
    "resolve_funasr_vad_model_name",
    "resolve_funasr_spk_model_name",
]

FUNASR_MODEL_ALIASES = {
    "sensevoice": "iic/SenseVoiceSmall",
    "sensevoice-small": "iic/SenseVoiceSmall",
    "paraformer": "paraformer-zh",
    "paraformer-zh": "paraformer-zh",
}

# 2026-10-02 新增：说话人分离模型（speaker diarization）
# SenseVoice 自身不产角色号，必须额外挂一个说话人嵌入模型（spk_model）。
# 官方 demo 一致写法：AutoModel(model=..., vad_model="fsmn-vad", spk_model="cam++")
# 关键约束（FunASR 官方文档原文）：「通用 AutoModel 说话人聚类位于 VAD 流水线中，
# 只设置 spk_model 或 return_spk_res=True 不会给直接推理增加说话人分离」
# → 所以 spk_model 必须和 vad_model 一起传（本项目的 vad_model 本来就有）。
#
# ⚠ 关于算力：CAM++ 只是 ~28MB 的说话人嵌入模型（不是生成式大模型），
# CPU 上就能跑，不占 GPU 显存。开启分离的额外开销 ≈ 多一个模型加载 + 一次聚类。
FUNASR_SPK_ALIASES = {
    "cam++": "cam++",
    "campplus": "cam++",
    "camplus": "cam++",
    "": "",
}

# CAM++ 本地快照的 ModelScope 目录名
CAMPLUS_DIR_NAME = "speech_campplus_sv_zh-cn_16k-common"
CAMPLUS_REQUIRED_FILES = ("model.pt", "config.yaml")

# Required beside model.pt for SenseVoice (sentencepiece); used for completeness checks.
SENSEVOICE_REQUIRED_FILES = (
    "model.pt",
    "chn_jpn_yue_eng_ko_spectok.bpe.model",
    "config.yaml",
    "configuration.json",
)


def _path_has_non_ascii(path: Path | str) -> bool:
    try:
        str(path).encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def _patch_inspect_for_funasr_frozen() -> None:
    """PyInstaller freezes modules without .py sources.

    funasr's @tables.register calls inspect.getsourcelines for metadata; that
    raises OSError under frozen builds and aborts registration of SenseVoiceSmall
    etc. Swallow those errors so models still register.
    """
    if not getattr(sys, "frozen", False):
        return
    import inspect

    if getattr(inspect, "_yilan_funasr_source_patch", False):
        return

    _orig_gsl = inspect.getsourcelines
    _orig_gs = inspect.getsource

    def _safe_getsourcelines(obj, *args, **kwargs):
        try:
            return _orig_gsl(obj, *args, **kwargs)
        except (OSError, TypeError, IOError):
            return ([""], 0)

    def _safe_getsource(obj, *args, **kwargs):
        try:
            return _orig_gs(obj, *args, **kwargs)
        except (OSError, TypeError, IOError):
            return ""

    inspect.getsourcelines = _safe_getsourcelines  # type: ignore[assignment]
    inspect.getsource = _safe_getsource  # type: ignore[assignment]
    inspect._yilan_funasr_source_patch = True  # type: ignore[attr-defined]


def _ensure_funasr_core_models_registered() -> None:
    """Force-import inference models needed for SenseVoice (packaged app safety net)."""
    import importlib

    for mod in (
        "funasr.models.sense_voice.model",
        "funasr.models.fsmn_vad_streaming.model",
        "funasr.tokenizer.whisper_tokenizer",
    ):
        try:
            importlib.import_module(mod)
        except Exception as e:
            logger.warning(f"预加载 FunASR 模块失败 {mod}: {e}")


def _project_sensevoice_dir() -> Path:
    return FUNASR_MODEL_DIR / "models" / "iic" / "SenseVoiceSmall"


def _project_vad_dir() -> Path:
    return FUNASR_MODEL_DIR / "models" / "iic" / "speech_fsmn_vad_zh-cn-16k-common-pytorch"


def _project_camplus_dir() -> Path:
    return FUNASR_MODEL_DIR / "models" / "iic" / CAMPLUS_DIR_NAME


def _camplus_complete(dir_path: Path) -> bool:
    return all((dir_path / name).is_file() for name in CAMPLUS_REQUIRED_FILES)


def _sensevoice_complete(dir_path: Path) -> bool:
    return all((dir_path / name).is_file() for name in SENSEVOICE_REQUIRED_FILES)


def _configured_funasr_dir() -> Path | None:
    """User override: env VQE_FUNASR_DIR, then config transcribe.funasr_cache_dir."""
    env = (os.environ.get("VQE_FUNASR_DIR") or os.environ.get("YILAN_FUNASR_DIR") or "").strip()
    if env:
        return Path(env)
    try:
        from ..config import load_config

        tr = (load_config() or {}).get("transcribe") or {}
        raw = str(tr.get("funasr_cache_dir") or tr.get("funasr_dir") or "").strip()
        if raw:
            return Path(raw)
    except Exception:
        pass
    return None


def _same_drive_funasr_root() -> Path | None:
    """Prefer program-drive data folder (avoids filling system C: when app is on D:)."""
    try:
        from ..paths import APP_ROOT

        root_path = Path(APP_ROOT).resolve()
        drive = root_path.drive  # e.g. 'D:'
        if not drive:
            return None
        # D:\YilanChengWenData\models\funasr  — ASCII-only, same volume as exe
        candidate = Path(drive + os.sep) / "YilanChengWenData" / "models" / "funasr"
        if _path_has_non_ascii(candidate):
            return None
        return candidate
    except Exception:
        return None


def _ascii_funasr_root() -> Path:
    """Windows-safe cache root when project path contains non-ASCII (e.g. 中文目录).

    Priority:
      1) 用户自定义（环境变量 / config）
      2) 与程序同盘的 YilanChengWenData\\models\\funasr（推荐，不占系统盘）
      3) %LOCALAPPDATA%\\YilanChengWen\\models\\funasr（回退）
    """
    custom = _configured_funasr_dir()
    if custom is not None:
        root = Path(custom)
        if not _path_has_non_ascii(root):
            root.mkdir(parents=True, exist_ok=True)
            return root
        logger.warning(
            "自定义 FunASR 目录含非 ASCII 字符，已忽略: %s（SentencePiece 无法读取）",
            root,
        )

    same_drive = _same_drive_funasr_root()
    if same_drive is not None:
        same_drive.mkdir(parents=True, exist_ok=True)
        return same_drive

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "C:\\YilanChengWen"
    root = Path(base) / "YilanChengWen" / "models" / "funasr"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_windows_junction(link: Path, target: Path) -> bool:
    """Create a directory junction (no admin) so native code can open via ASCII path."""
    target = target.resolve()
    link.parent.mkdir(parents=True, exist_ok=True)

    if link.exists() or link.is_symlink():
        try:
            if (link / "models").is_dir() or link.resolve() == target:
                return True
            # Empty placeholder dir blocks mklink /J
            if link.is_dir() and not any(link.iterdir()):
                link.rmdir()
            else:
                return (link / "models").is_dir()
        except OSError:
            return False

    try:
        if sys.platform.startswith("win"):
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0 and link.exists():
                logger.info(f"已创建模型目录联接（避免中文路径）: {link} -> {target}")
                return True
            logger.warning(
                f"创建目录联接失败 (code={completed.returncode}): "
                f"{(completed.stderr or completed.stdout or '').strip()}"
            )
        else:
            link.symlink_to(target, target_is_directory=True)
            return True
    except OSError as e:
        logger.warning(f"无法创建模型目录联接: {e}")
    return False


def funasr_runtime_root() -> Path:
    """Directory FunASR should use as MODELSCOPE_CACHE / local model parent.

    - 用户自定义（设置 / 环境变量）优先，且须为纯 ASCII 路径
    - 程序目录为纯英文时：用 exe 旁 models\\funasr
    - 程序目录含中文时：同盘 YilanChengWenData\\models\\funasr，或 LOCALAPPDATA 回退
    """
    custom = _configured_funasr_dir()
    if custom is not None:
        root = Path(custom)
        if not _path_has_non_ascii(root):
            root.mkdir(parents=True, exist_ok=True)
            logger.info(f"使用自定义 FunASR 模型目录: {root}")
            return root
        logger.warning(
            "自定义 FunASR 目录含非 ASCII，已忽略并回退默认策略: %s",
            root,
        )

    project = FUNASR_MODEL_DIR
    if not _path_has_non_ascii(project):
        project.mkdir(parents=True, exist_ok=True)
        return project

    safe = _ascii_funasr_root()
    # If project already has weights, expose them via junction under ASCII path
    if project.is_dir() and any(project.rglob("model.pt")):
        if not (safe / "models").exists():
            if not _ensure_windows_junction(safe, project.resolve()):
                safe.mkdir(parents=True, exist_ok=True)
                logger.warning(
                    "程序路径含非 ASCII 字符，FunASR 使用安全目录: %s "
                    "（建议把程序放到纯英文路径，或在设置中指定模型目录）",
                    safe,
                )
        return safe
    safe.mkdir(parents=True, exist_ok=True)
    return safe


def resolve_funasr_model_name(funasr_model: str) -> tuple[str, bool]:
    """Resolve FunASR model aliases, preferring complete local caches."""
    model_name = FUNASR_MODEL_ALIASES.get(funasr_model, funasr_model)
    is_sensevoice = model_name in {"iic/SenseVoiceSmall", "SenseVoiceSmall"} or funasr_model in {
        "sensevoice",
        "sensevoice-small",
    }

    if not is_sensevoice:
        return model_name, False

    candidates = [
        funasr_runtime_root() / "models" / "iic" / "SenseVoiceSmall",
        _project_sensevoice_dir(),
    ]
    # Deduplicate while preserving order
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if _sensevoice_complete(cand):
            # Prefer ASCII-safe path for native loaders (SentencePiece on Windows).
            # Do NOT Path.resolve() — it expands junctions back to the Chinese path.
            path_str = str(cand.absolute())
            if _path_has_non_ascii(path_str):
                safe_cand = funasr_runtime_root() / "models" / "iic" / "SenseVoiceSmall"
                if _sensevoice_complete(safe_cand) and not _path_has_non_ascii(safe_cand):
                    return str(safe_cand.absolute()), True
                logger.warning(
                    "本地 SenseVoice 位于含中文/非 ASCII 的路径，SentencePiece 可能无法读取。"
                    "将回退为模型 ID 以便下载到 ASCII 缓存，或请把项目放到纯英文路径。"
                )
                return model_name, True
            return path_str, True

    # Incomplete local dir (e.g. only model.pt): do NOT force local path — allow hub download
    incomplete = _project_sensevoice_dir()
    if (incomplete / "model.pt").exists() and not _sensevoice_complete(incomplete):
        logger.warning(
            "本地 SenseVoice 不完整（缺少 bpe/config 等），将尝试从 ModelScope 重新拉取到缓存目录"
        )
    return model_name, True


def resolve_funasr_vad_model_name() -> str:
    """Resolve the default FunASR VAD model, preferring local cache."""
    candidates = [
        funasr_runtime_root() / "models" / "iic" / "speech_fsmn_vad_zh-cn-16k-common-pytorch",
        _project_vad_dir(),
    ]
    for cand in candidates:
        if (cand / "model.pt").is_file():
            path_str = str(cand.absolute())
            if _path_has_non_ascii(path_str):
                safe = funasr_runtime_root() / "models" / "iic" / "speech_fsmn_vad_zh-cn-16k-common-pytorch"
                if (safe / "model.pt").is_file() and not _path_has_non_ascii(safe):
                    return str(safe.absolute())
                continue
            return path_str
    return "fsmn-vad"


def resolve_funasr_spk_model_name(spk_model: str = "cam++") -> str:
    """解析说话人分离模型（CAM++），优先用本地已下载的快照。

    返回值可直接喂给 `AutoModel(spk_model=...)`：
      - 本地有完整快照 → 返回绝对路径（ASCII 优先，避免中文路径问题）
      - 本地没有 / 不完整 → 返回模型 ID "cam++"，让 FunASR 自己从 ModelScope 拉
      - spk_model 为空串 → 返回空串（表示不分离，调用方不该传 spk_model 参数）
    """
    resolved = FUNASR_SPK_ALIASES.get((spk_model or "").strip().lower(), spk_model or "")
    if not resolved:
        return ""

    candidates = [
        funasr_runtime_root() / "models" / "iic" / CAMPLUS_DIR_NAME,
        _project_camplus_dir(),
    ]
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if _camplus_complete(cand):
            path_str = str(cand.absolute())
            if _path_has_non_ascii(path_str):
                safe = funasr_runtime_root() / "models" / "iic" / CAMPLUS_DIR_NAME
                if _camplus_complete(safe) and not _path_has_non_ascii(safe):
                    return str(safe.absolute())
                logger.warning(
                    "本地 CAM++ 位于含非 ASCII 的路径，回退为模型 ID 以便下载到 ASCII 缓存: %s",
                    path_str,
                )
                return resolved
            return path_str

    incomplete = _project_camplus_dir()
    if (incomplete / "model.pt").exists() and not _camplus_complete(incomplete):
        logger.warning(
            "本地 CAM++ 不完整（缺少 %s），将尝试从 ModelScope 重新拉取",
            ", ".join(f for f in CAMPLUS_REQUIRED_FILES if not (incomplete / f).is_file()),
        )
    return resolved


_RICH_TAG_RE = None


def _strip_rich_tags(text: str) -> str:
    """去掉 SenseVoice 富标签（`<|zh|><|NEUTRAL|><|Speech|><|withitn|>` 等）。

    官方 `rich_transcription_postprocess` 做的是同一件事，但它是有损的展示函数
    （还会合并重复标记 + 文本替换）。这里只做最小必要的去标签，避免把
    【说话人N】/时间戳排版弄乱，且不改动既有非分离路径的行为。
    """
    global _RICH_TAG_RE
    if _RICH_TAG_RE is None:
        import re

        _RICH_TAG_RE = re.compile(r"<\|[^|]*\|>")
    return _RICH_TAG_RE.sub("", text or "")


def _format_ms(ms: int) -> str:
    """毫秒 → [MM:SS]（超过 1 小时才带小时位）。与 xf_asr 的格式保持一致。"""
    if ms <= 0:
        return "00:00"
    total = ms // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_funasr_speaker_text(result, timestamps: bool = True) -> str:
    """把 FunASR + CAM++ 的 generate 结果格式化成带说话人/时间戳的文本。

    CAM++ 生效时 FunASR 会在结果里给 `sentence_info`，每项形如：
        {"spk": 0, "start": 3200, "end": 7000, "text": "你好", "sentence": "你好"}

    ⚠ 官方两处 demo 用了**不同的键名**：
        funasr.com/blog       → `s["sentence"]`
        funasr.com/go/sensevoice → `sent["text"]`
      所以这里两个键都读，谁在用都行。

    输出示例（两个开关都开）：
        [00:03] 【说话人1】你好我是老师
        [01:05] 【说话人2】老师好我是学生

    没有 `sentence_info`（CAM++ 没生效 / 老版本 FunASR）→ 返回空串，
    由调用方退回 `extract_funasr_text()` 的纯文本路径。
    """
    if not isinstance(result, list) or not result:
        return ""
    first = result[0] if isinstance(result[0], dict) else None
    if first is None:
        return ""
    sentences = first.get("sentence_info")
    if not isinstance(sentences, list) or not sentences:
        return ""

    # 角色编号 → 连续编号（spk=0/1 映射成 说话人1/说话人2）
    order: list = []
    for sent in sentences:
        if not isinstance(sent, dict):
            continue
        spk = sent.get("spk")
        if spk is not None and spk not in order:
            order.append(spk)
    speaker_map = {spk: f"说话人{i + 1}" for i, spk in enumerate(order)}

    lines: list[str] = []
    for sent in sentences:
        if not isinstance(sent, dict):
            continue
        # 官方两处 demo 键名不一致：sentence / text 都读
        raw = sent.get("text") or sent.get("sentence") or ""
        text = _strip_rich_tags(str(raw)).strip()
        if not text:
            continue
        prefix = ""
        if timestamps:
            try:
                start_ms = int(sent.get("start") or 0)
            except (TypeError, ValueError):
                start_ms = 0
            if start_ms > 0:
                prefix += f"[{_format_ms(start_ms)}] "
        spk = sent.get("spk")
        if spk is not None:
            prefix += f"【{speaker_map.get(spk, spk)}】"
        lines.append(f"{prefix}{text}" if prefix else text)
    return "\n".join(lines)


def prepare_local_audio(
    media_path: str,
    quality: str = "fast",
    batch_root: Optional[str] = None,
) -> tuple[str, str, bool]:
    """Return an audio path for a local audio/video file.

    Audio files are used directly. Video files are converted to mp3 first.
    """
    media_path_obj = Path(media_path)
    suffix = media_path_obj.suffix.lower()

    if suffix in AUDIO_EXTENSIONS:
        if not media_path_obj.exists():
            raise FileNotFoundError(f"本地音频文件不存在: {media_path}")
        logger.info(f"使用本地音频文件: {media_path}")
        return str(media_path_obj), media_path_obj.stem, False

    audio_path, title = extract_audio_from_local_video(media_path, quality, batch_root)
    return audio_path, title, True


def extract_audio_from_local_video(
    video_path: str,
    quality: str = "fast",
    batch_root: Optional[str] = None,
) -> tuple[str, str]:
    """Extract mp3 audio from a local video file."""
    start_time = time.time()
    logger.info(f"从本地视频提取音频: {video_path}")

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"本地视频文件不存在: {video_path}")

    video_path_obj = Path(video_path)
    if video_path_obj.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"不支持的视频格式: {video_path_obj.suffix}")

    title = video_path_obj.stem
    audio_output_dir = local_audio_dir(video_path, batch_root=batch_root)
    audio_output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(audio_output_dir / f"{title}.mp3")

    quality_map = {"fast": "32", "medium": "64", "slow": "128"}
    bitrate = quality_map.get(quality, "64")

    from .ffmpeg_tools import ensure_ffmpeg_on_path, resolve_ffmpeg

    ensure_ffmpeg_on_path()
    ffmpeg_bin = resolve_ffmpeg()
    if not ffmpeg_bin:
        raise RuntimeError(
            "未找到 FFmpeg。请将 ffmpeg.exe 放到程序目录的 ffmpeg\\ 下，"
            "或安装系统 FFmpeg 并加入 PATH。"
        )

    try:
        cmd = [
            ffmpeg_bin,
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ab",
            f"{bitrate}k",
            "-ar",
            "44100",
            "-y",
            audio_path,
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        elapsed = time.time() - start_time
        logger.info(f"音频提取完成: {title} (耗时: {format_time(elapsed)})")
        return audio_path, title
    except subprocess.CalledProcessError as e:
        logger.error(f"音频提取失败: {e.stderr.decode('utf-8', errors='ignore')}")
        raise RuntimeError(f"FFmpeg 提取音频失败: {e}") from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "FFmpeg 无法执行。请检查程序目录 ffmpeg\\ 或系统 PATH 中的 ffmpeg。"
        ) from e


def transcribe_audio(
    audio_path: str,
    model_size: str = "tiny",
    cpu_threads: int = 4,
    asr_engine: str = "funasr",
    funasr_model: str = "sensevoice",
    engine_config: dict = None,
) -> str:
    """Transcribe audio with the selected ASR engine.

    engine_config 是「当前引擎专属」的配置块：
    - qwen_asr → config.transcribe.qwen_asr 块
    - xf_asr   → config.transcribe.xf_asr 块
    - funasr   → transcribe 下所有 funasr_* 扁平键（说话人分离 / 缓存目录等）
    由调用方 (cli/processor) 按 asr_engine 装好对应块再传入，避免「给 xf_asr 传 qwen_asr 配置」导致凭证缺失误降 mock。
    """
    if asr_engine == "whisper":
        return transcribe_audio_with_whisper(audio_path, model_size, cpu_threads)
    if asr_engine == "funasr":
        return transcribe_audio_with_funasr(
            audio_path, funasr_model, engine_config=engine_config
        )
    if asr_engine == "qwen_asr":
        # 在 import qwen_asr / huggingface_hub 之前先设好 env 变量
        # （huggingface_hub 在 import 时把 ENDPOINT 缓存到 module-level 常量）
        import os
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HOME", r"E:\AI_Models\Qwen3-ASR")
        # 延迟 import：避免未装 qwen_asr 时启动报错
        from .qwen_asr import transcribe_audio_with_qwen_asr
        return transcribe_audio_with_qwen_asr(audio_path, engine_config or {})
    if asr_engine == "xf_asr":
        # 2026-09-27 新增：讯飞听见云端识别 backend
        # 凭证缺失自动降级 mock（与 qwen_asr 保持一致的容错策略）
        # 延迟 import：避免未装 requests 时启动报错（pyproject 已固定 requests>=2.31）
        from .xf_asr import transcribe_audio_with_xf_asr
        return transcribe_audio_with_xf_asr(audio_path, engine_config or {})
    raise ValueError(f"不支持的 ASR 引擎: {asr_engine}")


def transcribe_audio_with_whisper(audio_path: str, model_size: str = "tiny", cpu_threads: int = 4) -> str:
    """Transcribe audio with faster-whisper."""
    faster_whisper = import_required("faster_whisper", "faster-whisper")
    modelscope = import_required("modelscope", "modelscope")

    start_time = time.time()
    model_path = MODEL_DIR / f"whisper-{model_size}"

    if not model_path.exists():
        logger.info(f"下载 Whisper {model_size} 模型...")
        model_map = {
            "tiny": "pengzhendong/faster-whisper-tiny",
            "base": "pengzhendong/faster-whisper-base",
            "small": "pengzhendong/faster-whisper-small",
        }
        repo_id = model_map.get(model_size)
        if not repo_id:
            raise ValueError(f"不支持的模型: {model_size}")

        download_start = time.time()
        modelscope.snapshot_download(repo_id, local_dir=str(model_path))
        logger.info(f"模型下载完成 (耗时: {format_time(time.time() - download_start)})")

    logger.info(f"加载 Whisper 模型 ({model_size})...")
    load_start = time.time()
    model = faster_whisper.WhisperModel(
        model_size_or_path=str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
    )
    logger.info(f"模型加载完成 (耗时: {format_time(time.time() - load_start)})")

    logger.info("开始转写音频...")
    transcribe_start = time.time()
    segments_generator, _ = model.transcribe(audio_path, language="zh")

    full_text = ""
    segment_count = 0
    for segment in segments_generator:
        full_text += segment.text.strip() + " "
        segment_count += 1

    full_text = traditional_to_simplified(full_text.strip())
    logger.info(f"转写完成: {segment_count} 段 (耗时: {format_time(time.time() - transcribe_start)})")
    logger.info(f"转写总耗时: {format_time(time.time() - start_time)}, 共 {len(full_text)} 字符")
    return full_text


def transcribe_audio_with_funasr(
    audio_path: str,
    funasr_model: str = "sensevoice",
    engine_config: dict | None = None,
) -> str:
    """Transcribe audio with FunASR.

    2026-10-02 新增：可选的**本地**说话人分离（SenseVoice + CAM++）。

    engine_config 取 `transcribe` 下所有 `funasr_*` 键（由 processor._resolve_engine_config 装好）：
      - funasr_speaker       (bool)  是否启用说话人分离，默认 False
      - funasr_spk_model     (str)   说话人模型，默认 "cam++"
      - funasr_timestamps    (bool)  分隔输出是否带 [MM:SS]，默认 True
      - funasr_cache_dir     (str)   模型目录（_configured_funasr_dir 单独读，这里不用）

    与 xf_asr 的区别（重要）：这是**本地**能力，不调网络、不花云端额度；
    额外开销 = 多加载一个 ~28MB 的 CAM++ 嵌入模型 + 一次聚类，CPU 即可跑、不占显存。
    """
    cfg = engine_config or {}
    want_speaker = bool(cfg.get("funasr_speaker", False))
    spk_model_id = str(cfg.get("funasr_spk_model") or "cam++")
    want_timestamps = bool(cfg.get("funasr_timestamps", True))

    # Prefer ASCII-safe cache (critical on Windows when project path has 中文)
    funasr_cache_dir = funasr_runtime_root()
    funasr_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MODELSCOPE_CACHE"] = str(funasr_cache_dir)

    try:
        import_required("torch", "torch")
    except RuntimeError as e:
        raise RuntimeError(
            "FunASR 需要 PyTorch。请先在虚拟环境中运行: "
            "python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu"
        ) from e

    # 必须在 import funasr 之前打补丁：注册装饰器依赖 getsourcelines
    _patch_inspect_for_funasr_frozen()
    funasr = import_required("funasr", "funasr")
    _ensure_funasr_core_models_registered()

    model_name, is_sensevoice = resolve_funasr_model_name(funasr_model)
    vad_model_name = resolve_funasr_vad_model_name()
    spk_model_name = resolve_funasr_spk_model_name(spk_model_id) if want_speaker else ""
    start_time = time.time()
    logger.info(f"加载 FunASR 模型 ({model_name})...")
    logger.info(f"FunASR/ModelScope 模型缓存目录: {funasr_cache_dir}")
    if _path_has_non_ascii(FUNASR_MODEL_DIR):
        logger.info(
            "检测到项目 models 路径含非 ASCII 字符；已改用 ASCII 安全目录加载 "
            "（SentencePiece 无法读取中文路径下的 .bpe.model）"
        )
    load_start = time.time()

    # 打包环境下确认 SenseVoice 已注册，便于日志排查
    if getattr(sys, "frozen", False) and is_sensevoice:
        try:
            from funasr.register import tables

            keys = list(getattr(tables, "model_classes", {}) or {})
            if "SenseVoiceSmall" not in keys:
                logger.error(
                    "SenseVoiceSmall 仍未注册。已注册: %s",
                    ", ".join(keys[:20]) + ("..." if len(keys) > 20 else ""),
                )
                raise RuntimeError(
                    "打包环境 FunASR 模型注册失败（SenseVoiceSmall）。"
                    "请使用 0.4.2+ 版本绿色包，或改用开发环境运行。"
                )
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"检查 FunASR 注册表失败: {e}")

    # FunASR 的 disable_pbar 读的是 AutoModel 构造参数 self.kwargs，
    # 仅在 generate() 里传无效（VAD 阶段 inference 仍会刷 tqdm）。
    # 非 TTY（GUI 日志桥）下 tqdm 的 \r/ANSI 无法原地刷新，会整段糊进日志。
    common_model_kwargs = dict(
        disable_update=True,
        disable_pbar=True,  # 必须在构造时传入，见 funasr AutoModel.inference
        device="cpu",
    )
    if is_sensevoice:
        model_kwargs = dict(
            model=model_name,
            vad_model=vad_model_name,
            vad_kwargs={"max_single_segment_time": 30000},
            **common_model_kwargs,
        )
        generate_kwargs = {"language": "zh", "use_itn": True, "batch_size_s": 60, "merge_vad": True}
    else:
        model_kwargs = dict(
            model=model_name,
            vad_model=vad_model_name,
            punc_model="ct-punc",
            **common_model_kwargs,
        )
        generate_kwargs = {"batch_size_s": 60}

    # 2026-10-02：本地说话人分离（CAM++）
    # FunASR 官方约束：spk_model 必须与 vad_model 一起传（聚类在 VAD 流水线里做）
    if spk_model_name:
        model_kwargs["spk_model"] = spk_model_name
        logger.info(
            f"已启用本地说话人分离（CAM++，模型={spk_model_name}）；"
            "首次使用会从 ModelScope 下载约 28MB 到 FunASR 缓存目录"
        )

    model = funasr.AutoModel(**model_kwargs)

    logger.info(f"FunASR 模型加载完成 (耗时: {format_time(time.time() - load_start)})")
    logger.info("开始 FunASR 转写音频...")
    transcribe_start = time.time()
    # 双保险：环境变量 + generate 侧再次声明（部分子路径读 generate cfg）
    os.environ["TQDM_DISABLE"] = "1"
    try:
        result = model.generate(
            input=audio_path,
            disable_pbar=True,  # inference_with_vad 的 pbar_total 读 generate cfg
            **generate_kwargs,
        )
    except TypeError:
        # older funasr may not accept disable_pbar on generate
        result = model.generate(input=audio_path, **generate_kwargs)

    # 2026-10-02：优先走说话人/时间戳格式；拿不到 sentence_info 就退回纯文本
    if spk_model_name:
        speaker_text = format_funasr_speaker_text(result, timestamps=want_timestamps)
        if speaker_text:
            text = traditional_to_simplified(speaker_text)
            logger.info(
                f"FunASR 说话人分离生效（{len(speaker_text.splitlines())} 段，"
                f"{len(text)} 字符）"
            )
            logger.info(f"FunASR 转写总耗时: {format_time(time.time() - start_time)}")
            return text
        logger.warning(
            "已挂载 CAM++ 但结果里没有 sentence_info——"
            "可能 FunASR 版本较旧或音频只有单人语音，本次退回纯文本输出"
        )
    text = extract_funasr_text(result)
    text = traditional_to_simplified(text.strip())
    logger.info(f"FunASR 转写完成 (耗时: {format_time(time.time() - transcribe_start)})")
    logger.info(f"FunASR 转写总耗时: {format_time(time.time() - start_time)}, 共 {len(text)} 字符")
    return text


def extract_funasr_text(result) -> str:
    """Extract plain text from FunASR generate result."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return str(result.get("text") or result.get("sentence") or "")
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, dict):
                text = item.get("text") or item.get("sentence") or ""
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return " ".join(parts)
    return str(result or "")
