# -*- coding: utf-8 -*-
"""用 Python 打 zip，避免 Compress-Archive 对中文文件名处理异常。

用法:
  python packaging/make_release_zip.py <source_dir> <zip_path>
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# Skip noisy/runtime-only paths when packaging green zip.
# "logs" only counts at the app root: vendor packages ship their own data/logs
# (e.g. botocore's CloudWatch Logs models) and dropping them breaks folder/zip parity.
SKIP_ROOT_DIRS = {"logs"}
SKIP_ANY_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".log", ".pyc", ".part", ".ytdl", ".tmp"}
SKIP_NAMES = {"custom_post_headers.txt"}  # runtime file holding auth headers


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: make_release_zip.py <source_dir> <zip_path>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    zip_path = Path(sys.argv[2])
    if not src.is_dir():
        print(f"not a directory: {src}", file=sys.stderr)
        return 1

    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(src).parts
            if rel_parts[0] in SKIP_ROOT_DIRS or path.name in SKIP_NAMES:
                continue
            if any(p in SKIP_ANY_DIRS for p in rel_parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            arc = path.relative_to(src).as_posix()
            zf.write(path, arcname=arc)
            count += 1

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"zip written: {zip_path} ({count} files, {size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
