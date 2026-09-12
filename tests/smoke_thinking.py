"""Smoke test: providers/llm.py 自动剥离 <think>...</think> 块（推理模型暴露）"""
from __future__ import annotations

import os
import sys
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")


def test_strip_basic():
    """1. 标准 M3 输出格式：think 块在前，正文在后"""
    from video_to_article.providers.llm import _strip_thinking_block

    raw = "<think>\n用户让我整理视频内容。我先想想...\n应该先识别主题。\n</think>\n\n# 文章标题\n\n这是正文。"
    cleaned = _strip_thinking_block(raw)
    print("cleaned =", repr(cleaned[:50]))
    assert "<think>" not in cleaned
    assert "</think>" not in cleaned
    assert "这是正文" in cleaned
    assert cleaned.lstrip().startswith("# 文章标题")
    print("OK 1: 剥离 think 块，保留正文\n")


def test_strip_no_think():
    """2. 没有 think 块时，原文返回"""
    from video_to_article.providers.llm import _strip_thinking_block

    raw = "# 标题\n\n正常正文，无 think 块。"
    cleaned = _strip_thinking_block(raw)
    assert cleaned == raw, f"应原样返回，实际 {cleaned!r}"
    print("OK 2: 无 think 块时原文返回\n")


def test_strip_empty():
    """3. 空字符串 / None 边界"""
    from video_to_article.providers.llm import _strip_thinking_block

    assert _strip_thinking_block("") == ""
    assert _strip_thinking_block(None) is None
    print("OK 3: 空/None 边界\n")


def test_strip_multiline():
    """4. think 块跨多行（含换行符）"""
    from video_to_article.providers.llm import _strip_thinking_block

    raw = "<think>\nLine1\nLine2\nLine3\n</think>\n# 后面的内容"
    cleaned = _strip_thinking_block(raw)
    assert "Line1" not in cleaned
    assert "Line2" not in cleaned
    assert cleaned.strip() == "# 后面的内容"
    print("OK 4: 跨多行 think 块正确剥离\n")


def test_strip_only_first():
    """5. 只剥离首个 think 块（罕见场景：M3 输出多个 think）"""
    from video_to_article.providers.llm import _strip_thinking_block

    # 第一个 think 块在开头，第二个在中间（这种情况罕见，但 regex 应该不命中）
    raw = "<think>第一个</think>\n# 标题\n<think>第二个</think>\n正文"
    cleaned = _strip_thinking_block(raw)
    # 预期：第一个被剥，第二个仍在
    assert "第一个" not in cleaned
    assert "第二个" in cleaned  # 中间的保留
    assert "# 标题" in cleaned
    print("OK 5: 只剥离首个 think 块，保留中段\n")


def test_strip_whitespace_around():
    """6. think 块前后有大量空白（典型 M3 输出）"""
    from video_to_article.providers.llm import _strip_thinking_block

    raw = "\n\n<think>\n   \n   thinking content\n   \n</think>\n\n\n\n# 文章\n\n正文"
    cleaned = _strip_thinking_block(raw)
    assert "thinking content" not in cleaned
    assert cleaned.lstrip().startswith("# 文章")
    print("OK 6: think 块前后空白也吃掉\n")


def test_strip_imported_in_llm_module():
    """7. 验证 _strip_thinking_block 在 llm 模块下可访问（不是私有的）"""
    from video_to_article.providers import llm as llm_mod
    assert hasattr(llm_mod, "_strip_thinking_block")
    assert callable(llm_mod._strip_thinking_block)
    print("OK 7: _strip_thinking_block 在 llm 模块下可访问\n")


if __name__ == "__main__":
    test_strip_basic()
    test_strip_no_think()
    test_strip_empty()
    test_strip_multiline()
    test_strip_only_first()
    test_strip_whitespace_around()
    test_strip_imported_in_llm_module()
    print("=" * 60)
    print("ALL 7 tests passed ✓")
    print("=" * 60)