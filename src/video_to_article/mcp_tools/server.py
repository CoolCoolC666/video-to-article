"""MCP server（P0：四个只读工具）。

## 部署

```powershell
.venv\\Scripts\\python.exe -m video_to_article.mcp_tools.server
# 端点 http://127.0.0.1:8000/mcp/
```

只监听 127.0.0.1：这是本地服务，不该暴露到局域网。

## 客户端接入

MCode / Claude Code CLI（都原生支持 Streamable HTTP）：
```json
{ "mcpServers": { "yilanchengwen": {
  "url": "http://127.0.0.1:8000/mcp/" } } }
```

Claude Desktop **原生不支持 HTTP**，需要 mcp-remote 桥接（需 Node.js）：
```json
{ "mcpServers": { "yilanchengwen": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp/"] } } }
```
（社区实测：直接配 SSE 会出现「工具先出现后消失」，桥接更稳。）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging_config import configure_logging
from .capabilities import (
    DASHSCOPE_ASYNC_MODELS,
    IMAGE_HOST_PROVIDERS,
    all_engines,
    describe_effective,
)
from .config_view import (
    ACTIVE_SUFFIX,
    effective_config,
    list_config_files,
    list_profiles,
    load_config_file,
    masked_config,
)

logger = configure_logging()

try:  # mcp 2.x 里 FastMCP 已更名为 MCPServer（2026-07-28 重命名）
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - 兼容 mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server(
    name="yilanchengwen",
    instructions=(
        "一览成文 · 批量转写工具。\n"
        "推荐顺序：config_list 选配置 → engine_capabilities 看引擎要求 → "
        "transcribe_plan 先规划（不花钱）→ 用户确认后再跑。\n"
        "P0 阶段全部只读，不会写任何文件、不会调用任何付费 API。"
    ),
)


# ==========================================================================
# 1. 配置类
# ==========================================================================
@mcp.tool()
def config_list() -> Dict[str, Any]:
    """列出所有可用配置文件，以及每份里的四类档案（密钥已脱敏）。

    四类档案：llm（大模型）/ asr（自定义 POST）/ cover（AI 封面）/ host（图床）。
    默认配置就是程序目录下的 config.json —— **不传 config_file 即用它**。
    """
    files = list_config_files()
    if not files:
        return {
            "configs": [],
            "hint": "没有找到任何配置文件。请确认程序目录下存在 config.json。",
        }
    out = []
    for f in files:
        item = dict(f)
        try:
            cfg = load_config_file(f["path"])
            profs = list_profiles(cfg)
            item["profiles"] = {k: v for k, v in profs.items()
                                if not k.endswith(ACTIVE_SUFFIX)}
            # ⚠ 用 removesuffix 而不是切片：切片长度算错一位就会把
            #   "llm__active" 截成 "ll"（__active 是 8 个字符，不是 9）
            item["active"] = {k.removesuffix(ACTIVE_SUFFIX): v for k, v in profs.items()
                              if k.endswith(ACTIVE_SUFFIX)}
        except Exception as e:
            item["read_error"] = str(e)[:200]
        out.append(item)
    return {"configs": out}


@mcp.tool()
def config_describe(config_file: Optional[str] = None) -> Dict[str, Any]:
    """看某份配置的详情：当前引擎链、模型、prompt、图床可读性（脱敏）。

    Args:
        config_file: 省略则用程序默认的 config.json
    """
    cfg = load_config_file(config_file)
    eff = describe_effective(cfg)
    tr = (cfg or {}).get("transcribe") or {}
    host = (cfg or {}).get("image_host") or {}
    return {
        "config_file": config_file or "<默认 config.json>",
        "asr_chain": eff["asr"],
        "llm": eff["llm"],
        "llm_concurrency": 1,
        "image_host": {
            "enable": bool(host.get("enable")),
            "provider": host.get("provider") or "easyimage",
            "provider_meaning": IMAGE_HOST_PROVIDERS.get(
                str(host.get("provider") or "easyimage"), ""
            ),
            "endpoint": masked_config({"e": host.get("api_url") or host.get("endpoint")})["e"],
            # DashScope 要公网 URL，桶不可读就是必失败的前置条件
            "publicly_readable": bool(
                host.get("public_base_url") or int(host.get("presign_seconds") or 0) > 0
            ),
        },
        "prompts_dir": "prompts/articles/",
        "raw_config": masked_config(cfg),
    }


# ==========================================================================
# 2. 能力类
# ==========================================================================
@mcp.tool()
def engine_capabilities() -> Dict[str, Any]:
    """所有 ASR 引擎的能力边界：并发上限、要不要公网 URL、要不要图床、模型名。

    跑之前先看这个，能避免大部分「跑到一半才炸」。
    """
    return {
        "engines": all_engines(),
        "dashscope_async_models": DASHSCOPE_ASYNC_MODELS,
        "image_host_providers": IMAGE_HOST_PROVIDERS,
        "global_rules": [
            "本地引擎一律并发 1（显存/线程独占）",
            "云端引擎建议 2，再高容易触发 RPM 限速",
            "**LLM 整理阶段恒为 1**（触发限速 + 单条就要 40s + 重试成本放大）",
            "DashScope 只认 -filetrans 结尾的异步模型名",
        ],
    }


# ==========================================================================
# 3. 规划类（P0 最有价值的工具）
# ==========================================================================
@mcp.tool()
def transcribe_plan(
    source: str,
    recursive: bool = True,
    limit: int = 0,
    prompts: Optional[List[str]] = None,
    skip_existing: bool = True,
    batch_root: Optional[str] = None,
    config_file: Optional[str] = None,
    profiles: Optional[Dict[str, str]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """**只规划，不执行** —— 不写任何文件、不调用任何付费 API。

    告诉 Agent：会处理哪些、跳过哪些、为什么跳、大概要多久、
    以及**跑之前就该知道的风险**（这轮调试里两次都是跑一半才暴露问题）。

    Args:
        source: **原始视频所在目录**（不是 data/ 下的已抽取音频），或单文件 / URL
        recursive: 目录递归扫描
        limit: 只规划前 N 个（0 = 不限）
        prompts: 提示词名列表，省略则用默认
        skip_existing: 跳过已完成的（强烈建议保持 True）
        batch_root: 输出路径的组织根。**省略 = 用 source**（与 CLI --local-dir 一致）
        config_file: 配置文件路径，省略用默认 config.json
        profiles: 临时激活的档案，如 {"llm": "Deepseek（调）"}
        overrides: 仅本次生效的覆盖，如 {"asr_engine": "custom_post"}
    """
    from ..batch import find_local_videos
    from ..output_manager import build_output_paths
    from ..processor import plan_batch_urls

    try:
        cfg = effective_config(config_file, profiles, overrides)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    tr = cfg.get("transcribe") or {}
    engine = str(tr.get("asr_engine") or "funasr")
    prompt_names = list(prompts) if prompts else None
    enable_llm = True

    # 收集待处理源
    p = Path(source)
    is_dir = p.is_dir()
    # CLI handle_local_dir 的做法：batch_root 默认 = local_dir
    effective_root = batch_root if batch_root else (str(p) if is_dir else None)
    try:
        if p.exists():
            items = [source] if p.is_file() else find_local_videos(
                source, recursive=recursive, limit=limit or None
            )
        else:
            items = [source]  # 当作 URL
    except Exception as e:
        return {"ok": False, "error": f"扫描失败: {e}"}

    if not items:
        return {"ok": True, "planned": 0, "items": [],
                "warnings": [f"{source} 下没有找到可处理的媒体文件"]}

    try:
        plan = plan_batch_urls(
            video_urls=items,
            prompt_names=prompt_names,
            enable_llm_optimization=enable_llm,
            skip_existing=skip_existing,
            batch_root=effective_root,
            limit=limit or None,
            cookies_from_browser=tr.get("cookies_from_browser"),
            cookies_file=tr.get("cookies"),
            youtube_po_token=tr.get("youtube_po_token"),
        )
    except Exception as e:
        return {"ok": False, "error": f"规划失败: {type(e).__name__}: {e}"}

    planned_items = plan.get("planned_items") or []
    detail = []
    for it in planned_items:
        title = it.get("title") or ""
        try:
            raw_file, arts = build_output_paths(
                title=title, source=it.get("source") or it.get("url") or "",
                prompt_names=prompt_names, enable_llm_optimization=enable_llm,
            )
        except Exception:
            raw_file, arts = "", {}
        detail.append({
            "title": title,
            "source": it.get("source") or it.get("url") or "",
            "status": it.get("status") or "",
            "reason": it.get("reason") or "",
            "raw": str(raw_file) if raw_file else "",
            "articles": [str(x) for x in (arts or {}).values()],
        })

    eff = describe_effective(cfg)
    asr = eff["asr"]
    planned = len(plan.get("planned_urls") or [])

    return {
        "ok": True,
        "planned": planned,
        "skipped_before_run": len(plan.get("skipped_before_run") or []),
        "remaining_after_plan": plan.get("remaining_after_plan", 0),
        "status_counts": plan.get("status_counts", {}),
        "items": detail,
        "concurrency": {
            "asr_max": asr.get("max_concurrency", 1),
            "llm": 1,
            "note": "默认建议 ASR 并发保持 1；LLM 恒为 1",
        },
        "asr_chain": {k: v for k, v in asr.items() if k != "notes"},
        "warnings": _plan_warnings(cfg, asr, planned) + _source_warnings(source, effective_root),
    }


def _source_warnings(source: str, batch_root: Optional[str]) -> List[str]:
    """source 选错的后果很隐蔽：规划全绿但路径对不上，全判 unprocessed。

    本轮实测踩过：把 `data/local/1-课件/`（里面是**已抽取的音频**）
    当成源，而 output_manager 是按「源文件父目录名」定位输出的，
    于是算成 `output/local/audio/xxx`，跟已有的 `output/local/1-课件/xxx`
    完全对不上 —— 28 个全部 unprocessed，而其实好几个已经做完了。
    """
    warns: List[str] = []
    try:
        sp = Path(source).resolve()
    except OSError:
        return warns
    parts = {x.lower() for x in sp.parts}
    if "data" in parts or sp.name == "audio":
        warns.append(
            f"⚠ source 位于 {sp} —— data/ 下放的是**已抽取的音频**，"
            "不是原始视频。\n"
            "     输出路径按「源文件父目录名」定位，用音频当源会算成别的目录，"
            "结果与已有输出对不上（全部显示 unprocessed）。\n"
            "     请把 source 改成**原始视频所在目录**。"
        )
    return warns


def _plan_warnings(cfg: dict, asr: dict, planned: int) -> List[str]:
    """把**跑之前就该知道的风险**挑出来，别让它跑到一半才炸。"""
    warns: List[str] = []
    engine = asr.get("id")
    style = asr.get("api_style")
    host = (cfg or {}).get("image_host") or {}

    if engine == "custom_post" and style == "dashscope_async":
        if asr.get("needs_image_host"):
            warns.append(
                "DashScope 需要音频公网 URL，但没填「音频直链」→ 将借图床中转。"
            )
        if not host.get("public_base_url") and not int(host.get("presign_seconds") or 0):
            warns.append(
                "图床既没配 public_base_url 也没配 presign_seconds，"
                "DashScope 服务端将拉不到音频（会报 FILE_DOWNLOAD_FAILED）。"
            )
        model = str(asr.get("model") or "")
        if model and not any(model.endswith(s) for s in (
            "-filetrans", "-filetrans",)) and model not in DASHSCOPE_ASYNC_MODELS:
            warns.append(
                f"模型 {model!r} 不在官方异步模型清单里；"
                f"同步模型打异步端点会报 "
                f"'current user api does not support asynchronous calls'。"
                f"可用：{DASHSCOPE_ASYNC_MODELS}"
            )
        if not host.get("token") and not (
            host.get("access_key_id") and host.get("access_key_secret")
        ):
            warns.append("图床没有可用凭据（token 或 access_key 对），上传会失败。")
        if str(host.get("api_url") or "") and "example" in str(host.get("api_url")):
            warns.append("图床 api_url 看着像模板占位符，没换成真实域名。")

    llm = (cfg or {}).get("llm") or {}
    mt = llm.get("max_tokens")
    llm_vendor = str(llm.get("vendor") or "")
    # 实测上限（会变，只作为提示不作为阻断）
    known_ceiling = {"deepseek": 393216}
    if llm_vendor in known_ceiling and isinstance(mt, int) and mt > known_ceiling[llm_vendor]:
        warns.append(
            f"max_tokens={mt:,} 超过 {llm_vendor} 实测上限 "
            f"{known_ceiling[llm_vendor]:,}，会被服务端 400 拒绝"
            f"（发生在请求发出前，界面上看不出原因）。建议调低。"
        )
    if not llm.get("api_key"):
        warns.append("大模型没有 API Key，成稿阶段会失败（转写仍可正常）。")

    if planned == 0:
        warns.append("本次没有任何待处理项 —— 可能已经全部完成，或 skip_existing 过滤掉了。")
    return warns


# ==========================================================================
# 启动
# ==========================================================================
def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="一览成文 MCP server")
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只监听本机；改成 0.0.0.0 会暴露到局域网，慎用")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--path", default="/mcp",
                    help="SDK 默认就是 /mcp（**无尾斜杠**）")
    ap.add_argument("--stateless", action="store_true",
                    help="无状态模式：多客户端并发更稳，但不保留会话")
    args = ap.parse_args()

    logger.info(f"启动 MCP server → http://{args.host}:{args.port}{args.path}")
    # ⚠ transport 用 "streamable-http" 而不是 "sse"：
    #   SSE 传输在 2025-03-26 规范修订后已被 Streamable HTTP 取代。
    #
    # ⚠ mcp 2.x 的参数名是 streamable_http_path（**不是** path，也不是
    #   streamable_http_path 带尾斜杠）。1.x 的 FastMCP.run() 用 path=，
    #   2.x 的 MCPServer.run_streamable_http_async() 用 streamable_http_path=
    #   且是 keyword-only —— 照抄 v1 的 run(transport="streamable-http",
    #   port=..., path=...) 会直接 TypeError。
    mcp.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path=args.path,
        stateless_http=args.stateless,
    )


if __name__ == "__main__":
    main()