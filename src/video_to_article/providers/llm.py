import re
import time
from typing import Optional

from ..logging_config import configure_logging
from ..prompts import load_prompt
from ..text_utils import format_time
from .llm_providers import effective_vendor, resolve_protocol

logger = configure_logging()

# 2026-10-04: max_tokens 被服务端拒绝时的降级目标。
# 各家上限不同且会变（实测 DeepSeek 为 [1, 393216]），硬编码上限必然过时，
# 所以优先从**错误信息里解析真实范围**（DeepSeek 就会写明），解析不到才退到
# 这个「几乎所有厂商都接受」的值。
_FALLBACK_MAX_TOKENS = 65536
# 形如 "the valid range of max_tokens is [1, 393216]"
_MAX_TOKENS_RANGE_RE = re.compile(
    r"max_tokens[^\[\]]*\[\s*(\d+)\s*,\s*(\d+)\s*\]", re.IGNORECASE
)


def _max_tokens_ceiling(error_text: str) -> int:
    """从报错里抠出服务端真实的 max_tokens 上限，取不到返回 0。"""
    m = _MAX_TOKENS_RANGE_RE.search(str(error_text or ""))
    if not m:
        return 0
    try:
        return int(m.group(2))
    except (TypeError, ValueError):
        return 0

# 2026-09-12: 剥离推理模型（M3 / Qwen3 等）输出开头的 <think>...</think> 思维链块
# 规则：只处理开头首个块（含前后空白），不破坏正文
# ⚠ 2026-10-04 订正：DeepSeek 当前（api-docs.deepseek.com 2026 版）把思维链放在
#   **独立的 reasoning_content 字段**（流式是 delta.reasoning_content），
#   OpenAI SDK 会自动分离，**不会混进 content** —— 所以本剥离对 DeepSeek 无害
#   但也不起作用。它仍需要保留给 MiniMax-M3 / Qwen3 这类把思维链塞进 content 的。
#   另：DeepSeek 旧名 deepseek-reasoner / R1 已下线，别再按那些名字做判断。
_THINK_BLOCK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def _strip_thinking_block(text: str) -> str:
    """剥离输出开头的 <think>...</think> 思维链块（推理模型会暴露给用户）。

    适用模型：MiniMax-M3、DeepSeek-R1、OpenAI o1 / o3 等带推理链的模型。
    对非推理模型无副作用（regex 不命中直接返回原文）。
    """
    if not text:
        return text
    return _THINK_BLOCK_RE.sub("", text, count=1)


def _render_prompt(prompt_template: str, text: str) -> str:
    """Render prompt text without treating Hexo/AnZhiYu tags as format fields."""
    return prompt_template.replace("{transcript_text}", text)


def optimize_text_with_llm(text: str, config: dict, prompt_name: str = "evaluation") -> Optional[str]:
    """Optimize/extract text using configured LLM provider."""
    if not config or "llm" not in config:
        logger.warning("未配置大模型，跳过文本优化")
        return None

    llm_config = config["llm"]
    # 2026-10-04：provider 字段的语义从「厂商」纠正为「协议」。
    # 旧配置零改动继续可用（resolve_protocol 内部回落到 provider 字段）。
    protocol = resolve_protocol(llm_config)
    vendor = effective_vendor(llm_config)

    logger.info(
        f"使用 {vendor}（{protocol} 协议）和提示词 '{prompt_name}' 进行文本优化..."
    )

    try:
        if protocol == "anthropic":
            return _optimize_with_anthropic(text, llm_config, prompt_name)
        if protocol == "openai_chat":
            return _optimize_with_openai(text, llm_config, prompt_name)
        # resolve_protocol 只认 PROTOCOL_LABELS 里的值，走不到这里；
        # 保留兜底是因为将来加协议时可能忘了接分支。
        logger.error(f"不支持的协议: {protocol}")
        return None
    except Exception as e:
        logger.error(f"文本优化失败: {e}")
        return None


