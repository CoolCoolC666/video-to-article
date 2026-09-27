# Smoke Tests

冒烟测试脚本，覆盖 GUI 改造 + device fallback + language 白名单 + max_tokens 1M 等改动。

## 运行方式

**必须从仓库根目录运行**（不是从 tests/ 内）：

```powershell
cd E:\000~\YilanChengWen-src
.venv\Scripts\python.exe tests\smoke_settings.py
.venv\Scripts\python.exe tests\smoke_fallback.py
.venv\Scripts\python.exe tests\smoke_language.py
.venv\Scripts\python.exe tests\smoke_jp_kr.py
.venv\Scripts\python.exe tests\smoke_release.py
.venv\Scripts\python.exe tests\smoke_cleanup.py
.venv\Scripts\python.exe tests\smoke_thinking.py
.venv\Scripts\python.exe tests\smoke_xf_asr.py
```

脚本内用 `sys.path.insert(0, r"src")` 把 `src/` 加到模块路径，所以 cwd 必须是仓库根。

## 环境要求

- Python 3.10+
- `pip install -e .` 或至少 `pip install PySide6 torch transformers`
- 不需要真实的 Qwen3-ASR 模型（脚本只验证配置读写 / cache key 逻辑）

## 测试覆盖

| 脚本 | 验证点 |
|------|--------|
| `smoke_settings.py` | SettingsDialog 正确读 + 写 5 字段（model/context/hf_home/language/device）|
| `smoke_fallback.py` | `_resolve_device()` 在 CUDA 不可用时降级 / cache key 失效 |
| `smoke_language.py` | Qwen3-ASR language 白名单兜底（Auto → Chinese）|
| `smoke_jp_kr.py` | Japanese/Korean 路径能走通（不实际转写，只验配置）|
| `smoke_release.py` | 释放 ASR 模型按钮 + 状态刷新 |
| `smoke_cleanup.py` | 临时缓存清理（%TEMP%\qwen_asr_chunks_*）|
| `smoke_thinking.py` | `_strip_thinking_block()` 剥离 Qwen3 / DeepSeek 思考块 |
| `smoke_xf_asr.py` | 讯飞听见云端识别（2026-09-27 新增，2026-09-27 订正字段）：HMAC-SHA1 签名算法、Mock 三种情况日志区分、长音频切段阈值、atexit 清理、SettingsDialog **5 字段读写**（删 api_key）+ 防呆不破坏 qwen_asr 块 |
| `smoke_xf_e2e.py` | xf_asr 端到端 dispatch（音频 → transcribe_audio → xf_asr 主入口）+ `__all__` re-export 校验 |