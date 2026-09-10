# YilanChengWen src 改造 — 安装与改动清单

> 维护人：Mavis
> 维护日期：2026-09-07
> 用途：记录这次为绕开 funasr PyInstaller 嵌入版 bug 而做的所有改动 + 安装的依赖

---

## 1. 安装的新依赖（venv: `E:\000~\YilanChengWen-src\.venv`）

按安装顺序：

| 包 | 版本 | 装它干啥 | 装到哪 |
|---|---|---|---|
| numpy | 2.5.3 | 基础 | venv |
| qwen_asr | 0.0.6+ | Qwen3-ASR 后端（funasr 替代） | venv |
| transformers | 4.57.6 | qwen_asr 依赖 | venv |
| openai | 3.8.0 | LLM API（OpenAI 兼容协议） | venv |
| httpx2 | 2.12.0 | openai 3.x HTTP 客户端 | venv |
| jiter | 0.16.0 | openai 依赖 | venv |
| sniffio | 1.3.1 | openai 依赖 | venv |
| anyio | 4.15.1 | openai 依赖 | venv |
| pydantic | 2.13.5 | openai 依赖 | venv |
| typing-extensions | 4.16.0 | openai 依赖 | venv |
| **torch** | **2.11.0+cu128** | Qwen3-ASR 推理（Blackwell sm_120 必需 cu128） | venv |
| **torchvision** | **0.26.0+cu128** | 视频/图像处理 | venv |
| **torchaudio** | **2.11.0+cu128** | 音频处理 | venv |
| setuptools | 78.1.0 | 降级（从 84.0.0 降下）兼容 torch | venv |

> **被绕开的**：`funasr`（PyInstaller 嵌入版坏）、`anthropic`（没用到）、`modelscope`（没用到）、`faster-whisper`（用 whisper 模式才需要）、`yt-dlp`（--local 模式不用）

## 2. 复制的二进制

| 文件 | 来源 | 目标 | 用途 |
|---|---|---|---|
| ffmpeg.exe (87 MB) | `E:\000~\YilanChengWen-0.4.5\ffmpeg\` | `E:\000~\YilanChengWen-src\ffmpeg\` | 源码模式找不到 ffmpeg，复制一份（不占 C 盘） |
| ffprobe.exe (87 MB) | 同上 | 同上 | 同上 |

## 3. 下载的模型

| 模型 | 来源 | 位置 | 大小 |
|---|---|---|---|
| Qwen3-ASR-0.6B | https://hf-mirror.com/Qwen/Qwen3-ASR-0.6B/resolve/main/ | `E:\AI_Models\Qwen3-ASR\Qwen_Qwen3-ASR-0.6B\` | 1.2 GB |
| Qwen3-ASR-0.6B (hub cache) | 同上（huggingface_hub 自动同步） | `E:\AI_Models\Qwen3-ASR\hub\models--Qwen--Qwen3-ASR-0.6B\` | 1.2 GB（重复存） |

> **总占用：约 2.4 GB**。如果要省一半空间，可以删 `E:\AI_Models\Qwen3-ASR\hub\` 下的 huggingface_hub 缓存（只留 `Qwen_Qwen3-ASR-0.6B\` 本地目录就够了）

## 4. 改的代码

### 4.1 新增

| 文件 | 行数 | 作用 |
|---|---|---|
| `src/video_to_article/media/qwen_asr.py` | 76 | Qwen3-ASR 后端实现（走 qwen_asr 包高层 API） |
| `run_qwen_asr.py` | 80 | 一次性端到端验证脚本（直接 snapshot_download + 转写） |
| `fix_asr_engine.py` | 35 | 一次性 config 修复脚本（用 Python regex 改，避免 edit 工具在 mojibake 上静默失败） |

### 4.2 修改

| 文件 | 改动 |
|---|---|
| `config.json` | `asr_engine: "funasr"` → `"qwen_asr"`（用 Python regex 改，edit 工具失败） |
| `src/video_to_article/cli.py` | line 149：`--asr-engine` choices 加 `"qwen_asr"`，default None；line 160-169：新加 `resolve_asr_engine()` 辅助；line 238-241：`main()` 里回填 |
| `src/video_to_article/media/audio.py` | line 416-435：转写函数加 `qwen_asr` 分支 + 提前设 `HF_ENDPOINT`（避免 huggingface_hub 缓存默认值到 `huggingface.co`） |
| `src/video_to_article/processor.py` | line 507-512：print 加 `qwen_asr` 分支（之前 else 走 whisper 误报） |
| `src/video_to_article/ffmpeg/ffmpeg.exe` | 复制（87 MB） |
| `src/video_to_article/ffmpeg/ffprobe.exe` | 复制（87 MB） |

## 5. 磁盘使用（截至 2026-09-07 20:30）

| 盘 | 总 | 已用 | 剩余 | 备注 |
|---|---|---|---|---|
| C | 300 GB | 252 GB | **48 GB** | 13% → 16%，已清 9.5 GB（pip cache 7.2 GB + pip-unpack 2.2 GB） |
| D | 653 GB | 414 GB | 239 GB | 36% |
| E | 954 GB | 639 GB | **315 GB** | 33% 充足，所有模型 + venv + 源码 + 0.4.5 安装都放 E |

## 6. 关键环境变量

```powershell
$env:HF_HOME = 'E:\AI_Models\Qwen3-ASR'           # 模型缓存根
$env:HF_ENDPOINT = 'https://hf-mirror.com'        # 国内镜像（必须设）
$env:PYTHONHTTPSVERIFY = 0                        # 备选：跳过 SSL 验证（HF mirror 有时不稳）
$env:HUGGINGFACE_HUB_CACHE = 同 HF_HOME           # 旧版兼容
```

## 7. 已知坑（避免重复踩）

1. **edit 工具在含 mojibake 的 JSON 上**报告"Successfully replaced"但实际没改——**用 Python + regex 改**才稳（见 `fix_asr_engine.py`）
2. **huggingface_hub 在 import 时把 `ENDPOINT` 缓存到 module-level**——必须在 import 之前设 `HF_ENDPOINT`（audio.py 里就是这么做的）
3. **pip-unpack-* 临时目录不自动清**——C 盘满时手动删（`tempfile.gettempdir()` + `pip-unpack-*` 模式）
4. **PyInstaller 嵌入 funasr 1.3.14 bug 永远修不了**——必须走源码 + qwen_asr 替代
5. **YilanChengWen 0.4.5 嵌入 Python 3.12** 全局 pip 装不进去——必须用 venv
6. **路径含空格**（`E:\AI Agent\Models\FunASR`）让 funasr 1.3.14 抽风——新模型目录全用 `E:\AI_Models\Qwen3-ASR`（无空格）
