# Skill：批量转写

`yilanchengwen-batch-transcribe/SKILL.md` —— 告诉 Agent **这套 MCP 工具怎么用**。

分两层，和方案里的一致：

| 层 | 位置 | 作用 |
|---|---|---|
| **知识层** | `skills/*/SKILL.md` | 工具清单、调用顺序、配置怎么选、已知坑 |
| **能力层** | `src/video_to_article/mcp_tools/` | 真正干活的 MCP server |

分开的理由：换 Agent 也能复用同一份 Skill，只要它能连 MCP。

## 安装

**MCode**（原生支持 Streamable HTTP）：

```powershell
cd E:\000~\YilanChengWen-src
.venv\Scripts\python.exe -m video_to_article.mcp_tools.server
```

MCP 配置：

```json
{ "mcpServers": { "yilanchengwen": {
  "url": "http://127.0.0.1:8000/mcp" } } }
```

然后把本目录下的 `SKILL.md` 复制到 MCode 的 skill 目录。

**Claude Desktop**（**原生不支持 HTTP**，需 mcp-remote 桥接，要 Node.js）：

```json
{ "mcpServers": { "yilanchengwen": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp"] } } }
```

配置文件位置：
- Windows `%APPDATA%\Claude\claude_desktop_config.json`
- macOS `~/Library/Application Support/Claude/claude_desktop_config.json`

> 社区实测：直接配 SSE 会出现「工具先出现后消失」，桥接反而更稳。

## 当前阶段（P0）是四个只读工具

`config_list` / `config_describe` / `engine_capabilities` / `transcribe_plan`

不写文件、不调付费 API。写操作的工具（`transcribe_from_raw`、
`transcribe_batch_start` 等）在 P1–P3，方案见 `日志/2026-10-07_B_批量转写MCP工具_实施方案.md`。