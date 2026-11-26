# -*- coding: utf-8 -*-
"""自检：通过 import_required() 懒加载的模块，必须显式写进 spec 的 hiddenimports。

PyInstaller 的静态分析看不见 `__import__(name)` 这种动态导入，漏掉一个模块
的表现是「打包后在客户机上才报 缺少依赖 XXX」——boto3（R2/OSS 上传）就是这样丢的，
在课堂机上直接让「上传音频 → 云端转写」整条链路断掉。

精简版刻意排除的本地引擎栈（torch / funasr 等）是例外：它们缺了会给出友好提示，
而且课堂演示走的是云端链路。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "video_to_article"
SPEC = ROOT / "packaging" / "video_to_article_gui.spec"

# 精简版（-Slim）刻意排除的本地引擎栈 —— 动态导入它们是允许的
SLIM_EXCLUDED = {
    "torch", "transformers", "qwen_asr", "funasr", "faster_whisper", "modelscope",
}


def top(name: str) -> str:
    return name.split(".")[0]


def dynamic_imports() -> set:
    """源码里所有 import_required("X") 的顶层模块名。"""
    found = set()
    for p in SRC.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"""import_required\(\s*["']([A-Za-z0-9_.]+)["']""", text):
            found.add(top(m.group(1)))
    return found


def spec_hiddenimports() -> set:
    """spec 里 hiddenimports 列表的顶层模块名。"""
    text = SPEC.read_text(encoding="utf-8")
    block = re.search(r"hiddenimports\s*=\s*\[(.*?)\n\]", text, re.S)
    assert block, "spec 里找不到 hiddenimports 列表"
    return {top(n) for n in re.findall(r"""["']([A-Za-z0-9_.]+)["']""", block.group(1))}


def test_every_dynamic_import_is_bundled() -> None:
    dyn = dynamic_imports()
    hidden = spec_hiddenimports()
    missing = sorted(dyn - hidden - SLIM_EXCLUDED)
    assert not missing, (
        "这些模块是动态导入的，但没写进 spec 的 hiddenimports，打包后会缺：\n  "
        + "\n  ".join(missing)
    )


def test_spec_lists_boto3() -> None:
    assert "boto3" in spec_hiddenimports(), "boto3 必须进包（R2/OSS 上传靠它）"


def test_dynamic_imports_not_empty() -> None:
    dyn = dynamic_imports()
    assert "boto3" in dyn and "yt_dlp" in dyn and "requests" in dyn, dyn


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK {fn.__name__}")
    print(f"ALL PASS ({len(fns)} groups)")
    print("  dynamic imports:", ", ".join(sorted(dynamic_imports())))
