---
name: yilanchengwen-batch-transcribe
description: 一览成文（video-to-article）批量转写的 MCP 工具用法。当用户要批量转写视频/音频、查看还剩多少没处理、重新生成整理版文章、或排查转写与成稿失败时使用。
---

# 一览成文 · 批量转写

## 这个 Skill 解决什么

用户有一批课件/视频要转写成文章。项目 CLI 的批量能力已经完整
（断点续传 / 自动补救轮次 / 报告复查），但它是**一次性跑完就闷头到底**的脚本：
中途失败只能等它跑完；Agent 也拿不到结构化的"哪几个失败了、为什么"。

MCP 工具补的是**让 Agent 能驱动**这件事，不是重新实现批量。

## 服务怎么起

```powershell
cd <项目根>
.venv\Scripts\python.exe -m video_to_article.mcp_tools.server
```

端点 `http://127.0.0.1:8000/mcp`（**无尾斜杠**）。默认只监听本机。

## 四个工具（当前阶段全部只读）

| 工具 | 干什么 | 写盘 | 花钱 |
|---|---|---|---|
| `config_list` | 列出配置文件 + 四类档案（llm/asr/cover/host），密钥已脱敏 | ❌ | ❌ |
| `config_describe` | 当前引擎链、模型、图床能否公网访问、LLM 上限 | ❌ | ❌ |
| `engine_capabilities` | 各引擎并发上限、要不要公网 URL、可用模型名 | ❌ | ❌ |
| `transcribe_plan` | **只规划**：会做什么、跳过什么、为什么、跑前风险 | ❌ | ❌ |

## 推荐调用顺序

```
1. config_list         → 有哪些配置可选
2. engine_capabilities → 当前引擎有什么前置条件
3. transcribe_plan     → 先看清楚要干什么（不花钱）
4. 把 plan 结果讲给用户，确认后再谈执行
```

**不要跳过第 3 步直接跑。** 这一轮调试里用户连续两次踩了
「跑之前就该知道、却跑到一半才暴露」的坑。

## 配置怎么选

三层，从粗到细：

| 层 | 长什么样 | 作用 |
|---|---|---|
| 配置文件 | `configs/*.json`（可选目录） | 一整套环境。**省略 = 项目根的 config.json** |
| 档案 | `{"llm": "Deepseek（调）"}` | 临时激活某个服务档案，**仅本次调用生效** |
| overrides | `{"asr_engine": "funasr"}` | 单次参数覆盖 |

**默认配置就是用户当前在用的那份**，不动它也能跑。

传档案时 label 或 id 都行。找不到会明确报错并列出可用项，不会静默回落到默认。

## 三条硬规则

1. **绝不替用户改 `config.json`** —— 只读工具和转写调用一律不回写。
   Agent 帮用户跑一次任务，不该弄乱他 GUI 里当前选中的档案。
2. **密钥绝不回显** —— 所有工具返回都经 `mask_secret` 处理。
   你也不要把配置里的 api_key / token 抄进给用户的回复里。
3. **source 必须是原始视频所在目录** —— 不是 `data/local/` 下已抽取的音频。
   输出路径按「源文件父目录名」定位，用音频当源会算到别的目录，
   结果与已有输出对不上（全部显示 unprocessed）。`transcribe_plan` 会警告这种情况。

## 引擎能力速查

| 引擎 | 并发 | 前置条件 |
|---|---|---|
| `funasr` / `qwen_asr` / `whisper` | **1** | 本地推理，显存/线程独占 |
| `xf_asr` | 2 | 5 小时 / 500MB，不切段 |
| `custom_post` | 2 | **看 API 风格** |

**LLM 整理阶段恒为 1**，别改。原因不是技术上不能，而是代价大于收益：
触发限速（实测 MiniMax 429 Token Plan 上限）、单条长文就要 40+ 秒
（并发不会更快，只会让所有请求都慢到超时）、失败重试成本成倍放大，
而流水线耗时大头在 ASR 不在 LLM。

## 已知坑（这些是实测踩出来的，不是推测）

### DashScope 异步风格

- **只认 `-filetrans` 结尾的模型名**。`qwen3-asr-flash`（同步模型）打异步端点
  会报 `current user api does not support asynchronous calls`，
  错误里完全看不出是模型名问题。可用：
  `qwen-audio-3.0-asr-flash-filetrans` / `qwen3-asr-flash-filetrans` /
  `fun-asr` / `paraformer-v2`
- **音频必须是公网 URL**，程序不接受文件上传 → 借图床（R2 / OSS）换 URL
- **图床桶必须外部可读**，否则任务 `FILE_DOWNLOAD_FAILED`
  （R2 配 Public Development URL，OSS 设 ACL 公共读，
  或把 `presign_seconds` 设成 3600 走签名直链）

### 大模型

- **max_tokens 各家上限不同**，配超会被 400 拒绝，而这个 400 发生在**请求发出前**，
  界面上只剩「成稿没生成」。DeepSeek 实测上限 393216。
  超了会自动降级重试（从报错里解析真实上限），但更好是配对。
- 抓模型时 `/models` **只有一半厂商报告输出上限**（DeepSeek 有、MiniMax 没有）。
  抓取结果里没有就按服务商文档填。

### 请求头文件

- `custom_post_headers.txt` 每行只能是 `Key: Value`。
  **别把 curl 示例 / JSON / Python 代码粘进去** —— 程序会拒绝非法的 HTTP 头名，
  但更早的版本会把它们真的发出去，服务端直接掐连接（10054）。
- 只在服务端要求非 Bearer 鉴权时才写 `Authorization`，
  否则用「API Key」输入框。

## 后续阶段

P1 起会有 `transcribe_from_raw`（只重整不重转）、`transcribe_recheck`，
P3 起会有 `transcribe_batch_start` / `transcribe_status` / `transcribe_cancel`
（异步台账 + 心跳）。届时本文件会补充写操作的安全边界。

在这些工具出现之前，要补整理版就引导用户跑 CLI：

```
python transcribe.py --from-raw <raw.md 路径> --prompts general_article
```