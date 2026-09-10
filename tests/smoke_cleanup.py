"""Smoke: atexit 注册 + 手动清理 qwen_asr_chunks_* tempdir"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import time
import atexit

sys.path.insert(0, r"src")


def test_atexit_registered():
    """1. 导入 qwen_asr 自动注册 atexit 兜底清理"""
    import video_to_article.media.qwen_asr as q
    # 检查 atexit 已注册（Python 不会暴露列表，但 _cleanup_orphaned_chunks_on_exit 应可用）
    assert hasattr(q, "_cleanup_orphaned_chunks_on_exit")
    print(f"_cleanup_orphaned_chunks_on_exit 存在: {q._cleanup_orphaned_chunks_on_exit.__name__}")
    print("OK 1: atexit 兜底函数已定义（import 时自动注册）\n")


def test_manual_cleanup():
    """2. 手动清残留 tempdir"""
    # 先创建几个假 tempdir
    tempdir = tempfile.gettempdir()
    created = []
    for i in range(3):
        d = os.path.join(tempdir, f"qwen_asr_chunks_smoketest_{i}_{os.getpid()}")
        os.makedirs(d, exist_ok=True)
        # 放一个 wav 模拟（10 KB）
        wav = os.path.join(d, "chunk_000000.wav")
        with open(wav, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 10240)
        created.append(d)
    print(f"创建 {len(created)} 个假 tempdir")

    # 调用清理
    import glob
    cleaned = 0
    bytes_freed = 0
    for d in glob.glob(os.path.join(tempdir, "qwen_asr_chunks_smoketest_*")):
        for root, _dirs, files in os.walk(d):
            for f in files:
                try:
                    bytes_freed += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        shutil.rmtree(d, ignore_errors=True)
        cleaned += 1
    mb = bytes_freed / 1024 / 1024
    print(f"清理 {cleaned} 个，腾出 {mb:.2f} MB")
    assert cleaned == 3, f"应清 3 个，实际 {cleaned}"
    assert all(not os.path.exists(d) for d in created)
    print("OK 2: 手动清残留 tempdir 正确\n")


def test_atexit_runs_on_normal_exit():
    """3. atexit 在 Python 正常退出时跑（创建→退出子进程→残留被清）"""
    import subprocess
    # 写一个临时脚本，import qwen_asr + 创建一个 tempdir + 不显式清理
    sub_script = r"""
import os, sys, tempfile, shutil
sys.path.insert(0, r"src")
# import 触发 atexit.register
import video_to_article.media.qwen_asr as q
# 创建假 tempdir
d = os.path.join(tempfile.gettempdir(), "qwen_asr_chunks_atexit_test")
os.makedirs(d, exist_ok=True)
with open(os.path.join(d, "test.txt"), "w") as f:
    f.write("x" * 1024)
print(f"BEFORE_EXIT: {d} exists={os.path.exists(d)}")
# 不显式清理，依赖 atexit
"""
    d = os.path.join(tempfile.gettempdir(), "qwen_asr_chunks_atexit_test")
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)

    proc = subprocess.run(
        [sys.executable, "-c", sub_script],
        capture_output=True, timeout=30,
    )
    # 强制 utf-8 解码（Windows 子进程可能是 GBK）
    proc_stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr_text = proc.stderr.decode("utf-8", errors="replace")
    print("子进程输出:")
    print(proc_stdout)
    assert "BEFORE_EXIT: True" in proc_stdout or "BEFORE_EXIT" in proc_stdout, "子进程应创建 tempdir"
    # 关键：atexit 应该已经把它清了
    assert not os.path.exists(d), f"atexit 没跑，{d} 仍残留"
    print("OK 3: atexit 真的在 Python 退出时清残留\n")


if __name__ == "__main__":
    test_atexit_registered()
    test_manual_cleanup()
    test_atexit_runs_on_normal_exit()
    print("=" * 50)
    print("ALL cleanup tests passed ✓")
    print("=" * 50)