def _optimize_with_openai(text: str, config: dict, prompt_name: str) -> Optional[str]:
    """Optimize text with OpenAI-compatible chat completions."""
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("未安装 openai 库，请运行: pip install openai")
        return None

    start_time = time.time()
    timeout_seconds = float(config.get("timeout_seconds", config.get("timeout", 180)))
    max_retries = int(config.get("max_retries", 3))
    client = OpenAI(
        api_key=config.get("api_key"),
        base_url=config.get("base_url", "https://api.openai.com/v1"),
        timeout=timeout_seconds,
        max_retries=max_retries,
    )

    prompt_template = load_prompt(prompt_name)
    if not prompt_template:
        return None
    prompt = _render_prompt(prompt_template, text)

    want_max = int(config.get("max_tokens", 4000) or 4000)

    def _create(mt: int):
        return client.chat.completions.create(
            model=config.get("model", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=config.get("temperature", 0.3),
            max_tokens=mt,
        )

    try:
        try:
            response = _create(want_max)
        except Exception as first_exc:
            # ⚠ 各家 max_tokens 上限**不一样**，而 GUI 只有一个统一的输入框
            #   （本 fork 曾把上限拉到 100 万以免长文截断），用户换个厂商就可能
            #   直接撞上限被拒。实测：DeepSeek 合法区间 [1, 393216]，
            #   配 520000 → 400 Invalid max_tokens value → 整理版根本不生成，
            #   而且报错发生在**请求发出前**，用户只会看到「没有产出」。
            #
            # 处置：只在错误信息**明确指向 max_tokens** 时降级重试，
            # 不做无条件重试（避免把真实的鉴权/额度错误也重试一遍）。
            msg = str(first_exc)
            if "max_tokens" not in msg:
                raise
            # 优先用服务端自己报出来的上限（比任何硬编码都准）
            ceiling = _max_tokens_ceiling(msg)
            safe = ceiling if ceiling > 0 else _FALLBACK_MAX_TOKENS
            logger.warning(
                f"max_tokens={want_max} 被服务端拒绝（各家上限不同）。"
                + (f"服务端报告上限为 {ceiling}，降到 {safe} 重试一次。"
                   if ceiling else f"未能从报错里读出上限，先降到 {safe} 重试一次。")
            )
            want_max = safe
            response = _create(want_max)

        if hasattr(response, "choices"):
            optimized_text = response.choices[0].message.content
        elif isinstance(response, dict):
            optimized_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        elif isinstance(response, str):
            if response.strip().startswith("<!doctype") or response.strip().startswith("<html"):
                logger.error("API 返回了 HTML 页面而不是 JSON 响应")
                return None
            optimized_text = response
        else:
            logger.error(f"未知的响应格式: {type(response)}")
            return None

        if not optimized_text:
            logger.error("API 返回空内容")
            return None

        if optimized_text.strip().startswith("<!doctype") or optimized_text.strip().startswith("<html"):
            logger.error("API 返回了 HTML 页面而不是文本内容")
            return None

        # 2026-09-12: 剥离开头的 <think>...</think> 思维链（M3 / DeepSeek-R1 等推理模型）
        optimized_text = _strip_thinking_block(optimized_text)
        if not optimized_text:
            logger.error("API 返回内容在剥离 thinking 块后为空")
            return None

        logger.info(f"文本优化完成 (耗时: {format_time(time.time() - start_time)})")
        return optimized_text

    except Exception as e:
        logger.error(f"API 调用失败: {e}")
        if hasattr(e, "response"):
            logger.error(f"HTTP 状态码: {getattr(e.response, 'status_code', 'unknown')}")
        return None


def _optimize_with_anthropic(text: str, config: dict, prompt_name: str) -> Optional[str]:
    """Optimize text with Anthropic."""
    try:
        from anthropic import Anthropic
    except ImportError:
        logger.error("未安装 anthropic 库，请运行: pip install anthropic")
        return None

    start_time = time.time()
    client = Anthropic(api_key=config.get("api_key"))

    prompt_template = load_prompt(prompt_name)
    if not prompt_template:
        return None
    prompt = _render_prompt(prompt_template, text)

    response = client.messages.create(
        model=config.get("model", "claude-3-5-sonnet-20241022"),
        max_tokens=config.get("max_tokens", 4000),
        temperature=config.get("temperature", 0.3),
        messages=[{"role": "user", "content": prompt}],
    )

    optimized_text = response.content[0].text
    # 2026-09-12: 剥离开头的 <think>...</think> 思维链（M3 / DeepSeek-R1 等推理模型）
    optimized_text = _strip_thinking_block(optimized_text)
    logger.info(f"文本优化完成 (耗时: {format_time(time.time() - start_time)})")
    return optimized_text
