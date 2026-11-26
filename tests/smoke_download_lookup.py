# -*- coding: utf-8 -*-
"""自检：下载产物的「按 ID 兜底查找」与「目录清单」。

背景：文件是用「下载那一刻」算出的 stem 写进磁盘的，查找却用「下载之后」
重算的 stem。标题在字幕阶段（Bilibili API）与下载后（yt-dlp）不一致时，
文件在磁盘上却认不出来 —— 这类兜底就是为它准备的。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_to_article.media.download import (  # noqa: E402
    AUDIO_EXTS,
    _dir_listing,
    _find_by_video_id,
)


def test_find_by_video_id_when_stem_differs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 文件名里的标题与「预期 stem」不同 —— 只差在标题那一段
        (d / "别的标题_BV11fsh68EyT.mp3").write_bytes(b"x")
        found = _find_by_video_id([d], "BV11fsh68EyT", AUDIO_EXTS)
        assert found, "按视频 ID 必须能找到标题对不上的文件"
        assert Path(found).name.endswith(".mp3"), found


def test_find_by_video_id_ignores_wrong_ext() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "x_BV1.json").write_text("{}", encoding="utf-8")
        assert _find_by_video_id([d], "BV1", AUDIO_EXTS) is None, "非音频扩展名不该被认成音频"


def test_find_by_video_id_empty_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "a_BV1.mp3").write_bytes(b"x")
        assert _find_by_video_id([d], "", AUDIO_EXTS) is None


def test_find_by_video_id_missing_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "nope"
        assert _find_by_video_id([missing], "BV1", AUDIO_EXTS) is None


def test_dir_listing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        assert _dir_listing(d) == "(空)"
        (d / "a.mp3").write_bytes(b"x")
        (d / "b.mp3").write_bytes(b"x")
        out = _dir_listing(d)
        assert "a.mp3" in out and "b.mp3" in out, out
        assert "2 个文件" in out, out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK {fn.__name__}")
    print(f"ALL PASS ({len(fns)} groups)")
