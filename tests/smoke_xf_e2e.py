"""One-off end-to-end smoke: transcribe_audio() -> xf_asr dispatch."""
import sys, tempfile, subprocess, shutil, os
sys.path.insert(0, r"src")

# 准备短音频（用 ffmpeg 合成静音）
audio = os.path.join(tempfile.gettempdir(), "e2e_xf.wav")
ffmpeg = shutil.which("ffmpeg")
if ffmpeg:
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=16000:cl=mono", "-t", "3",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", audio],
        check=False, capture_output=True,
    )
if not os.path.exists(audio):
    # fallback: 最小 wav header
    import wave
    with wave.open(audio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 3)

# 入口 1：audio.transcribe_audio 分发（长语音鉴权只需 APPID + SecretKey）
from video_to_article.media import audio as am
text = am.transcribe_audio(
    audio, asr_engine="xf_asr",
    engine_config={"mock": True, "app_id": "", "secret_key": ""},
)
print(f"[e2e1] transcribe_audio(xf_asr, mock=True) -> {len(text)} chars")
print(f"[e2e1] preview: {text[:60]}")

# 入口 2：直接调
from video_to_article.media.xf_asr import transcribe_audio_with_xf_asr
text2 = transcribe_audio_with_xf_asr(audio, {"mock": True})
print(f"[e2e2] direct call -> {len(text2)} chars")

# 入口 3：凭证缺失 + mock=false → 自动降级 mock（不报错）
text3 = transcribe_audio_with_xf_asr(audio, {"mock": False, "app_id": ""})
print(f"[e2e3] missing creds + mock=False auto-fallback -> {len(text3)} chars")

# 入口 4：__all__ 导出检查
import video_to_article.media
print(f"[e2e4] __all__ exports xf_asr: {'transcribe_audio_with_xf_asr' in video_to_article.media.__all__}")

# 入口 5：engine dispatch 错误路径
try:
    am.transcribe_audio(audio, asr_engine="bogus")
    print("[e2e5] FAIL: should have raised ValueError")
except ValueError as e:
    print(f"[e2e5] bogus engine raises ValueError: {e}")

os.unlink(audio)
print("[e2e] ALL END-TO-END OK")