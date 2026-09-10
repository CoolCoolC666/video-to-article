"""Python 启动主程序 0.6B 端到端测试。
避开 PowerShell 编码 + 中文路径转义问题。
"""
import os
import subprocess
import sys
from pathlib import Path

# 关键环境变量
os.environ['HF_HOME'] = r'E:\AI_Models\Qwen3-ASR'
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
# 强制 UTF-8，避免 PowerShell 编码
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'
# 让 venv python 找得到 src/video_to_article
os.environ['PYTHONPATH'] = r'E:\000~\YilanChengWen-src\src'

# mp3 路径（直接传 str 给 subprocess，避免 .ps1 变量插值）
mp3 = r'E:\000~\YilanChengWen-0.4.5\data\bilibili\Bilibili-甘泽成谣雨成诗\audio\差分宇宙科研前台符玄震撼发布连试用不死途也能够保住的顶级生存位优化后的缇宝主C全程思路让你看完轻松连_BV1Wd4X6fEnZ.mp3'
if not Path(mp3).exists():
    print(f'FATAL: mp3 不存在: {mp3}')
    sys.exit(1)

print(f'mp3 exists: {Path(mp3).stat().st_size} bytes')
print(f'cmd: python -m video_to_article --local <mp3> --prompt general_article --asr-engine qwen_asr')

# 用 subprocess + CREATE_NO_WINDOW 后台跑；stdout/stderr 都 capture 到 log
log = r'E:\000~\YilanChengWen-src\run_e2e_main.log'
creationflags = 0x08000000  # CREATE_NO_WINDOW

# 关键：venv 的 python 解释器
venv_python = r'E:\000~\YilanChengWen-src\.venv\Scripts\python.exe'
if not Path(venv_python).exists():
    print(f'FATAL: venv python 不存在: {venv_python}')
    sys.exit(1)

p = subprocess.Popen(
    [venv_python, '-X', 'utf8', '-m', 'video_to_article',
     '--local', mp3,
     '--prompt', 'general_article',
     '--asr-engine', 'qwen_asr'],
    cwd=r'E:\000~\YilanChengWen-src',
    stdout=open(log, 'wb'),
    stderr=subprocess.STDOUT,
    creationflags=creationflags,
    env=os.environ,
)
print(f'pid: {p.pid}')
print(f'log: {log}')
