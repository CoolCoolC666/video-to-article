<div align="center">

<a href="https://linux.do/" title="LINUX DO 社区">
  <img src="resources/linux-do.svg" width="88" height="88" alt="LINUX DO" />
</a>

### [LINUX&nbsp;DO](https://linux.do/)

**本项目在 [LINUX DO](https://linux.do/) 社区分享与交流** · 欢迎同好围观、反馈、吹水

[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-1c1c1e?style=for-the-badge&labelColor=ffb003&logoColor=white)](https://linux.do/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](./LICENSE)

</div>

---

# 一览成文 YilanChengWen

把 **在线视频 / 本地音视频** 转写为文字，并可选用大模型整理成结构化文章。

| 项 | 内容 |
| --- | --- |
| 中文名 | **一览成文** |
| 英文名 | **YilanChengWen** |
| Python 包 | `video_to_article` |
| 许可证 | [MIT](./LICENSE) |
| 版本 | 见 `src/video_to_article/__init__.py` |
| 作者博客 | [blog.yilanapp.com](https://blog.yilanapp.com/) |

## 能做什么

- 多平台链接下载（B 站 / YouTube / 抖音 / 小红书 / 微博等，能力随 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 更新）
- 本地音频、视频直接转写
- 默认本地 ASR（FunASR SenseVoice），可选 Whisper
- 大模型按**提示词模板**生成成稿；也可仅下载、不转写
- 桌面 GUI（单条 / 批量 / B 站搜索 / 仅下载 / 补跑工具）

### 成稿类型由提示词决定（重点）

**产出的文章形态完全取决于你配置的提示词**，可高度自定义：

| 目录 | 作用 |
| --- | --- |
| `prompts/articles/` | 成稿模板（GUI 下拉可选） |
| `prompts/system/` | 系统基础提示（默认不在界面展示） |

- **默认模板** `snack_recipe`：面向**美食视频**，整理成食谱向结构
- 你可按视频类型自行新增 `.md` 模板（教程、访谈、评测、科普……），无需改代码
- 不同模板 = 不同文章结构与语气；扩展方式就是往 `prompts/articles/` 加文件

## 环境要求

- Windows 10/11（GUI 与打包脚本按 Windows 编写；CLI 也可在其他系统开发）
- Python **3.10+**（推荐 3.12）
- **FFmpeg**（开发环境可装系统版；绿色包可内置 `ffmpeg/`）

## 软件下载与离线模型（弱网推荐）

若不便从源码构建，或网络不稳定，可用预编译绿色包 + FunASR 语音模型。

链接：https://pan.quark.cn/s/6971a6e70b44

说明：

- 分享内容面向 **弱网 / 不便联网下模型** 的场景，含 **软件本体** 与 **转写用语音模型**（体积较大）。
- 也可在 GitHub [Releases](https://github.com/DEKVIW/video-to-article/releases) 下载对应版本的主程序 zip；模型包较大时，弱网优先用网盘。

### 解压建议

| 程序解压位置 | 建议 | 模型默认位置 |
| --- | --- | --- |
| **纯英文路径**（如 `D:\Apps\YilanChengWen\`） | **强烈推荐** | 程序旁 `models\funasr\` |
| **含中文路径**（如 `…\我的项目\…`） | 能运行，但不理想 | 不会用中文路径当「可靠加载路径」，见下表 |

底层 FunASR / SentencePiece 对 **含中文的模型文件路径** 兼容差，程序会尽量把缓存放到 **纯英文路径**。

### 离线模型怎么放

1. 解压绿色包到目标目录，双击 `YilanChengWen.exe`。
2. 解压模型包，将其中的 **`models` 文件夹** 合并到 `YilanChengWen.exe` **同级**（不要多套一层目录）。
3. 确认存在：

   `models\funasr\models\iic\SenseVoiceSmall\model.pt`

4. 仅「下载视频 / 字幕」、不做本地转写时，**不需要**语音模型。

| 场景 | 能否直接用 |
| --- | --- |
| 程序在 **英文路径**，模型在 exe 同级 `models\funasr\` | ✅ 标准用法 |
| 程序在中文路径，模型放到同盘 **`盘符:\YilanChengWenData\models\funasr\`** | ✅ 与自动策略一致（路径须为纯英文） |
| 设置 → 转写 →「FunASR 模型目录」填 **纯英文** 路径 | ✅ 自定义优先 |

**中文路径下不要默认「拷到程序同级就一定能读」**；最省事仍是：程序解压到英文目录 + 模型合并到 exe 旁。

### 模型实际落在哪

- **英文程序路径**：`程序目录\models\funasr\`
- **中文程序路径**（自动避开中文路径加载）：
  - 优先：同盘 `盘符:\YilanChengWenData\models\funasr\`
  - 回退：`%LOCALAPPDATA%\YilanChengWen\models\funasr\`
- **自定义**：设置中的「FunASR 模型目录」或环境变量 `YILAN_FUNASR_DIR` / `VQE_FUNASR_DIR`（须纯英文）
- 运行日志里「FunASR/ModelScope 模型缓存目录」= 本次实际使用的目录

首次联网转写也会把模型下到上述缓存位置（约 1GB 级磁盘）。

## 本地环境启动

在项目根目录 PowerShell：

```powershell
# 1. 虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip

# 2. 安装本包 + GUI 依赖
pip install -e ".[gui]"
# 或：pip install -r requirements.txt
#     pip install "PySide6>=6.6" "PySide6-Fluent-Widgets>=1.6"

# 3. 配置（勿把含密钥的 config.json 提交到 Git）
copy config.example.json config.json
# 编辑 config.json：填入 LLM API Key 等

# 4. 启动 GUI
python gui_app.py
# 或
.\run_gui.bat
# 或
python -m video_to_article.gui
```

命令行入口（可选）：

```powershell
python transcribe.py --help
# 可编辑安装后也可：
# video-to-article --help
```

> 首次本地转写会下载 FunASR 模型（体积较大）。开发与绿色版均可使用离线模型包；放置规则见上文「绿色版下载与离线模型」。建议项目/程序路径尽量使用纯英文。

## 打包构建

```powershell
.\.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File scripts\build_gui_onedir.ps1
```

本地产物（默认 **不进 Git**，见 `.gitignore`）：

| 路径 | 说明 |
| --- | --- |
| `dist/YilanChengWen/` | 最新可运行目录 |
| `dist/releases/YilanChengWen-x.y.z.zip` | 对外分发主程序 zip（一般不含语音大模型权重） |
| `dist/releases/YilanChengWen-models-funasr-sensevoice.zip` | 可选：离线 FunASR 模型（需另打） |

```powershell
# 仅打 FunASR 离线模型包（可选）
python packaging/make_models_funasr_zip.py
```

用户：解压主程序 zip → 双击 `YilanChengWen.exe`；需要离线转写时按上文合并 `models`（注意英文/中文路径差异）。

## 源码结构

```text
.
├── src/video_to_article/   # 核心包（下载 / 转写 / 成稿 / GUI）
├── prompts/                # 提示词（articles 成稿 + system 基础）
├── packaging/              # PyInstaller / 元数据 / 入口
├── scripts/                # 打包脚本
├── resources/              # 图标、社区徽章资源
├── config.example.json     # 配置示例
├── gui_app.py              # 开发启动 GUI
├── transcribe.py           # 开发启动 CLI
├── pyproject.toml
├── LICENSE
└── README.md
```

**不会**进入版本库的内容（见 `.gitignore`）：`.venv/`、`build/`、`dist/`、`docs/`、`data/`、`models/`、`output/`、`logs/`、个人 `config.json`、大体积 FFmpeg 二进制等。

## 技术栈

- Python 3.10+ · yt-dlp · FunASR / faster-whisper
- LLM：OpenAI / Anthropic 兼容接口
- GUI：PySide6 · QFluentWidgets
- 打包：PyInstaller（onedir）

## 免责声明

请仅处理你有权使用的音视频内容，并遵守各平台服务条款与当地法律法规。下载、转写与二次创作后果由使用者自行承担。

## 社区与反馈

- 社区讨论：[LINUX DO](https://linux.do/)
- 博客：[blog.yilanapp.com](https://blog.yilanapp.com/)

欢迎 Issue / PR。较大改动建议先开 Issue。提交前请确认未包含 API Key、`config.json`、个人音视频与 `dist/`。

## 本 fork 改动

本 fork ([CoolCoolC666/video-to-article](https://github.com/CoolCoolC666/video-to-article)) 基于 [DEKVIW/video-to-article](https://github.com/DEKVIW/video-to-article) v0.4.5，从零**新增 Qwen3-ASR 引擎支持**（DEKVIW 上游仅有 FunASR / Whisper）并围绕它做了一系列增强。

### 新增：Qwen3-ASR 引擎

上游 `src/video_to_article/media/` 目录只有 `__init__.py` / `audio.py` / `download.py` / `ffmpeg_tools.py` / `thumbnails.py`，**没有**任何 Qwen3-ASR 相关代码。fork **新增** `src/video_to_article/media/qwen_asr.py`（~520 行），提供 `transcribe_audio_with_qwen_asr()` 接口，与现有 `funasr` 引擎并列。

### 新增：讯飞听见云端识别（xf_asr）

fork **新增** `src/video_to_article/media/xf_asr.py`（~590 行），提供 `transcribe_audio_with_xf_asr()` 接口，**真实 API 已跑通**（2026-09-27：5.74MB / 25 分钟 mp3 实测转写成功落盘 `raw.md`）。

| 改动 | 内容 | 为什么改 |
|------|------|----------|
| **长语音转写 REST API** | 调 `https://raasr.xfyun.cn/v2/api/upload` + `getResult` 两步 | 上游没有云端 ASR backend，本地 FunASR/Qwen3-ASR 在噪声大 / 方言 / 多人对话场景识别率不够 |
| **鉴权签名（MD5 + HMAC-SHA1 + base64）** | `signa = base64(HMAC-SHA1(secret_key, MD5(app_id + ts).hexdigest()))`——**漏 MD5 一步** server 报 26601 `signa verify fail` | 长语音 lfasr 鉴权算法，按 [caitongbo/Speech-to-Text](https://github.com/caitongbo/Speech-to-Text) 等 5+ demo 一致实现 |
| **鉴权只用 APPID + SecretKey 两件套** | 长语音 lfasr 控制台只下发 APPID + SecretKey，**不**需要 APIKey | APIKey 是短音频 ifasr 实时接口的字段，GUI 多加它是常见错误 |
| **upload 用 URL query string + raw bytes（不 multipart）** | 鉴权参数（`appId`/`signa`/`ts`/`fileSize`/`fileName`/`sliceSize`/`language`/`duration`）全在 URL query，body 是 raw 音频字节，`Content-Type: application/json` | `requests.post(files=...)` 走 multipart，讯飞返回 26600「音频使用表单方式上传」——中文错误描述是「别走 multipart」的反向提示 |
| **upload 必传 `duration` 字段** | `duration = str(int(audio_duration_seconds))`，demo 一致必填 | 漏传 server 仍报 26600 |
| **getResult 响应字段** | `content.orderInfo.status`（**不是** `content.taskStatus`），状态码 `{3}` 处理中 / `4` 完成 | 之前 demo 反推错字段名 + 状态码常量，搞了 4 个 KeyError 才订正 |
| **订单 ID 字段** | `content.orderId`（**不是** `content.taskId`） | upload 成功后 `result["content"]["orderId"]`，query getResult 时回传 `orderId` |
| **orderResult 双重 json.loads** | `content.orderResult` 是 **JSON 字符串**（不是 dict），外层 parse 后拿 `lattice[]`/`lattice2[]`；每个 segment 的 `json_1best` 又是字符串或 dict，再 parse 后拿 `st.rt[].ws[].cw[].w` | 之前按猜的 `resultMap[sid].text` 实现是纯错；按 caitongbo（字符串）+ csdn（dict）两个 demo 双重兼容 |
| **说话人分离**（可关） | 勾选后 upload 带 `roleType=1` + `roleNum=N`，每段加 `【说话人1】` 标签 | 两人对话（访谈 / 对话课 / 师生问答）需要区分谁在说。标准版 lfasr 只支持 `roleType=1`；`roleNum=0` 自动盲分，1-10 指定人数 |
| **时间戳输出**（可关） | 每段加 `[MM:SS]` 前缀（读 `st.bg` / `lattice2.begin`，本来就在响应里只是之前没解析）；超 1 小时自动带小时位 | 做文章时定位段落、核对哪段说错了很方便 |
| **垂直领域优化** | `pd=edu` / `court` / `finance` / `medical` / `tech` / `sport` / `gov` / `game` / `ecom` / `car`，默认 `edu` | 讯飞为各领域准备了定制声学+语言模型，`edu` 对课堂实录术语识别率提升最明显 |
| **口语规整**（可关） | `eng_colloqproc=true`，自动去「嗯/啊/呃」+ 口癖重复 | 课堂实录/访谈口癖多，去掉后给下游 LLM 成稿的文本干净很多。零成本零权限 |
| **增强参数失败自动降级** | 增强参数被账号拒绝（`26600`/`26610`）时自动退回最小参数集重试一次，只打 WARNING 不中断转写 | 官方 Java SDK 明写 `role_type`「只有在开通了角色分离功能的前提下才会生效」——可选能力不能变成硬失败 |
| **Mock 模式（默认开启）** | 凭证缺失或显式 `mock=true` 时返回 fake 中文文本，不调网络 | 无凭证也能跑通流程 + GUI 验证，避免启动时悄悄调 API 失败 |
| **长音频客户端不切段** | 客户端整段直传，讯飞 server 自己处理长音频（≤500MB / 5h） | 客户端切 wav 头丢失 + 边界静音问题反而引入失败；server 端 OK 不切更稳 |
| **atexit 兜底清理** | 进程退出清残留 `xf_asr_chunks_*` tempdir | GUI 强杀 / 进程崩溃场景（虽然现在不切段，保留兜底） |
| **requests.Session cache** | 模块级 session 复用连接池 | upload + poll 多次复用 TCP 连接 |

**配置位置**：「设置 → 转写 → 讯飞听见 高级」GroupBox。CLI 用 `--asr-engine xf_asr`。

**凭证获取**：https://www.xfyun.cn/ 注册 → 控制台 → 「语音转写（长语音）」→ 创建应用 → **APPID + SecretKey**（**只这两项**，不需要 APIKey）。新注册免费 5 小时试用包。

#### 凭证齐全但还是走 Mock 的排查

`transcribe_audio_with_xf_asr()` 入口按三种情况打不同日志，让用户一眼看出走哪条分支：

| 情况 | 日志级别 | 含义 |
|------|----------|------|
| 凭证齐全 + Mock 勾上 | **WARNING** | 你填了凭证但忘了取消 Mock 勾选——**将走 Mock 不调真实 API** |
| Mock 显式勾上（无论凭证） | INFO | 本地调试模式 |
| Mock 未勾 + 凭证缺失 | **WARNING** | 自动降级 Mock，需补 APPID + SecretKey |

另外 GUI 在「默认 ASR 引擎」下拉切到 `xf_asr（讯飞听见）` 时会**自动取消 Mock 勾选**（前提是凭证齐全），并弹窗提醒，避免「填了凭证但忘了取消勾选」的陷阱。

#### 残留冗余字段自动清理

旧版本（6 字段）遗留的 `xf_asr.api_key` 字段在 2026-09-27 重构后**不再读取也不再写出**，但磁盘 config.json 可能还残留。下次点 GUI 「保存设置」时 `_save()` 会自动 `xf_asr.pop("api_key", None)` 清理冗余字段。

#### 真实 API 跑通的契约订正过程（开发者参考）

xf_asr 集成期间按以下顺序踩坑订正协议层（每个 fix 都加 smoke test 防回归，共 10 个测试）：

| Commit | 现象 | 根因 | 修法 |
|--------|------|------|------|
| `b1b585e` | 真实 API 返回 26600 | `requests.post(files=...)` 走 multipart | URL query string + raw bytes + `Content-Type: application/json` |
| `8656f38` | 仍 26600（切段 wav 头丢失 + duration 缺失） | 客户端过度切段 + 漏传 `duration` | 关闭 `_split_audio_for_long` 切段，加 `duration` 字段 |
| `1a40f6f` | 上传成功拿到 order_id，但 `KeyError 'taskId'` | 协议层用错键名 | `result["content"]["orderId"]`（讯飞拼写） |
| `60be01f` | poll 启动后撞 `KeyError 'taskStatus'` | 响应字段路径猜错 + 状态码常量错 | `content.orderInfo.status`（3 处理中 / 4 完成）+ 状态码常量订正 |
| `cee2c11` | status=4 后 `_parse_result_content` 仍可能 KeyError | `resultMap[sid].text` 是猜的，实际 `orderResult` 是 JSON 字符串 + lattice/lattice2 双重 parse | `json.loads(orderResult)` + 兼容 `lattice`（caitongbo）+ `lattice2`（csdn）+ `json_1best` 字符串/dict 双重兼容 |

教训：参照 demo 写代码必须字段名 / 路径 / 常量全对齐，**不能凭直觉**；每订正一处加 smoke test 验证契约不回归。

#### 说话人分离：讯飞支持 / Qwen3-ASR 不支持（2026-10-02 实测查证）

想在转写里区分「谁在说话」，四个引擎的能力**不一样**：

| 引擎 | 说话人分离 | 说明 |
|------|-----------|------|
| **xf_asr（讯飞）** | ✅ 支持 | 需账号开通「角色分离」权限；未开通时本 fork 自动降级不报错 |
| **qwen_asr（Qwen3-ASR）** | ❌ **不支持** | 多个独立实测一致：「目前版本不支持自动说话人分离，会把多人对话识别为连续文本」；官方把「需要说话人分离的会议记录」列为谨慎使用场景 |
| **funasr** | ⚠️ 需额外挂 CAM++ | 单独 `AutoModel(..., spk_model="cam++")` 才能分离，SenseVoice 自身不产角色号。本 fork 暂未接 |
| **whisper** | ❌ 不支持 | 需外挂 pyannote 等第三方模型 |

> **Qwen3-ASR 实测原文**（两处独立来源一致）：
> 「Qwen3-ASR目前版本不支持自动说话人分离。它会把多人对话识别为连续的文本,不区分不同的说话者。」
> 「4人圆桌讨论（含打断、抢话、语气词），模型未做说话人分离，但通过上下文连贯性，将发言逻辑自动归并为段落」
>
> 来源：[Qwen3-ASR 服务实测](https://blog.csdn.net/)、[Qwen3-ASR-0.6B 实测](https://blog.csdn.net/weixin_36296444/article/details/157788729)

**关于「省算力」的澄清**：xf_asr 的说话人分离是**云端**能力，不占本地 GPU，开关的意义是「要不要为这个能力承担不准确风险」；Qwen3-ASR 是**根本做不到**，开关没意义；真要在本地分离只能上 FunASR + CAM++，那才会多占显存——如果哪天要接，算力开关做在那条路上。

### Qwen3-ASR 引擎的健壮性增强

| 改动 | 内容 | 为什么改 |
|------|------|----------|
| **长视频自动切片** | 音频 > **7.5 分钟**（`CHUNK_THRESHOLD_SEC`）自动切 **5 分钟/段**（`CHUNK_SEGMENT_SEC`）<br>ffmpeg 切到 `%TEMP%\qwen_asr_chunks_xxxx\` 临时目录<br>Qwen3-ASR 逐段转写，最终合并成完整转写结果<br>转写完 / 异常路径 / 进程崩溃均清理（`atexit` 兜底扫 `%TEMP%\qwen_asr_chunks_*`） | Qwen3-ASR 1.7B 对长音频单次推理必 OOM / 截断；分段转写每段都在阈值内更稳<br>上游 DEKVIW 没这功能，长视频直接转写是 1 小时整段发过去 |
| **Device fallback** | `auto` / `cuda` / `cpu` 三档 GUI 暴露<br>`auto` = CUDA 可用走 `cuda:0` + `max_memory={0: "7GiB"}` 限显存（适配 12GB 显卡防 OOM），不可用静默降级 CPU<br>`cuda` = 强制 CUDA；不可用时显式报错（不静默降级，避免以为跑 GPU 实际跑 CPU）<br>`cpu` = 强制 CPU（NVIDIA 驱动故障 / GPU 满载 / 远程控制等场景） | 默认 `device_map="auto"` 12GB 显卡跑 1.7B 模型必 OOM；显式 `max_memory` 才能稳跑 |
| **Language 白名单** | Qwen3-ASR 0.0.6 API 仅支持 30 种语言，`Auto` 必报 `Unsupported language`<br>GUI 暴露 4 种：Chinese / English / Japanese / Korean<br>用户填非法值时自动降级 `Chinese` + WARNING 日志<br>**不能**传 `Chinese+English` 混合（Qwen3-ASR API 强制单语种） | GUI 不加白名单兜底，用户填 "Auto" 必撞 Unsupported language 报错 |
| **手动「释放 ASR 模型」按钮** | 转写完成后 PyTorch 默认不释放显存，GPU 仍占 3-4GB<br>按钮一键释放（`del model + torch.cuda.empty_cache()`）<br>状态 label 5s 刷新：蓝/灰 显示当前加载的 model_id | 转写完跑 SD / 玩显卡游戏经常撞显存墙 |
| **「清理临时缓存」按钮** | 转写时产生的 ASR 音频分片（`%TEMP%\qwen_asr_chunks_*`）一键清理 + 显示腾出 MB | 长视频分片缓存可达 GB 级 |

**配置位置**：「设置 → 转写 → Qwen3-ASR 高级」GroupBox。CLI 用 `--qwen-device` 参数。

### LLM / GUI 通用

| 改动 | 内容 | 为什么改 |
|------|------|----------|
| **LLM `max_tokens` 扩到 1,000,000** | 上游默认 12,000 对长视频转写 + 长成稿模板不够<br>GUI `setRange(256, 1_000_000)` + tooltip 解释 | 长视频转写文本可达 100K+ 字符 + 提示词模板 5K 字符 + 成稿 50K 字符，12K 必截断 |
| **「转写」Tab GUI 化** | Qwen3-ASR 5 字段（`model_id` / `context_file` / `hf_home` / `language` / `device`）直接暴露在「设置 → 转写 → Qwen3-ASR 高级」GroupBox + 文件浏览按钮 | 改一次不用手动编辑 JSON，错误率低 |

### 健壮性

- **`closeEvent` 强等 worker**：上游关闭 GUI 时 worker 线程不一定退出，可能卡死
  - 本 fork：`thread.wait(10000)` + `terminate()` 兜底
- **`atexit` 兜底清理 tempdir**：进程任何路径退出（GUI 关闭 / 异常 / kill）都会清理 `%TEMP%\qwen_asr_chunks_*`

### 测试 / 工程

- **9 个 smoke 脚本移到 `tests/`**（`smoke_settings / fallback / language / jp_kr / release / cleanup / thinking / xf_asr / xf_e2e`）
  - 验证 GUI 字段读写、device fallback、language 兜底、释放按钮、清理按钮、xf_asr 鉴权签名 / upload 协议 / poll 状态码 / orderResult 解析 / 说话人分离 / 增强参数降级
  - 9 套全过，共 60+ 断言
  - `tests/README.md` 说明运行方式（从仓库根跑）
- **`.gitignore` 加严**：
  - 新增 `pip-unpack-*/` `run_e2e_main.log` `__tmp_*` `*.bak` `config.json.bak*`
  - 一次性 fork 脚本（`fix_*.py` `switch_*.py` `strip_*.py` `run_qwen_asr.py` `transcribe.py`）默认不入仓
- **`config.example.json` 模板占位**：`api_key="your-api-key-here"`，新用户 fork 后改 `config.json` 时不会误以为已经是占位

### 通用改进候选（可考虑回提上游 PR）

下列改动不依赖 fork-specific 场景，对所有 NVIDIA 用户 / 长视频用户都受益，可以拆 PR：

1. **Qwen3-ASR device fallback** — 任何 NVIDIA 显卡用户受益（不限 12GB）
2. **Qwen3-ASR language 白名单** — 上游用户也会踩到 Auto 报错
3. **LLM max_tokens 1M** — 上游默认 12K 太小，长视频必截断
4. **GUI 字段暴露**（5 字段从 config.json 提升到 GUI）— 降低使用门槛

### Fork-specific（不打算回上游）

- 中文 README 风格的本章节（上游 README 是中文，但不希望 PR 改 README）
- 兼容含 `~` 的 Windows 路径（`E:\000~\YilanChengWen-src`）

## License

[MIT License](./LICENSE) © 2026 一览成文 YilanChengWen contributor(s)
