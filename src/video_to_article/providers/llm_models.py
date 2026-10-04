"""大模型服务端的模型列表自动发现（2026-10-04）。

## 为什么必须用 requests 直连，不能用 openai SDK

实测（2026-10-04，本机环境）：

    openai.__version__        = 3.8.0
    OpenAI(api_key="x").chat  → 可用（懒加载，当前转写不受影响）
    OpenAI(api_key="x").models → ModuleNotFoundError: No module named 'jiter'

`pyproject.toml` 只声明了 `openai>=1.0.0`，而 openai 从 2.x 起把 `jiter`
变成了硬依赖 —— 声明 `>=1.0.0` 可能装出 3.x，但项目没跟着声明它的传递依赖。

所以任何走 `client.models.list()` 的实现在当前依赖下**会直接崩**。
直连 requests 还有第二个好处：绕开 SDK 对响应格式的额外校验，
对国产 provider 的兼容性更好。

## 实测通过（2026-10-04，用真实 base_url，只读不计费）

    GET https://api.minimaxi.com/v1/models
      → HTTP 200
      → {"object": "list", "data": [{"id", "object", "created", "owned_by"}]}

标准 OpenAI 格式。

## 设计原则

**抓取失败绝不影响正常配置。** 不是所有 provider 都实现 `/models`，
所以失败一律降级，绝不阻断保存、绝不清空用户已填的 Model。
"""
from __future__ import annotations

import json
from typing import List, Optional, Tuple

from ..logging_config import configure_logging
from ..text_utils import import_required

logger = configure_logging()

DEFAULT_TIMEOUT = 15
# 部分网关默认只返回前几条，尽量要全量（服务端不认这个参数时会忽略）
_LIST_QUERY = "?limit=200"


def build_models_url(base_url: str) -> str:
    """把 base_url 拼成模型列表地址，容忍各种写法。

    容忍这些情况（实测用户配置里都出现过）：

    - 带不带末尾斜杠：``https://x/v1`` / ``https://x/v1/``
    - 末尾重复的 v1：``https://x/v1`` 不应变成 ``.../v1/v1/models``
    - 只给了根：``https://x`` → 补 ``/v1/models``
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/models"):
        return base
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/models"


def parse_models_response(payload) -> List[str]:
    """从响应里提取模型 id 列表。

    兼容几种常见形态（各家 provider 不一）：

    - OpenAI 标准：``{"object": "list", "data": [{"id": "..."}]}``
    - 老式：``{"data": ["gpt-4o", ...]}``（直接是字符串数组）
    - 单 key：``{"models": [...]}``

    解析失败返回 []，**不抛异常** —— 交给上层降级。
    """
    if isinstance(payload, list):
        # 直接就是数组
        return [str(x) for x in payload if isinstance(x, (str, int)) and str(x).strip()]

    if not isinstance(payload, dict):
        return []

    items = payload.get("data")
    if items is None:
        items = payload.get("models")
    if not isinstance(items, list):
        return []

    out: List[str] = []
    seen = set()
    for it in items:
        mid = None
        if isinstance(it, str):
            mid = it
        elif isinstance(it, dict):
            for key in ("id", "name", "model", "model_name"):
                v = it.get(key)
                if isinstance(v, str) and v.strip():
                    mid = v.strip()
                    break
        if mid and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def fetch_models(
    base_url: str,
    api_key: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[List[str], Optional[str]]:
    """抓取模型列表。

    ⚠ **绝不走 openai SDK**（缺 jiter，见模块 docstring）。

    Returns:
        ``(models, error)``
          - 成功：``(["a", "b"], None)``
          - 失败：``([], "失败原因")`` —— 上层据此提示，**不清空用户已填的值**
    """
    url = build_models_url(base_url)
    if not url:
        return [], "Base URL 为空"

    requests = import_required("requests", "requests")

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = requests.get(f"{url}{_LIST_QUERY}", headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        return [], f"请求超时（>{timeout}s），该服务可能没有 /models 接口"
    except requests.exceptions.RequestException as e:
        return [], f"网络请求失败: {type(e).__name__}"

    # 服务端返回 HTML 登录页 / 错误页是常见情况，别把整页塞给用户
    ctype = str(resp.headers.get("Content-Type") or "").lower()
    if "json" not in ctype:
        return [], f"该地址未返回 JSON（Content-Type={ctype or '空'}），多半不支持 /models"

    if resp.status_code == 404:
        return [], "该服务没有实现 /models 接口，请手动填写模型名"
    if resp.status_code in (401, 403):
        return [], "鉴权失败：请检查 API Key 是否正确、是否有该模型列表的权限"
    if resp.status_code != 200:
        return [], f"服务端返回 HTTP {resp.status_code}"

    try:
        payload = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        return [], "响应不是合法 JSON"

    models = parse_models_response(payload)
    if not models:
        return [], "响应里没解析出模型列表（结构可能不常见）"

    # ⚠ 只打条数，**不打印 key**，也不把整个列表刷进日志。
    # url 也用本地拼的那个，不碰 resp.request（stub / 某些 transport 没有该属性）。
    logger.info(f"已从服务端抓取 {len(models)} 个模型（{url}）")
    return models, None
