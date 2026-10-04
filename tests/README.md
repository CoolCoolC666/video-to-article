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
.venv\Scripts\python.exe tests\smoke_xf_e2e.py
.venv\Scripts\python.exe tests\smoke_funasr_speaker.py
.venv\Scripts\python.exe tests\smoke_custom_post_asr.py
.venv\Scripts\python.exe tests\smoke_dashscope_asr.py
.venv\Scripts\python.exe tests\smoke_llm_settings.py
.venv\Scripts\python.exe tests\smoke_bilibili_parse.py
```

### 一次跑完（14 套）

```powershell
cd E:\000~\YilanChengWen-src
Get-ChildItem tests\smoke_*.py | ForEach-Object {
  & .venv\Scripts\python.exe $_.FullName *>&1 | Out-Null
  "{0,-32} {1}" -f $_.Name, $(if ($LASTEXITCODE -eq 0) {"OK"} else {"FAIL"})
}
```

> ⚠ **必须用 `.venv\Scripts\python.exe`**：GUI 相关 smoke 依赖 `qfluentwidgets`，
> 只装在 venv 里，系统 python 跑会 ImportError。

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
| `smoke_funasr_speaker.py` | FunASR CAM++ 模型解析 / sentence_info 格式化 / 富标签剥离 |
| `smoke_custom_post_asr.py` | 自定义 POST 云端识别：协议契约、切段阈值、跨段偏移、9 类错误码、**base 自动拼路径 + 已填完整路径不重复拼**、请求头文件解析、凭证双来源、旧名 `minimax_asr` 兼容、五处注册点 |
| `smoke_dashscope_asr.py` | **DashScope 异步**（2026-10-04 新增，20 组）：端点拼接、提交体字段、**中文/空格 URL 编码**、`parameters` 条件分支（`language_hints` 只 paraformer / 人数越界不下发）、轮询状态机（GET）、**整体 SUCCEEDED 但子任务 FAILED 会被拦住**、内联结果兜底、超时、官方 `transcripts[].sentences[]` 归一 + **毫秒转秒**、分风格切段阈值、没配图床的可操作提示 |
| `smoke_llm_settings.py` | LLM 多档案 + 模型自动发现：协议/厂商两行对齐、Model 可编辑下拉 + ⟳ 抓取、档案增删改、档案隔离 |
| `smoke_bilibili_parse.py` | B 站引用解析（2026-10-03 新增）：严格语法、**av↔BV 本地互转**（锚点 `av170001 ↔ BV17x411w7KC`）、优先级与消费区间、b23 短链展开、av 形式 URL 取字幕 |
