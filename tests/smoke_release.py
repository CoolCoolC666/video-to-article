"""Smoke: 手动释放 ASR 模型（GUI 端新功能）"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")


def test_get_cached_model_id():
    """1. 未加载时返回 None"""
    from video_to_article.media import qwen_asr
    qwen_asr._cached_model = None
    qwen_asr._cached_model_id = None
    print(f"未加载状态: get_cached_model_id() = {qwen_asr.get_cached_model_id()}")
    assert qwen_asr.get_cached_model_id() is None, "应返回 None"

    # 模拟"加载了"
    class FakeModel:
        pass
    qwen_asr._cached_model = FakeModel()
    qwen_asr._cached_model_id = "Qwen/Qwen3-ASR-0.6B"
    print(f"已加载状态: get_cached_model_id() = {qwen_asr.get_cached_model_id()}")
    assert qwen_asr.get_cached_model_id() == "Qwen/Qwen3-ASR-0.6B"
    print("OK 1: get_cached_model_id 正确返回状态\n")


def test_release_clears_cache():
    """2. _release_cached_model 清空 cache"""
    from video_to_article.media import qwen_asr

    class FakeModel:
        pass
    qwen_asr._cached_model = FakeModel()
    qwen_asr._cached_model_id = "Qwen/Qwen3-ASR-1.7B"
    qwen_asr._release_cached_model()
    print(f"释放后: get_cached_model_id() = {qwen_asr.get_cached_model_id()}")
    assert qwen_asr.get_cached_model_id() is None
    assert qwen_asr._cached_model is None
    print("OK 2: _release_cached_model 后 get_cached_model_id 返回 None\n")


def test_release_idempotent():
    """3. 重复释放安全（不会报错）"""
    from video_to_article.media import qwen_asr
    qwen_asr._release_cached_model()  # 已经 None
    qwen_asr._release_cached_model()  # 再来一次
    print("OK 3: 重复释放安全（idempotent）\n")


if __name__ == "__main__":
    test_get_cached_model_id()
    test_release_clears_cache()
    test_release_idempotent()
    print("=" * 50)
    print("ALL release tests passed ✓")
    print("=" * 50)
