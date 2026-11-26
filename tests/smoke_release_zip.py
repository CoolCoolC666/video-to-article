
# -*- coding: utf-8 -*-
"""自检：打包 zip 的排除规则不能误伤第三方包自带的 data/logs 目录。

历史 bug：排除表里一个 "logs" 命中任意层级目录名，把 _internal/botocore/data/logs/
整块丢掉 —— 文件夹交付物里有、zip 里没有，「复制贴贴下运行」时 zip 版本少文件。
"""
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKER = ROOT / "packaging" / "make_release_zip.py"


def _build_zip(tmp: Path) -> Path:
    src = tmp / "pkg"
    (src / "logs").mkdir(parents=True)
    (src / "logs" / "app.log").write_text("noise", encoding="utf-8")
    (src / "_internal" / "botocore" / "data" / "logs" / "2014-03-28").mkdir(parents=True)
    (src / "_internal" / "botocore" / "data" / "logs" / "2014-03-28" / "service-2.json.gz").write_bytes(b"{}")
    (src / "_internal" / "pkg" / "__pycache__").mkdir(parents=True)
    (src / "_internal" / "pkg" / "__pycache__" / "x.pyc").write_bytes(b"x")
    (src / "custom_post_headers.txt").write_text("Authorization: secret", encoding="utf-8")
    (src / "YilanChengWen.exe").write_bytes(b"x")
    zip_path = tmp / "out.zip"
    r = subprocess.run([sys.executable, str(MAKER), str(src), str(zip_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return zip_path


def test_vendor_logs_dir_kept():
    """第三方包自带的 data/logs 必须留在 zip 里"""
    with tempfile.TemporaryDirectory() as td:
        zp = _build_zip(Path(td))
        with zipfile.ZipFile(zp) as z:
            names = set(z.namelist())
        assert "_internal/botocore/data/logs/2014-03-28/service-2.json.gz" in names, sorted(names)


def test_root_logs_and_secrets_dropped():
    """程序自己的 logs/ 残留与带凭据的运行时文件不进 zip"""
    with tempfile.TemporaryDirectory() as td:
        zp = _build_zip(Path(td))
        with zipfile.ZipFile(zp) as z:
            names = set(z.namelist())
        assert not any(n.startswith("logs/") for n in names), sorted(names)
        assert "custom_post_headers.txt" not in names, sorted(names)
        assert not any("__pycache__" in n for n in names), sorted(names)
        assert "YilanChengWen.exe" in names, sorted(names)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK {fn.__name__}")
    print(f"ALL PASS ({len(fns)} groups)")
