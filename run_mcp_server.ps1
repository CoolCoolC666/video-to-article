# run_mcp_server.ps1 - 启动 MCP server（Streamable HTTP）
#
# 端点：http://127.0.0.1:8000/mcp   （无尾斜杠；只监听本机，不暴露到局域网）
#
# 客户端接入（MCode 原生支持 HTTP）：
#   { "mcpServers": { "yilanchengwen": { "url": "http://127.0.0.1:8000/mcp" } } }
#
# Claude Desktop 原生不支持 HTTP，需要 mcp-remote 桥接（需 Node.js）：
#   { "mcpServers": { "yilanchengwen": {
#       "command": "npx", "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp"] } } }
#
# 关闭本窗口 = 停服务（MCP 只读工具会随之失效，重开即可）。
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$py = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

Write-Host '=== MCP server -> http://127.0.0.1:8000/mcp ==='
& $py -m video_to_article.mcp_tools.server @args
