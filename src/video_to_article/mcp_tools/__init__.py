"""批量转写 MCP 工具（2026-10-07）。

## 定位

项目**已有完整批量能力**（`processor.plan_batch_urls` / `process_batch` /
`check_batch_report` / `batch.find_local_videos`）。本包不重写任何一条，
只把现有能力包成 Agent 可驱动的接口，补三件现没有的：

1. **结构化返回** —— CLI 是 `print()` 到 stdout，Agent 拿不到可靠结果
2. **配置选择** —— 项目已有四类档案（llm / asr / cover / host），
   MCP 让 Agent 能按档案调用，而不必让你手改 config.json
3. **异步台账** —— 批量是小时级任务，不能同步等（见 P3）

## 铁律：复用，不重写

内部一律 import 现有函数，**绝不 subprocess 调 transcribe.py**。
否则要写一层正则去解析项目自己 print 的格式，而这层会随项目改动一起烂；
批量规则也会变成两份逻辑，改一处漏一处。

## 分期

| 阶段 | 工具 | 写盘 | 花钱 |
|---|---|---|---|
| P0 | config_list / config_describe / engine_capabilities / transcribe_plan | ❌ | ❌ |
| P1 | transcribe_from_raw | ✅ | ✅ |
| P2 | transcribe_recheck | ✅ | ❌ |
| P3 | transcribe_batch_start / status / cancel（异步台账 + 心跳） | ✅ | ✅ |
"""