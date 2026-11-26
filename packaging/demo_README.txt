一览成文（YilanChengWen）— 课堂演示速查
==========================================

【演示前 1 分钟】
  1. 把整个文件夹解压到本机任意位置（建议纯英文路径，如 D:\YilanChengWen）
  2. 双击 YilanChengWen.exe
  3. 等主界面出来（首次启动约 10~20 秒）

【演示怎么走】
  1. 「单条处理」页 → 粘贴一条视频链接（或选本地视频文件）
  2. 文章模板选 general_article
  3. 点「开始」，进度区会依次显示：
       上传音频 → 云端转写 → 生成文章
  4. 完成后看 output\ 目录下的 general_article.md

【包里已经配好的】
  · 转写：阿里云百炼 DashScope 异步（qwen-audio-3.1-asr-flash-filetrans）
  · 成稿：云端大模型（MiniMax-M3.1-Flash）
  · 图床：Cloudflare R2 —— 把音频换成公网可读地址，DashScope 才拉得到

【包里没有什么】
  · 没有本地语音模型（torch / FunASR / Whisper 那条路被裁掉了）
      → 「设置 → 转写 → 默认 ASR 引擎」保持「自定义 POST」，不要改
  · 没有视频下载用的 cookies
      → YouTube 链接可能下不动；B 站 / 本地视频没问题

【出问题了看哪里】
  · logs\app.log —— 所有日志都在这
  · 常见报错对应关系：
      「该端点拒绝了当前凭证 / Free quota exhausted」
          → 服务商额度用完了，换服务商或等额度恢复
      「模型 ... 不支持异步调用」
          → 模型名被改过，改回 qwen-audio-3.1-asr-flash-filetrans
      「图床没有可用凭据」
          → config.json 里 image_host 三件套
            （token / access_key_id / access_key_secret）被清空了

【安全提醒】
  config.json 里有你的 API Key。演示完请从公用电脑上删掉这个文件夹。
