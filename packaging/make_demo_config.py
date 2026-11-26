# -*- coding: utf-8 -*-
"""准备课堂演示包：演示用 config.json + 课堂速查。

用法:
  python packaging/make_demo_config.py --config config.json --out dist/YilanChengWen

为什么不直接把 config.json 拷过去：

  1. **密钥**：演示链路只需要「云端大模型 + DashScope + 图床」三组。
     讯飞转写、MiniMax ASR、AI 封面三组密钥演示用不到，不该跟着包走。
  2. **本机绝对路径**：funasr 缓存目录、qwen_asr 模型缓存、cookies 文件
     都是本机路径，教室电脑上不存在 —— 留着只会让人以为能用，
     程序又会去那些目录找文件。

注意：生成的是**演示专用**配置，别拿它覆盖日常用的那份。
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 演示用不到的密钥 → 清空（保留字段名，GUI 不会因缺字段报错）
BLANK_SECRETS: Tuple[Tuple[str, ...], ...] = (
    ("transcribe", "xf_asr", "secret_key"),
    ("transcribe", "minimax_asr", "api_key"),
    ("ai_cover", "api_key"),
)

# 本机绝对路径 → 清空
BLANK_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("transcribe", "funasr_cache_dir"),
    ("transcribe", "qwen_asr", "hf_home"),
    ("youtube", "cookies_file"),
    ("transcribe", "custom_post", "headers_file"),
)

# custom_post 的档案里，只有 dashscope_async 风格是演示链路用的
KEEP_PROFILE_STYLE = "dashscope_async"

# 课堂速查的中文文件名 —— 必须由 Python 落盘：
# PowerShell 5.1 会把 .ps1 里的中文字面量解码坏（见 build_gui_onedir.ps1 顶部提醒）
QUICKSTART_NAME = "课堂演示速查.txt"


def _get(cfg: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    node: Any = cfg
    for k in path:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _set(cfg: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    node: Any = cfg
    for k in path[:-1]:
        if not isinstance(node, dict) or not isinstance(node.get(k), dict):
            return
        node = node[k]
    if isinstance(node, dict) and path[-1] in node:
        node[path[-1]] = value


def build_demo_config(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """返回 (演示配置, 变更说明)。说明只写字段名，不写值。"""
    out = copy.deepcopy(cfg or {})
    notes: List[str] = []

    for path in BLANK_SECRETS:
        node = _get(out, path[:-1])
        if isinstance(node, dict) and node.get(path[-1]):
            notes.append("清空密钥 " + ".".join(path))
        _set(out, path, "")

    for path in BLANK_PATHS:
        node = _get(out, path[:-1])
        if isinstance(node, dict) and node.get(path[-1]):
            notes.append("清空本机路径 " + ".".join(path))
        _set(out, path, "")

    cp = _get(out, ("transcribe", "custom_post")) or {}
    for p in cp.get("profiles") or []:
        if not isinstance(p, dict):
            continue
        if p.get("api_style") != KEEP_PROFILE_STYLE and p.get("api_key"):
            label = p.get("label") or p.get("id") or "?"
            notes.append("清空密钥 transcribe.custom_post.profiles[" + str(label) + "].api_key")
            p["api_key"] = ""

    return out, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="准备课堂演示包配置")
    ap.add_argument("--config", required=True, help="源 config.json")
    ap.add_argument("--out", required=True, help="输出目录（config.json 与课堂速查写这里）")
    ap.add_argument("--quickstart", default="", help="课堂速查源文件（可选）")
    args = ap.parse_args()

    src = Path(args.config)
    if not src.is_file():
        print("源配置不存在: " + str(src), file=sys.stderr)
        return 1
    try:
        cfg = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print("源配置不是合法 JSON: " + str(e), file=sys.stderr)
        return 1

    demo, notes = build_demo_config(cfg)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    cfg_out = outdir / "config.json"
    cfg_out.write_text(json.dumps(demo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("demo config -> " + str(cfg_out))
    for n in notes:
        print("  - " + n)

    if args.quickstart:
        qsrc = Path(args.quickstart)
        if qsrc.is_file():
            dst = outdir / QUICKSTART_NAME
            shutil.copy2(qsrc, dst)
            print("quickstart -> " + str(dst))
        else:
            print("课堂速查源文件不存在，跳过: " + str(qsrc), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
