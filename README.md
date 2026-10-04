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
- 默认本地 ASR（FunASR SenseVoice，可挂 CAM++ 分离说话人），可选 Whisper / Qwen3-ASR / 讯飞听见 / 自定义 POST 云端接口
- 说话人分离 + 时间戳：`funasr`（挂 CAM++）、`xf_asr`（云端）、`custom_post`（端点可配）三个引擎都支持，输出格式统一
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

本 fork ([CoolCoolC666/video-to-article](https://github.com/CoolCoolC666/video-to-article)) 基于 [DEKVIW/video-to-article](https://github.com/DEKVIW/video-to-article) v0.4.5，从零**新增 3 个 ASR 引擎**（Qwen3-ASR / 讯飞听见 xf_asr / 自定义 POST custom_post）并围绕它们做了一系列增强。

### ASR 引擎总览

| 引擎 | 本地/云端 | 单请求上限 | 长音频策略 | 说话人分离 | 鉴权 | 端点可配 |
|------|----------|-----------|-----------|-----------|------|---------|
| `funasr` | 本地 | 受本机算力 | 本地切段（无限制） | ✅ 挂 CAM++（约 28MB） | 无 | — |
| `qwen_asr` | 本地 | 受显存 | **必须切**（>7.5min 切 5min） | ❌ 不支持 | 无 | — |
| `xf_asr`（讯飞） | 云端 | **5 小时 / 500MB** | **不切段**（整段直传） | ✅ 云端 `roleType` | APPID + SecretKey | ❌ |
| `custom_post`<br>· OpenAI 兼容 | 云端/自建 | **500 秒 / 50MB** | **必须切**（>450s 切 450s） | ✅ `verbose_json` | API Key 或请求头文件 | ✅ **可配** |
| `custom_post`<br>· DashScope 异步 | 云端 | **12 小时 / 2GB** | **不切段**（整段直传） | ✅ `diarization_enabled` | API Key 或请求头文件 | ✅ **可配** |

> ⚠ **切段策略不能按「本地 / 云端」二分**，必须看每个引擎的**真实上限**。
> 讯飞和 `custom_post` 都是云端，但默认上限差了 8 倍（5 小时 vs 500 秒），策略完全相反。
> 同一个 `custom_post` 引擎内部还有两种风格（500 秒 vs 12 小时，差 86 倍），
> 所以**切段阈值也是按风格分开取值的**（`MAX_DURATION_SEC` vs `DASHSCOPE_MAX_DURATION_SEC`）。

### 新增：自定义 POST 云端识别（custom_post）

fork **新增** `src/video_to_article/media/custom_post_asr.py`。最初是「MiniMax STT 专用」，
后按需求**泛化成端点 + 请求头都可配**，因此既能指向 MiniMax 官方，也能指向
私有部署 / 自建网关 / 任何兼容这套契约的服务。

```
默认端点   POST https://api.minimax.cn/v1/speech_to_text   ← GUI 可改
鉴权       Authorization: Bearer <API Key>     ← 也可改在请求头文件里
附加请求头  从程序内 custom_post_headers.txt 读  ← 用户可直接编辑
```

⚠ **同一个框要接两类协议完全不同的服务**，所以 2026-10-04 加了「API 风格」下拉：

| 风格 | 协议 | 音频怎么送 | 单请求上限 |
|------|------|-----------|-----------|
| **OpenAI 兼容**（默认） | `multipart/form-data` **上传文件本体**，一次请求直接返回 `text` + `segments` | 上传 | 500 秒 / 50 MB |
| **DashScope 异步** | `application/json`，**音频必须是公网 URL**（不接受文件上传） | 借图床中转拿 URL | 12 小时 / 2 GB |

> **填错风格的典型症状**：拿 OpenAI 兼容那套（multipart + 完整路径）去打 DashScope，
> 会直接被服务端**重置连接**（实测 `ConnectionResetError 10054`），不返回任何可读错误。

#### DashScope 异步（阿里云百炼 Qwen3-ASR / Paraformer）

```
提交      POST {base}/services/audio/asr/transcription   （X-DashScope-Async: enable）
轮询      GET  {base}/tasks/{task_id}
结果      output.results[].transcription_url  →  下载 JSON（24 小时有效）
```

| 改动 | 内容 | 为什么改 |
|------|------|----------|
| **借图床换公网 URL** | `upload_audio_for_public_url()` 复用 `cover.upload_image_to_host`（EasyImage 图床能收 mp3）；没配图床时给**三选一**可操作提示 | DashScope 文件级 ASR **硬性要求公网 URL**，不接受文件上传 |
| **图床支持 R2 / OSS** | `image_host.provider` 可选 `easyimage`（默认）/ `r2` / `oss`，后两者走 S3 兼容 API（boto3，懒加载可选依赖） | EasyImage 没有公网直链语义，而这里必须产出「外部能 GET 的地址」。见下方「图床配置」 |
| **URL 必须 percent-encode** | `urllib.parse.quote(safe=":/?#[]@!$&'()*+,;=")` | 官方明确警告：不编码会报 `InvalidFile.DownloadFailed`。用户的课件文件名几乎都是中文+空格 |
| **三步状态机** | 提交 → 轮询 → 下载；轮询**用 GET**（官方 Python 示例是 POST，已用 GET 实测兼容） | 长音频异步返回，避免单连接长挂 |
| **⚠ 整体成功但子任务失败** | 单独检查 `results[].subtask_status` | 官方明确：整体 `SUCCEEDED` 时子任务**仍可能 `FAILED`**。不查就会拿着一个不存在的 URL 去下载 |
| **毫秒 → 秒** | `begin_time`/`end_time` 官方单位是**毫秒**，归一时 `/1000` | 与 OpenAI 兼容风格的**秒**相反，混用会让时间轴差 1000 倍 |
| **`language_hints` 只给 paraformer** | 模型名含 `paraformer` 才下发 | 其他模型传了会 400 |
| **参数条件分支** | `channel_id:[0]`（多声道会分别计费）<br>`diarization_enabled` / `speaker_count`(2-100) / `disfluency_removal_enabled` | 都取自官方「任务提交接口」文档 |
| **内联结果兜底** | 无 `transcription_url` 但有 `output.transcription` → 走 `inline:` 前缀 | 有些部署直接内联返回 |
| **GUI 端点实时预览** | 填 Base URL 后下方实时显示 `→ 实际请求：https://...` | 解决「URL 到底填到哪一层」的困惑（**只填到 `/api/v1`**，路径由程序拼） |
| **拼路径有防御** | `build_base()` 会剥掉已填的尾部路径 | 填了完整路径也不会拼成 `/speech_to_text/speech_to_text` |
| **分风格切段** | DashScope 走 12h/2GB 阈值，**实际不切段** | 500s 阈值对 DashScope 是错的 |

**模型名**（DashScope 必填，OpenAI 兼容风格下该字段被忽略）：

> ⚠ **最容易踩的坑：同系列两个名字只差一个后缀，含义完全不同。**
>
> | 模型 | 音频时长 | 调用方式 |
> |---|---|---|
> | `qwen3-asr-flash` | ≤ **5 分钟** | **同步**（一次请求直接返回）|
> | `qwen3-asr-flash-filetrans` | ≤ **12 小时** | **异步**（提交 → 轮询 → 下载）|
>
> 本引擎走的是**异步**流程，填了前者会被服务端拒绝，而错误只有一句英文
> `current user api does not support asynchronous calls`，**完全看不出是少抄了后缀**。
> 程序已加提交前预检：填了同步模型会直接告诉你该用哪个（且**不会发出请求**，不白花钱）。

异步风格下可用的模型名（**以你手上官方文档为准**，官方会更新版本号）：

```
qwen-audio-3.0-asr-flash-filetrans   Qwen-Audio，≤2GB / 12h
qwen3-asr-flash-filetrans            Qwen3，≤2GB / 12h
fun-asr                              Fun-ASR
paraformer-v2                        仅北京地域
```

> ⚠ **实测结论（2026-10-04，同一独享部署端点 + 同一把 Key）**：
> 这句 403 绝大多数情况是**模型名填成了同步模型**，不是部署问题：
>
> | 模型名 | 结果 |
> |---|---|
> | `qwen-audio-3.0-asr-flash-filetrans` | 200，正常拿到 task_id ✅ |
> | `qwen3-asr-flash-filetrans` | 200，正常拿到 task_id ✅ |
> | `qwen3-asr-flash`（同步） | 403 `...does not support asynchronous calls` ❌ |
>
> 官方文档另提到：调用**独享部署**（端点形如 `<自定义>.maas.aliyuncs.com` /
> `qianwenaiapi.com`，公共端点是 `dashscope.aliyuncs.com`）报这句时，
> 可能该部署只支持同步。**但按上面的实测，换对模型名后即可正常提交。**

> ⚠ **开了说话人分离后，官方建议音频不超过 2 小时**，否则可能失败或超时
> （`diarization_enabled` 本身仅支持**单声道**）。
> `speaker_count` 只是「尽量输出这个人数」的提示，**不保证**一定输出。

#### 图床配置：给 DashScope 提供音频公网 URL

DashScope 不接受文件上传，所以音频必须先变成一个 **DashScope 服务端能直接 GET 的 URL**。
三条路，在「设置 → 图床」里切 `provider` 即可：

| provider | 填什么 | 说明 |
|---|---|---|
| `easyimage`（默认） | API URL + Token | 传统 multipart 图床 |
| `r2` | bucket + endpoint + 密钥 | Cloudflare R2，**出网流量永久免费** |
| `oss` | bucket + endpoint + 密钥 | 阿里云 OSS，与百炼同云时拉取最快 |

**选 R2 还是 OSS？**

- **R2**：10 GB/月存储免费，**Egress 完全免费**（它最大的卖点）。
  endpoint = `https://<AccountID>.r2.cloudflarestorage.com`（AccountID 在 R2 控制台右侧）。
- **OSS**：出网按量计费（几 MB 音频≈分文不取），但 **DashScope 服务器在阿里云境内**，
  拉同云 OSS 是内网速度，比跨境拉 R2 稳。endpoint = `https://oss-cn-<地域>.aliyuncs.com`。

> 音频只有几 MB，**成本上两者都不用担心**；要纠结就按「拉取稳定性」选 OSS。

配置步骤（GUI「设置 → 图床」，把 Provider 敲成 `r2` 或 `oss`，S3 字段组会自动亮起）：

```
provider           = oss              # 或 r2
bucket             = 你的桶名
endpoint           = https://oss-cn-beijing.aliyuncs.com
region             = cn-beijing       # 建议与百炼同区
access_key_id      = xxx
access_key_secret  = xxx
public_base_url    = https://<bucket>.oss-cn-beijing.aliyuncs.com
presign_seconds    = 3600
```

> ⚠ **桶必须外部可读**，否则 DashScope 拉到的是 403 / `InvalidFile.DownloadFailed`：
> - OSS：把对象 ACL 设成 public-read，或用上面的 `public_base_url` + 公开读
> - R2：给 bucket 开 **Public Development URL**（`https://pub-xxxx.r2.dev`）填进 `public_base_url`
> - **不想开公读**：把 `presign_seconds` 设成 3600，走带签名的临时直链（私有桶也能用，更安全）

密钥也可以走环境变量（不写进 config.json）：`OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET`
（R2 对应 `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`）。

> boto3 是**可选依赖**，只 r2/oss 用得到。不装也不影响 AI 封面和 easyimage 图床——
> 需要时程序会提示 `pip install boto3`。

| 改动 | 内容 | 为什么改 |
|------|------|----------|
| **端点可配** | `endpoint` 配置项 + GUI 输入框；非 `http(s)` 开头提前抛错 | 指向任意兼容服务；格式错误早报错比服务端 404 好查 |
| **请求头走可编辑的 text 文件** | 程序目录 `custom_post_headers.txt`（或 GUI 指定路径）<br>格式：每行 `Key: Value`，`#` 注释（支持行内），空行忽略<br>没冒号的行忽略 + WARNING（不静默吃掉用户输入）<br>首次使用自动生成模板（**只写占位说明，不写密钥**）<br>「生成模板」按钮遇到已存在文件**不覆盖**（不冲掉手写内容）<br>**日志只打印键名，绝不打印值** | 有些服务要额外的网关头 / 非 Bearer 鉴权，塞进 GUI 文本框既不灵活又容易把密钥写进 config.json |
| **`.gitignore` 加请求头文件** | `custom_post_headers.txt` / `*_headers.txt` | 文件里可能有 `Authorization` / 网关 token，**绝对不能进仓** |
| **凭证双来源** | ① API Key 输入框 → `Authorization: Bearer <key>`（默认）<br>② 请求头文件里直接写 `Authorization`（支持非 Bearer）<br>文件里的值**覆盖**程序生成的；写空值 = 删掉该头<br>「有没有凭证」也同时看两处 | 私有部署常用 `Token xxx` / 自定义签名，不是 Bearer |
| **真 multipart 上传** | `files={"file": ...}` + form 字段 `model`/`response_format`/`timestamp_level`/`stream` | **与讯飞正好相反**：讯飞必须 query string + raw body（走 multipart 报 26600），本引擎就是 multipart。写新引擎时别把讯飞那套习惯带过来 |
| **`language` 走 HTTP header** | BCP-47 标签放请求头，**不是 form 字段**；留空 = 自动检测 + 中英混说 | 放错位置会被静默忽略 |
| **强制切段 450 秒** | 超过 500 秒或 50MB → ffmpeg 切段并转单声道 16kHz 32kbps mp3 | 单请求 ≤ 500 秒 / 50 MB，**超了直接报错不截断**。留 50 秒余量：探测有误差，卡着 500s 切必 400 |
| **切段失败抛明确错误** | 指向 ffmpeg 检查，不静默直传 | 静默直传 = 服务端 400，比本地报错难查 |
| **`verbose_json` 才拿分离+时间戳** | 开任一开关就切 verbose_json，一律 `stream=false` | spec 明写 verbose_json/srt/vtt **不能**与 `stream=true` 同用 |
| **跨段时间戳累加** | 每段 `start` 从 0 重新计，拼接时加段偏移，得到全局时间轴 | 官方 `start`/`end` 单位是**秒**（float），与讯飞的毫秒相反 |
| **跨段说话人编号对齐** | 人数变多时分配新编号（S3…） | ⚠ 见下方「能力边界」 |
| **9 类错误码翻成中文** | 400/401/402/403/404/413/422/429/500 逐条给可操作提示，附 `request_id` 与端点 | 400/413 的提示直接指向「转成单声道 16kHz 或 mp3 压缩」这个具体动作 |
| **Mock 三分支** | 与 xf_asr 行为一致（凭证齐全却勾 mock 也给 WARNING） | 用户在两个云端引擎间切换时心智统一 |

**配置位置**：「设置 → 转写 → 自定义（POST）高级」。CLI 用 `--asr-engine custom_post`。

**换私有部署时**：如果对方的时长/体积限制不是 500 秒 / 50 MB，
改 `custom_post_asr.py` 顶部的 `MAX_DURATION_SEC` / `MAX_FILE_BYTES` 即可。

> ⚠ **说话人分离的能力边界（务必读）**：切段后服务端每个独立请求都**从 S1 重新编号**，
> 响应里**没有任何跨段身份信息**。所以：
> - **不切段的短素材分离最可靠**，推荐优先用这类素材
> - 切段后段间人数相同时只能按编号对齐；两人音色接近且编号互换了会认错
> - 需要**严格一致的跨段说话人身份** → 用 `xf_asr`（5 小时单文件不切段）

#### 请求头文件长什么样

程序首次运行会自动在程序目录生成 `custom_post_headers.txt`：

```
# 自定义 POST ASR — 附加请求头
#
# 格式：每行一条  Key: Value
#  - # 开头是注释，空行忽略
#  - 这里的头会与程序自动生成的合并（同名以本文件为准）
#  - Authorization 通常在「API Key」输入框里填，不用写这里；
#    只有当服务端要求非 Bearer 鉴权时才写在这里
#  - 本文件可能含密钥，已加入 .gitignore，不要提交到仓库
#
# 例：私有部署要额外带一个 token 头
# X-Deploy-Token: your-token-here
```

**不配任何东西时**：端点保持默认（MiniMax 官方）、API Key 填进输入框、
语言选「自动检测」——直接可用，请求头文件可以完全不管。

#### 引擎改名说明（`minimax_asr` → `custom_post`）

2026-10-02 本引擎从「MiniMax 专用」泛化为「自定义 POST」，引擎名随之改为 `custom_post`。
**旧名仍可用**：

- `audio.py` 的 dispatch 同时接受 `"custom_post"` 与 `"minimax_asr"`
- `transcribe_audio_with_minimax_asr()` 转发到新函数，并打 **WARNING** 提示迁移
- `_resolve_engine_config` 读 `config.custom_post`，没有才回落 `config.minimax_asr`

> 保留旧名是有意为之：如果直接改名不兼容，用户 config.json 里已保存的
> `"asr_engine": "minimax_asr"` 会**静默回落到 funasr**——用户以为在用云端其实在本地跑，
> 这类问题极难排查。宁可打一条迁移提示，也不让它静默失效。

建议把 config.json 里的 `asr_engine` 改成 `custom_post`、
并把 `transcribe.minimax_asr` 块改名为 `transcribe.custom_post`。

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
| **funasr（本地）** | ✅ 支持（2026-10-02 接入） | 需额外挂 CAM++ 嵌入模型 `spk_model="cam++"`，见下方「FunASR 高级」 |
| **xf_asr（讯飞）** | ✅ 支持 | 需账号开通「角色分离」权限；未开通时本 fork 自动降级不报错 |
| **qwen_asr（Qwen3-ASR）** | ❌ **不支持** | 多个独立实测一致：「目前版本不支持自动说话人分离，会把多人对话识别为连续文本」；官方把「需要说话人分离的会议记录」列为谨慎使用场景 |
| **whisper** | ❌ 不支持 | 需外挂 pyannote 等第三方模型 |

> **Qwen3-ASR 实测原文**（两处独立来源一致）：
> 「Qwen3-ASR目前版本不支持自动说话人分离。它会把多人对话识别为连续的文本,不区分不同的说话者。」
> 「4人圆桌讨论（含打断、抢话、语气词），模型未做说话人分离，但通过上下文连贯性，将发言逻辑自动归并为段落」
>
> 来源：[Qwen3-ASR 服务实测](https://blog.csdn.net/)、[Qwen3-ASR-0.6B 实测](https://blog.csdn.net/weixin_36296444/article/details/157788729)

#### 本地说话人分离：FunASR + CAM++（2026-10-02 新增）

| 改动 | 内容 | 为什么改 |
|------|------|----------|
| **CAM++ 模型解析** | `resolve_funasr_spk_model_name()` 别名归一（`cam++` / `CAM++` / `campplus`）+ 本地快照优先（ASCII 路径优先，避免中文路径） | 与既有 `resolve_funasr_vad_model_name()` 同款策略 |
| **`spk_model` 传给 `AutoModel`** | 开了分离就传 `spk_model=<cam++ 路径或 ID>`，**必须与 `vad_model` 一起传** | FunASR 官方约束：说话人聚类在 VAD 流水线里做，只传 `spk_model` 或 `return_spk_res=True` 对直接推理无效 |
| **`sentence_info` 格式化** | `format_funasr_speaker_text()` → `[MM:SS] 【说话人N】文本`，格式与 xf_asr 完全一致 | `spk` 是匿名的录音内标签，映射成连续编号（`spk=0`→`说话人1`）读起来更顺 |
| **富标签剥离** | 剥掉 SenseVoice 的 `<\|zh\|><\|HAPPY\|><\|Speech\|>` 之类标记 | 不剥会污染说话人行的排版。官方 `rich_transcription_postprocess` 是有损展示函数（还做文本替换），这里只做最小必要的去标签，不改动既有非分离路径行为 |
| **两个键名都读** | `sent["sentence"]`（blog demo）和 `sent["text"]`（go/sensevoice demo）都读 | 官方两处 demo 键名不一致，照抄任何一处都可能踩空 |
| **拿不到就退回** | 没有 `sentence_info` 时自动走 `extract_funasr_text()` 纯文本路径 + WARNING | 可选能力不能变成硬失败 |
| **GUI「FunASR 高级」GroupBox** | 分离说话人 + 说话人模型下拉 + 时间戳三个控件，模型下拉随分离开关联动置灰 | 与既有「Qwen3-ASR 高级」「讯飞听见 高级」风格一致 |

**关于算力**（这一条容易被误解）：

| 引擎 | 说话人分离的开销性质 |
|------|-------------------|
| funasr + CAM++ | **本地**，约 28MB 嵌入模型（非生成式），CPU 即可跑、**不占 GPU 显存**；额外开销 = 多加载一个小模型 + 一次聚类 |
| xf_asr | **云端**，不占本地算力；开销是讯飞时长额度 + 准确率风险 |
| qwen_asr | **做不到**，开关无意义 |

所以「为了省算力」这个开关只对 FunASR 有意义（关掉就少加载一个模型、少一次聚类），对 Qwen3-ASR 是伪命题。

**配置位置**：「设置 → 转写 → FunASR 高级」。首次开启会从 ModelScope 下载约 28MB 到 FunASR 缓存目录（路径规则见上文「离线模型怎么放」）。

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
| **LLM 协议 / 厂商 拆成两个正交字段** | `llm.protocol`（`openai_chat` / `anthropic`，决定用哪个 SDK、发什么格式）<br>`llm.vendor`（`minimax` / `deepseek` / `qwen`… 决定预填哪个 Base URL）<br>UI 上「协议 Protocol」「厂商 Vendor」两行**标签对齐**显示 | 原来 `provider` 一个字段同时承担「协议开关」和「厂商名」，语义混淆：填 `openai` + minimax base_url 实际是「OpenAI 兼容协议连 MiniMax」 |
| **旧 config 零改动兼容** | `resolve_protocol()` 有 `protocol` 就用；没有就回落到旧 `provider`；**任何无法识别的 `provider` 值一律当 `openai_chat`**<br>写盘时 `provider` 仍保留写入 | 旧配置不用改一行就能跑；兜底顺带修掉「`provider` 写个空格就整个转写挂掉」 |
| **模型列表自动抓取** | 「⟳ 抓取」按钮 → `GET {base_url}/models`，后台线程不阻塞 UI，结果进 **Model 可编辑下拉**<br>**抓取失败绝不清空已填内容、绝不阻断保存** | Model 名（如 `MiniMax-M2.7-highspeed`）手打极易错；且不是所有 provider 都提供 `/models`，必须留手填口子 |
| **抓取走 requests 直连而非 openai SDK** | 新增 `providers/llm_models.py`，**不用 `client.models.list()`** | 实测 `openai 3.8.0` 缺 `jiter`（`pyproject` 只声明 `openai>=1.0.0`），`client.models` 属性访问直接 `ModuleNotFoundError`；`c.chat` 懒加载所以现有转写不受影响 |
| **多配置档案（四处通用）** | 「设置」里共 4 个档案区，全部复用 `gui/profile_store.py`：<br>① 大模型 `llm.profiles` ② 自定义 POST `transcribe.custom_post.profiles`<br>③ AI 封面 `ai_cover.profiles` ④ 图床 `image_host.profiles`<br>右下角「管理配置档案…」可四选一，各 Tab 内另有「管理…」直达 | 换服务不用每次手打全套。封面常在多套生图服务间切、图床常在多套图床间切，都是高频场景 |
| **`custom_post` 也有同一套档案** | 「设置 → 转写 → 自定义（POST）」下方独立档案区，存 `transcribe.custom_post.profiles`<br>**每个档案可指向不同的请求头文件** | 切一个云端 ASR Provider 要手打「端点 + Key + 请求头文件路径」，而不同 Provider 的鉴权方式还各不相同（Bearer / 自定义头 / 私有网关 token）——档案让它们各存各的互不干扰 |
| **AI 封面 / 图床 也都有档案** | 「AI 封面」存 `ai_cover.profiles`（Provider / 模型 / Edit 模型 / brand…）<br>「图床」存 `image_host.profiles`（Provider / API URL / Token…） | 这两处本来是纯手填，每次换服务都要重打全套。封面常在多套生图服务间切（图床同理） |
| **档案机制统一到 `gui/profile_store.py`** | `ProfileSpec`（声明存什么字段 / 存到哪 / 列表显示什么）+ `ProfileManagerDialog` + `ProfileMixin`<br>四处档案区共用同一套实现，**加第五处只需加一段声明** | 之前 LLM 与 custom_post 各手写一套 handler（260 行），必然漂移。本轮就因为引擎清单同样手写两份而出了 bug |
| **右下角「管理配置档案…」改为四选一** | 档案区有四处后，按钮先弹选择器（大模型 / 自定义 POST / AI 封面 / 图床），各 Tab 内仍有自己的「管理…」直达 | 塞四个按钮进按钮行会破坏 Save/Cancel 的位置记忆 |
| **⚠ ASR 引擎清单单一真源** | 新增 `gui/asr_engines.py`，settings 与「覆盖本次 ASR」两处下拉都从它读<br>含旧名归一（`minimax_asr` → `custom_post`） | **修 bug**：「覆盖本次 ASR」的下拉只列了 funasr / whisper，fork 新增的三个引擎在那儿**根本选不到**，用户只能去改全局默认——而「覆盖本次」的意义正是只改这一条 |
| **档案与当前配置：当前字段为准，档案是模板库** | 改档案不自动改下方字段，需点「应用到当前」才覆盖 | 避免「以为在改档案、其实改的是当前配置」；`providers/llm.py` 一行不用改，零回归 |
| **转写 Tab 按所选引擎聚焦** | 默认**只显示「默认 ASR 引擎」所选那一个**的设置分组（5 个引擎不再全铺开，页面短很多）<br>勾「显示全部引擎的设置（不分当前引擎）」恢复完整表单<br>**隐藏只是不显示，参数照常读写——不会丢配置** | 转写页原来把 FunASR / Qwen3-ASR / 讯飞 / 自定义 POST 四套高级设置全铺出来，要滚很久才能看完；实际每次只用一套 |
| **⚠ profiles 存 list 而不是 dict** | 见下方说明 | 这是本设计最容易埋雷的一处 |

#### ⚠ 档案为什么必须存 list（最容易埋雷的一处）

`config.py` 的 `deep_update` 是递归合并：

```python
for key, value in updates.items():
    if isinstance(value, dict) and isinstance(base.get(key), dict):
        deep_update(base[key], value)   # dict → 递归合并
    else:
        base[key] = value               # 其他（含 list）→ 整体替换
```

推论：

| profiles 存成 | 删除一个档案时 | 结果 |
| --- | --- | --- |
| **list**（本项目采用） | 整体重写列表 | ✅ 删掉就真的没了 |
| dict（key = 档案 id） | `deep_update` **递归合并** | ❌ 删掉的 key 不会消失 → **删不掉的幽灵档案** |

这个区别在代码 review 时极难一眼看出，所以 `smoke_llm_settings.py` 里有一条
**专门锁住这个行为**的测试（同时验证 list 正常、dict 会残留）。


### 健壮性

- **`closeEvent` 强等 worker**：上游关闭 GUI 时 worker 线程不一定退出，可能卡死
  - 本 fork：`thread.wait(10000)` + `terminate()` 兜底
- **`atexit` 兜底清理 tempdir**：进程任何路径退出（GUI 关闭 / 异常 / kill）都会清理 `%TEMP%\qwen_asr_chunks_*`

### 测试 / 工程

- **14 个 smoke 脚本在 `tests/`**（`smoke_settings / fallback / language / jp_kr / release / cleanup / thinking / xf_asr / xf_e2e / funasr_speaker / custom_post_asr / dashscope_asr / llm_settings / bilibili_parse`）
  - 验证 GUI 字段读写、device fallback、language 兜底、释放按钮、清理按钮、xf_asr 鉴权签名 / upload 协议 / poll 状态码 / orderResult 解析 / 说话人分离 / 增强参数降级、FunASR CAM++ 模型解析 / sentence_info 格式化 / 富标签剥离、custom_post 协议契约 / 切段阈值 / 跨段偏移 / 错误码 / 端点可配 / 请求头文件解析 / 旧名兼容 / 五处注册点、**DashScope 提交体 / URL 编码 / 轮询状态机 / subtask_status / 毫秒转秒**、LLM 档案、av↔BV 互转
  - 14 套全过，共 140+ 断言
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
