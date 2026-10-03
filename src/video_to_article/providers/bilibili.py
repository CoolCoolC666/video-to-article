"""Bilibili search, metadata, and CC/AI subtitle helpers."""

from __future__ import annotations

import html
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

from ..logging_config import configure_logging
from ..text_utils import import_required

logger = configure_logging()

BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
BILIBILI_SPI_API = "https://api.bilibili.com/x/frontend/finger/spi"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ==== B 站 ID 解析（2026-10-03 重写，严格语法）====
#
# 核心约束：**宁可返回空串，也不要返回一个错的 ID**。
# 错的 BV 号会让下游静默拉到别的视频 / 别的字幕，比直接失败难查得多。

# BV 号的 base58 字母表（去掉 1 / i / l / o 四个易混字符）
_BV_TABLE = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
_BV_TR = {c: i for i, c in enumerate(_BV_TABLE)}
_BV_S = [11, 10, 3, 8, 4, 6]   # 位置重排表
_BV_XOR = 177451812
_BV_ADD = 8728348608
# 官方注明本算法只覆盖 aid < 2^35（29460791296）
_BV_MAX_AID = 2 ** 35

# BV 号：BV1 + 9 位 base58，长度固定 12
RE_BV = re.compile(r"\bBV1[1-9A-HJ-NP-Za-km-z]{9}\b", re.IGNORECASE)
# 显式 av 前缀 + 1~20 位数字（覆盖现行 15/18 位 aid）
RE_AV_PREFIXED = re.compile(r"\bav(\d{1,20})\b", re.IGNORECASE)
# URL 里的 /video/av{数字}
RE_AV_IN_URL = re.compile(r"/video/av(\d{1,20})\b", re.IGNORECASE)
# bilibili 视频 URL（子域名任意：b23 短链常跳 m.）
RE_BILI_URL = re.compile(
    r"https?://(?:[a-z0-9-]+\.)?bilibili\.com/video/"
    r"(?:av(\d{1,20})|(BV1[1-9A-HJ-NP-Za-km-z]{9}))",
    re.IGNORECASE,
)
# b23.tv 短链
RE_SHORT_URL = re.compile(r"https?://b23\.tv/[\w]+", re.IGNORECASE)
# 裸数字（仅 allow_bare_numbers=True 时启用，6~16 位）
RE_BARE_DIGITS = re.compile(r"(?<!\d)\d{6,16}(?!\d)")

ORDER_MAP = {
    "totalrank": "totalrank",
    "pubdate": "pubdate",
    "click": "click",
    "dm": "dm",
}


def clean_bilibili_title(title: str) -> str:
    """Strip Bilibili search highlight tags and decode HTML entities."""
    if not title:
        return "未知标题"
    text = html.unescape(str(title))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip() or "未知标题"


def normalize_bvid(bvid: str) -> str:
    """归一化 BV 号的**前缀**，主体保持原样。

    2026-10-03：用户实测把稍后再看 URL 粘进来时发现的大小写问题。

    ⚠ **只归一化前缀，绝对不能把整个号转大写**——base58 字母表
    （fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF）
    本身大小写混排，BV1FpLU62EZW 和 BV1FPLU62EZW 是**两个不同的视频**。
    整体 upper() 会静默拉到别的视频。

    归一化内容：
        "bv1FpLU62EZW" -> "BV1FpLU62EZW"   前缀小写 → 合法
        "BV1FpLU62EZW"  -> "BV1FpLU62EZW"   已是规范
        "bv1fplu62ezw"  -> "BV1fplu62ezw"   前缀修了，主体仍按原样校验

    Returns:
        归一化后的字符串；不是 BV 形状时原样返回
    """
    s = (bvid or "").strip()
    if len(s) >= 3 and s[:2].upper() == "BV" and s[2] == "1":
        return "BV1" + s[3:]
    return s


def is_valid_bvid(bvid: str) -> bool:
    """BV 号是否合法：12 位 + "BV1" 前缀 + 9 位合法 base58 字符（区分大小写）。"""
    s = normalize_bvid(bvid)
    if len(s) != 12 or not s.startswith("BV1"):
        return False
    return all(ch in _BV_TR for ch in s[3:])


def extract_bvid(url_or_id: str) -> str:
    """从 URL / 裸 BV 号里提取 BV 号（严格语法 + 前缀归一化）。

    2026-10-03 重写。之前是 `re.search(r"(BV[\\w]+)")`，会把 "BV1garbage!!!" 这类
    也当 BV 号返回，下游拿它拼 API 必然 404。

    真实 BV 号 = "BV1" + 9 位 base58（字母表去掉 1 / i / l / o 四个易混字符）。
    长度固定 12 位，用 \\b 边界避免从更长的串里截一段。

    匹配用 IGNORECASE（用户手打可能写成 bv1…），但**返回前会归一化前缀**——
    主体字符保持原样，因为 base58 区分大小写。
    """
    if not url_or_id:
        return ""
    match = RE_BV.search(str(url_or_id))
    return normalize_bvid(match.group(0)) if match else ""


def extract_aid(url_or_id: str) -> str:
    """从 URL / 显式 av 号里提取数字 aid（严格语法）。

    2026-10-03 重写。之前是 `re.search(r"(?:av|/video/av)?(\\d{5,})", text)`——
    前缀是**可选**的，等于「任意 5 位以上数字都算 aid」。实测误判：

        "介绍 painter 这个 2026 年的展览，共 12345 件作品" -> "12345"   ❌
        "课程回放 20260927220839"                          -> "20260927220839" ❌

    这些会把版权清单 / 课程表里的年份和统计数字当 AV 号去查 B 站 API。
    现在只认两种形式：显式 `av` 前缀，或 URL 里的 `/video/av{数字}`。
    裸数字默认**不**认（真要认请显式写 av 前缀）。
    """
    if not url_or_id:
        return ""
    text = str(url_or_id)
    # 先看 URL 里的 /video/av{数字}（大写 AV 也认）
    m = RE_AV_IN_URL.search(text)
    if m:
        return m.group(1)
    # 再看显式 av 前缀（1-20 位，覆盖现行 15/18 位 aid）
    m = RE_AV_PREFIXED.search(text)
    if m:
        return m.group(1)
    return ""


def aid_to_bvid(aid: str | int) -> str:
    """数字 aid → 12 位 BV 号（本地纯计算，不联网）。

    算法：base58 + 固定异或/加法 + 位置重排（BV1_ _4 1 7 _ _ 模板）。
    锚点向量（多语言实现一致）：av170001 ↔ BV17x411w7KC

    ⚠ 官方注明本算法只覆盖 aid < 2^35（29460791296）。超范围返回空串——
    **绝不能返回一个错的 BV 号**，那会静默拉到别的视频字幕，比返回空更糟。
    超范围时应改走 B 站 view API 拿 bvid。
    """
    try:
        n = int(str(aid).strip())
    except (TypeError, ValueError):
        return ""
    if n <= 0 or n >= _BV_MAX_AID:
        logger.warning(
            f"aid={n} 超出本地 BV 转换算法范围（< {_BV_MAX_AID}），"
            "将改走 B 站 view API 查 bvid"
        )
        return ""
    x = (n ^ _BV_XOR) + _BV_ADD
    out = list("BV1  4 1 7  ")
    for i in range(6):
        out[_BV_S[i]] = _BV_TABLE[(x // 58 ** i) % 58]
    return "".join(out)


def bvid_to_aid(bvid: str) -> str:
    """12 位 BV 号 → 数字 aid（本地纯计算，不联网）。解析失败返回空串。"""
    b = (bvid or "").strip()
    if not is_valid_bvid(b):
        return ""
    try:
        r = sum(_BV_TR[b[_BV_S[i]]] * 58 ** i for i in range(6))
    except (KeyError, IndexError):
        return ""
    return str((r - _BV_ADD) ^ _BV_XOR)


@dataclass
class BilibiliRef:
    """从自由文本里解析出的一条 B 站引用。"""

    kind: str  # "bv" | "av" | "short_url"
    value: str  # "BV1xxx" / "170001" / "https://b23.tv/xxx"
    raw: str = ""  # 命中的原始片段

    @property
    def bvid(self) -> str:
        """能直接算出 BV 号就返回（AV 走本地转换），否则空串。"""
        if self.kind == "bv":
            return self.value
        if self.kind == "av":
            return aid_to_bvid(self.value)
        return ""


def parse_bilibili_refs(text: str, *, allow_bare_numbers: bool = False) -> List[BilibiliRef]:
    """从一整段文本里按优先级抠出所有 B 站引用。

    设计要点（参考 bilibili_tool 的 parser 设计，代码独立实现）：
      1. **优先级 + 消费区间**：URL > 短链 > 显式 av > 显式 BV > [可选]裸数字。
         命中过的区间不再被后面的规则重复解析，否则 "…/video/av170001" 里的
         "av170001" 会被当成另一条独立记录。
      2. **裸数字默认不认**：版权清单 / 课程表里的 "2026" "100" "12345" 会被误伤。
         allow_bare_numbers=True 时才认 6-16 位（覆盖现行 15 位 aid）。
      3. **按出现顺序 + 去重**：用户粘一堆 ID 进来时，顺序要跟原文一致。

    Args:
        text: 原始输入（可含换行、空格分隔的多个 ID）
        allow_bare_numbers: 是否把 6-16 位裸数字当 aid（更激进）

    Returns:
        按原文出现顺序排列、已去重的 BilibiliRef 列表
    """
    if not text:
        return []
    collected: List[tuple[int, BilibiliRef]] = []
    consumed: List[tuple[int, int]] = []

    def _free(span: tuple[int, int]) -> bool:
        s, e = span
        return all(e <= cs or s >= ce for cs, ce in consumed)

    def _take(m, ref: BilibiliRef) -> None:
        collected.append((m.start(), ref))
        consumed.append(m.span())

    # 1) bilibili.com 视频 URL（子域名任意：b23 短链常跳 m.）
    for m in RE_BILI_URL.finditer(text):
        if not _free(m.span()):
            continue
        raw = m.group(0)
        if m.group(1):  # /video/av{数字}
            _take(m, BilibiliRef("av", m.group(1), raw))
        else:  # /video/BV1xxx
            tail = raw.split("/video/")[-1].split("?")[0].split("/")[0]
            if is_valid_bvid(tail):
                _take(m, BilibiliRef("bv", normalize_bvid(tail), raw))
    # 2) b23.tv 短链（要联网才能展开，先原样返回）
    for m in RE_SHORT_URL.finditer(text):
        if _free(m.span()):
            _take(m, BilibiliRef("short_url", m.group(0), m.group(0)))
    # 3) 显式 BV
    for m in RE_BV.finditer(text):
        if _free(m.span()):
            _take(m, BilibiliRef("bv", normalize_bvid(m.group(0)), m.group(0)))
    # 4) 显式 av 前缀
    for m in RE_AV_PREFIXED.finditer(text):
        if _free(m.span()):
            _take(m, BilibiliRef("av", m.group(1), m.group(0)))
    # 5) 裸数字（默认关闭）
    if allow_bare_numbers:
        for m in RE_BARE_DIGITS.finditer(text):
            if _free(m.span()):
                _take(m, BilibiliRef("av", m.group(0), m.group(0)))

    collected.sort(key=lambda x: x[0])
    seen: set[tuple[str, str]] = set()
    out: List[BilibiliRef] = []
    for _, ref in collected:
        key = (ref.kind, ref.value)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def expand_b23_shortlink(url: str, *, timeout: int = 15) -> str:
    """把 b23.tv 短链展开成真实 URL（跟随 302，不下载内容）。

    失败返回空串——短链展开失败不该让整条 pipeline 失败。
    """
    if not url or "b23.tv" not in url.lower():
        return ""
    requests = import_required("requests", "requests")
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": DEFAULT_UA},
            allow_redirects=True,
            timeout=timeout,
            stream=True,  # 只看跳转链，不拉正文
        )
        final = str(resp.url or "")
        resp.close()
        return final
    except Exception as e:
        logger.warning(f"b23 短链展开失败 ({url}): {e}")
        return ""


def resolve_bilibili_bvid(
    ref: str,
    *,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> str:
    """把任意 B 站引用（URL / BV / av / b23 短链）解析成 12 位 BV 号。

    这是下游所有需要 bvid 的接口（view / player 字幕）的统一入口。

    解析链：
        BV 号            → 直接返回（校验合法性）
        av 号 / av URL   → 本地算法转换（aid < 2^35 时）
                          超范围 → 调 view API 查（网络）
        b23 短链         → 跟随 302 → 回到上面任一分支
        完整 URL         → 从 URL 里抠 BV 或 av
        其它             → 抠出第一个引用（支持"一堆文本里只认一个"的场景）

    Returns:
        12 位 BV 号；解析不出来返回空串
    """
    if not ref:
        return ""
    text = str(ref).strip()

    # 1) 直接就是合法 BV（注意返回**归一化后**的，不能返回原串——
    #    用户手打 "bv1FpLU62EZW" 时小写前缀会让 B 站 API 查不到）
    if is_valid_bvid(text):
        return normalize_bvid(text)

    # 2) 文本里可能有多个引用——取第一个能解析成 bvid 的
    for r in parse_bilibili_refs(text):
        if r.kind == "bv":
            return r.value
        if r.kind == "av":
            bv = aid_to_bvid(r.value)
            if bv:
                return bv
            # 超范围：退到 view API
            return _bvid_via_view_api(r.value, cookies_from_browser, cookies_file) or ""
        if r.kind == "short_url":
            expanded = expand_b23_shortlink(r.value)
            if expanded and expanded != r.value:
                logger.info(f"b23 短链已展开: {r.value} -> {expanded}")
                # 递归（展开后通常是 /video/BVxxx 或 /video/avxxx）
                return resolve_bilibili_bvid(
                    expanded,
                    cookies_from_browser=cookies_from_browser,
                    cookies_file=cookies_file,
                )
    return ""


def _bvid_via_view_api(
    aid: str,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> str:
    """超范围 aid（本地方案算不出）时，用 view API 查 bvid。"""
    try:
        session = _build_bilibili_session(cookies_from_browser, cookies_file)
        resp = session.get(
            f"https://api.bilibili.com/x/web-interface/view?aid={aid}", timeout=20
        )
        data = (resp.json() or {}).get("data") or {}
        bvid = str(data.get("bvid") or "")
        if bvid:
            logger.info(f"aid={aid} 已通过 view API 查到 bvid={bvid}")
        return bvid
    except Exception as e:
        logger.warning(f"用 view API 查 aid={aid} 的 bvid 失败: {e}")
        return ""



def search_bilibili_videos(keyword: str, count: int = 5, order: str = "totalrank") -> List[Dict[str, Any]]:
    """Search Bilibili videos.

    Strategy (in order):
    1. bilibili-api-python if installed
    2. Public HTTP API with SPI cookies (no extra dependency)
    3. yt-dlp bilisearch fallback
    """
    keyword = (keyword or "").strip()
    if not keyword:
        logger.warning("搜索关键词为空")
        return []

    count = max(1, int(count or 5))
    order = ORDER_MAP.get(order, "totalrank")
    logger.info(f"搜索B站视频: 关键词='{keyword}', 数量={count}, 排序={order}")

    for name, fn in (
        ("bilibili-api", lambda: _search_via_bilibili_api(keyword, count, order)),
        ("HTTP API", lambda: _search_via_http_api(keyword, count, order)),
        ("yt-dlp bilisearch", lambda: _search_via_ytdlp(keyword, count)),
    ):
        try:
            videos = fn()
        except Exception as e:
            logger.warning(f"{name} 搜索异常: {e}")
            videos = []
        if videos:
            logger.info(f"搜索完成（{name}），找到 {len(videos)} 个视频")
            return videos

    logger.error("B站搜索失败：所有搜索通道均不可用")
    return []


def _search_via_bilibili_api(keyword: str, count: int, order: str) -> List[Dict[str, Any]]:
    try:
        from bilibili_api import search, sync
    except ImportError:
        logger.info("未安装 bilibili-api-python，跳过该通道")
        return []

    try:
        order_map = {
            "totalrank": search.OrderVideo.TOTALRANK,
            "pubdate": search.OrderVideo.PUBDATE,
            "click": search.OrderVideo.CLICK,
            "dm": search.OrderVideo.DM,
        }
        order_type = order_map.get(order, search.OrderVideo.TOTALRANK)

        async def _search():
            return await search.search_by_type(
                keyword=keyword,
                search_type=search.SearchObjectType.VIDEO,
                order_type=order_type,
                page=1,
            )

        result = sync(_search())
        raw_items = (result or {}).get("result") or []
        return _normalize_search_items(raw_items, count)
    except Exception as e:
        logger.warning(f"bilibili-api 搜索失败: {e}")
        return []


def _build_bilibili_session(
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
):
    """Create a requests session; attach browser/file cookies when provided.

    AI/CC subtitles often require login (``need_login_subtitle``). Anonymous
    SPI cookies are enough for search but not for subtitle listing.
    """
    requests = import_required("requests", "requests")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.bilibili.com",
            "Referer": "https://www.bilibili.com",
        }
    )

    _attach_auth_cookies(session, cookies_from_browser, cookies_file)

    # Always ensure buvid exists for API hygiene
    if not session.cookies.get("buvid3", domain=".bilibili.com"):
        buvid3 = str(uuid.uuid4()).upper() + "infoc"
        try:
            spi = session.get(BILIBILI_SPI_API, timeout=15)
            spi.raise_for_status()
            payload = spi.json() if spi.content else {}
            data = (payload or {}).get("data") or {}
            if data.get("b_3"):
                buvid3 = data["b_3"]
            if data.get("b_4"):
                session.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
        except Exception as e:
            logger.warning(f"获取 B站 SPI 指纹失败，将使用本地 buvid3: {e}")
        session.cookies.set("buvid3", buvid3, domain=".bilibili.com")
    return session


def _attach_auth_cookies(
    session,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> None:
    """Load Bilibili cookies from Netscape file and/or browser via yt-dlp."""
    from http.cookiejar import MozillaCookieJar

    if cookies_file:
        try:
            jar = MozillaCookieJar(str(cookies_file))
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies.update(jar)
            logger.info(f"已加载 cookies 文件: {cookies_file}")
        except Exception as e:
            logger.warning(f"加载 cookie 文件失败 ({cookies_file}): {e}")

    if cookies_from_browser:
        try:
            yt_dlp = import_required("yt_dlp", "yt-dlp")
            from .youtube_auth import parse_browser_spec

            # YoutubeDL loads browser cookies into its cookiejar on init.
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "cookiesfrombrowser": parse_browser_spec(cookies_from_browser),
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                jar = getattr(ydl, "cookiejar", None)
                if jar is None:
                    raise RuntimeError("yt-dlp 未提供 cookiejar")
                count = 0
                for c in jar:
                    domain = (c.domain or "").lstrip(".")
                    if "bili" not in domain and "hdslb" not in domain:
                        # Keep SESSDATA / bili-related only to avoid bloating
                        name = c.name or ""
                        if name not in {
                            "SESSDATA",
                            "bili_jct",
                            "DedeUserID",
                            "DedeUserID__ckMd5",
                            "sid",
                            "buvid3",
                            "buvid4",
                        }:
                            continue
                    try:
                        session.cookies.set(
                            c.name,
                            c.value,
                            domain=c.domain or ".bilibili.com",
                            path=c.path or "/",
                        )
                        count += 1
                    except Exception:
                        continue
                logger.info(
                    f"已从浏览器导入 B站相关 cookies: {cookies_from_browser} ({count} 条)"
                )
                if count == 0:
                    # Fallback: import all cookies (some builds domain-filter too strict)
                    for c in jar:
                        try:
                            session.cookies.set(
                                c.name,
                                c.value,
                                domain=c.domain or ".bilibili.com",
                                path=c.path or "/",
                            )
                            count += 1
                        except Exception:
                            pass
                    if count:
                        logger.info(f"已回退导入全部 cookies 中的 {count} 条")
        except Exception as e:
            logger.warning(f"从浏览器读取 B站 cookies 失败: {e}")


def bilibili_json_to_srt(payload: dict) -> str:
    """Convert Bilibili CC/AI subtitle JSON to SRT text."""
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, list) or not body:
        return ""

    def _ts(sec: float) -> str:
        if sec < 0:
            sec = 0.0
        ms = int(round(sec * 1000))
        h, rem = divmod(ms, 3600_000)
        m, rem = divmod(rem, 60_000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

    lines: list[str] = []
    idx = 1
    for item in body:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        start = float(item.get("from") or 0)
        end = float(item.get("to") or start)
        if end <= start:
            end = start + 0.5
        lines.append(str(idx))
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(content)
        lines.append("")
        idx += 1
    return "\n".join(lines).strip() + ("\n" if lines else "")


def bilibili_json_to_plain(payload: dict) -> str:
    """Flatten subtitle JSON to plain transcript text."""
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, list):
        return ""
    parts = []
    for item in body:
        if isinstance(item, dict):
            c = str(item.get("content") or "").strip()
            if c:
                parts.append(c)
    return "\n".join(parts)


def list_bilibili_subtitle_tracks(
    video_url: str,
    *,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> tuple[list[dict], dict]:
    """List CC/AI subtitle tracks via Bilibili player API.

    Returns (tracks, meta) where each track has lan, lan_doc, subtitle_url.

    2026-10-03：用 resolve_bilibili_bvid() 取代 extract_bvid()。
    之前只认 BV 号，遇到 av 号 / av 形式 URL 直接判 invalid_bvid → 字幕取不到
    → 白白降级到 ASR（花钱 + 耗时长）。现在 av 会先转成 bvid 再查。
    """
    bvid = resolve_bilibili_bvid(
        video_url, cookies_from_browser=cookies_from_browser, cookies_file=cookies_file
    )
    meta: dict[str, Any] = {"platform": "Bilibili", "bvid": bvid}
    if not bvid:
        meta["caption_failure_reason"] = "invalid_bvid"
        return [], meta

    session = _build_bilibili_session(cookies_from_browser, cookies_file)
    view = session.get(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        timeout=20,
    ).json()
    if view.get("code") != 0:
        meta["caption_failure_reason"] = f"view_api:{view.get('message') or view.get('code')}"
        return [], meta

    data = view.get("data") or {}
    meta["title"] = data.get("title") or "未知标题"
    meta["id"] = bvid
    meta["thumbnail"] = data.get("pic") or data.get("banner") or ""
    meta["channel"] = (
        (data.get("owner") or {}).get("name")
        or data.get("owner_name")
        or ""
    )
    meta["uploader"] = meta["channel"]
    pages = data.get("pages") or []
    if not pages:
        meta["caption_failure_reason"] = "no_pages"
        return [], meta
    cid = pages[0].get("cid")
    meta["cid"] = cid

    player = session.get(
        f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}",
        timeout=20,
    ).json()
    if player.get("code") != 0:
        meta["caption_failure_reason"] = f"player_api:{player.get('message') or player.get('code')}"
        return [], meta

    pdata = player.get("data") or {}
    meta["need_login_subtitle"] = bool(pdata.get("need_login_subtitle"))
    meta["login_mid"] = pdata.get("login_mid")
    tracks = ((pdata.get("subtitle") or {}).get("subtitles") or [])
    # Normalize URL
    for t in tracks:
        url = t.get("subtitle_url") or ""
        if url.startswith("//"):
            t["subtitle_url"] = "https:" + url
    meta["caption_available_manual"] = [
        str(t.get("lan") or "") for t in tracks if t.get("subtitle_url")
    ]
    if not tracks:
        if meta.get("need_login_subtitle") and not meta.get("login_mid"):
            meta["caption_failure_reason"] = "auth_or_bot_check"
            logger.warning(
                "B站字幕需登录后可见，请使用 --cookies-from-browser 或 cookies 文件（已登录 bilibili.com）"
            )
        else:
            meta["caption_failure_reason"] = "no_caption_tracks"
    return tracks, meta


def pick_bilibili_subtitle_track(
    tracks: Sequence[dict],
    preferred_langs: Optional[Sequence[str]] = None,
) -> Optional[dict]:
    """Pick best track: zh AI/manual first, then preferred list, then first."""
    if not tracks:
        return None
    preferred = [str(p).lower() for p in (preferred_langs or [])]

    def score(t: dict) -> tuple:
        lan = str(t.get("lan") or "").lower()
        doc = str(t.get("lan_doc") or "")
        # Prefer Chinese
        zh = 0
        if lan in {"zh-cn", "zh", "ai-zh", "zh-hans"} or "中文" in doc:
            zh = 0
        elif lan.startswith("ai-zh") or "zh" in lan:
            zh = 1
        else:
            zh = 10
        # Prefer preferred lang match
        pref = 50
        for i, p in enumerate(preferred):
            if p in lan or lan in p or (p.startswith("zh") and "zh" in lan):
                pref = i
                break
        # Prefer non-ai slightly? Actually AI is fine; manual often empty lan
        ai = 1 if lan.startswith("ai-") else 0
        return (zh, pref, ai)

    ranked = sorted([t for t in tracks if t.get("subtitle_url")], key=score)
    return ranked[0] if ranked else None


def fetch_bilibili_subtitle_text(
    video_url: str,
    *,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    preferred_langs: Optional[Sequence[str]] = None,
) -> tuple[Optional[str], dict]:
    """Download best Bilibili CC/AI subtitle as plain text + metadata."""
    tracks, meta = list_bilibili_subtitle_tracks(
        video_url,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_file,
    )
    if not tracks:
        return None, meta

    track = pick_bilibili_subtitle_track(tracks, preferred_langs)
    if not track:
        meta["caption_failure_reason"] = "no_caption_tracks"
        return None, meta

    session = _build_bilibili_session(cookies_from_browser, cookies_file)
    url = track.get("subtitle_url") or ""
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        meta["caption_failure_reason"] = f"subtitle_download_failed:{e}"
        return None, meta

    plain = bilibili_json_to_plain(payload)
    if len(plain.strip()) < 8:
        meta["caption_failure_reason"] = "subtitle_empty"
        return None, meta

    meta.update(
        {
            "caption_source": "ai" if str(track.get("lan") or "").startswith("ai-") else "manual",
            "caption_language": track.get("lan") or track.get("lan_doc") or "unknown",
            "caption_ext": "json",
            "caption_failure_reason": None,
            "thumbnail": meta.get("thumbnail"),  # may be filled by caller
        }
    )
    # thumbnail from view already? not set — optional
    logger.info(
        f"已通过 B站 API 提取字幕: {meta.get('title')} "
        f"({meta.get('caption_source')}/{meta.get('caption_language')})"
    )
    return plain, meta


def download_bilibili_subtitles_to_dir(
    video_url: str,
    out_dir: Path,
    *,
    stem: str,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    preferred_langs: Optional[Sequence[str]] = None,
    all_langs: bool = False,
) -> tuple[list[str], dict]:
    """Write Bilibili subtitle files (srt + optional json) under out_dir.

    Returns (paths, meta).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks, meta = list_bilibili_subtitle_tracks(
        video_url,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_file,
    )
    if not tracks:
        return [], meta

    session = _build_bilibili_session(cookies_from_browser, cookies_file)
    if all_langs:
        chosen = [t for t in tracks if t.get("subtitle_url")]
    else:
        one = pick_bilibili_subtitle_track(tracks, preferred_langs)
        chosen = [one] if one else []

    saved: list[str] = []
    for track in chosen:
        lan = re.sub(r"[^\w\-]+", "_", str(track.get("lan") or "und"))[:20]
        url = track.get("subtitle_url") or ""
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.warning(f"下载 B站字幕失败 ({lan}): {e}")
            continue
        srt = bilibili_json_to_srt(payload)
        if not srt.strip():
            continue
        # SRT only — common format for players / tools; no need for raw JSON by default
        srt_path = out_dir / f"{stem}.{lan}.srt"
        srt_path.write_text(srt, encoding="utf-8")
        saved.append(str(srt_path))
        logger.info(f"B站字幕已保存: {srt_path}")

    if saved:
        meta["caption_failure_reason"] = None
        meta["caption_files"] = saved
        best = pick_bilibili_subtitle_track(tracks, preferred_langs) or tracks[0]
        meta["caption_source"] = (
            "ai" if str(best.get("lan") or "").startswith("ai-") else "manual"
        )
        meta["caption_language"] = best.get("lan") or "unknown"
    else:
        meta["caption_failure_reason"] = meta.get("caption_failure_reason") or "download_or_parse_failed"
    return saved, meta


def _search_via_http_api(keyword: str, count: int, order: str) -> List[Dict[str, Any]]:
    """Search via Bilibili public web API (no extra package)."""
    try:
        session = _build_bilibili_session()
    except RuntimeError as e:
        logger.error(str(e))
        return []

    page_size = min(max(count, 1), 50)
    params = {
        "search_type": "video",
        "keyword": keyword,
        "order": order,
        "page": 1,
        "page_size": page_size,
    }
    headers = {
        "Referer": f"https://search.bilibili.com/all?keyword={quote(keyword)}",
        "Origin": "https://search.bilibili.com",
    }

    try:
        response = session.get(BILIBILI_SEARCH_API, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        # Some anti-bot responses return HTML with 200.
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" not in content_type and not response.text.lstrip().startswith("{"):
            logger.error("B站 HTTP 搜索返回非 JSON（可能触发风控）")
            return []
        payload = response.json()
    except Exception as e:
        logger.error(f"B站 HTTP 搜索请求失败: {e}")
        return []

    if not isinstance(payload, dict):
        logger.error("B站 HTTP 搜索返回格式异常")
        return []

    code = payload.get("code")
    if code not in (0, None):
        logger.error(f"B站 HTTP 搜索业务错误: code={code}, message={payload.get('message')}")
        return []

    data = payload.get("data") or {}
    raw_items = data.get("result") or data.get("items") or []
    if not raw_items:
        logger.warning(f"搜索无结果: {keyword}")
        return []

    return _normalize_search_items(raw_items, count)


def _search_via_ytdlp(keyword: str, count: int) -> List[Dict[str, Any]]:
    """Fallback search using yt-dlp's bilisearch extractor."""
    try:
        yt_dlp = import_required("yt_dlp", "yt-dlp")
    except RuntimeError as e:
        logger.error(str(e))
        return []

    query = f"bilisearch{count}:{keyword}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": count,
        "http_headers": {
            "User-Agent": DEFAULT_UA,
            "Referer": "https://www.bilibili.com",
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as e:
        logger.warning(f"yt-dlp bilisearch 失败: {e}")
        return []

    entries = (info or {}).get("entries") or []
    videos: List[Dict[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        url = entry.get("url") or entry.get("webpage_url") or ""
        bvid = extract_bvid(url) or extract_bvid(str(entry.get("id") or ""))
        aid = extract_aid(url) or extract_aid(str(entry.get("id") or ""))
        if bvid:
            final_url = f"https://www.bilibili.com/video/{bvid}"
            media_id = bvid
        elif aid:
            final_url = f"https://www.bilibili.com/video/av{aid}"
            media_id = f"av{aid}"
        else:
            continue
        title = clean_bilibili_title(str(entry.get("title") or media_id))
        videos.append(
            {
                "url": final_url,
                "title": title,
                "bvid": bvid or media_id,
                "duration": int(entry.get("duration") or 0),
                "play": int(entry.get("view_count") or entry.get("play") or 0),
                "author": str(entry.get("uploader") or entry.get("channel") or "未知UP主"),
            }
        )
        if len(videos) >= count:
            break
    return videos


def _normalize_search_items(raw_items: list, count: int) -> List[Dict[str, Any]]:
    video_list: List[Dict[str, Any]] = []
    for video in raw_items:
        if not isinstance(video, dict):
            continue
        # Some payloads interleave tips / media cards.
        item_type = video.get("type")
        if item_type not in (None, "video", "bili_video") and not video.get("bvid"):
            continue
        bvid = video.get("bvid") or extract_bvid(str(video.get("arcurl") or video.get("url") or ""))
        aid = video.get("aid") or video.get("id")
        if not bvid and aid:
            bvid = ""
            url = f"https://www.bilibili.com/video/av{aid}"
        elif bvid:
            url = f"https://www.bilibili.com/video/{bvid}"
        else:
            continue

        title = clean_bilibili_title(str(video.get("title") or "未知标题"))
        duration_raw = video.get("duration") or video.get("length") or "0:00"
        play = video.get("play") or video.get("view") or video.get("video_review") or 0
        try:
            play = int(play)
        except (TypeError, ValueError):
            play = 0
        author = video.get("author") or video.get("uname") or video.get("owner") or "未知UP主"
        if isinstance(author, dict):
            author = author.get("name") or author.get("uname") or "未知UP主"

        video_list.append(
            {
                "url": url,
                "title": title,
                "bvid": bvid or f"av{aid}",
                "duration": _parse_duration(str(duration_raw)),
                "play": play,
                "author": str(author),
            }
        )
        if len(video_list) >= count:
            break

    if not video_list:
        logger.warning("搜索结果解析后为空")
    return video_list


def _parse_duration(duration_str: str) -> int:
    """Parse duration string into seconds."""
    try:
        text = str(duration_str).strip()
        if text.isdigit():
            return int(text)
        parts = text.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
    except Exception:
        return 0


def format_duration(seconds: int) -> str:
    """Format seconds as duration text."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:02d}"


def format_play_count(count: int) -> str:
    """Format play count in Chinese compact form."""
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    if count >= 10000:
        return f"{count / 10000:.1f}万"
    return str(count)


def get_bilibili_video_url(bvid: str) -> str:
    """把任意 B 站引用拼成标准视频 URL。

    2026-10-03：改走 resolve_bilibili_bvid()，这样传 av 号 / av URL / b23 短链
    也能拿到规范 URL，而不是像原来 `extract_bvid(x) or x` 那样把原始垃圾串
    直接塞进 URL（拼出 https://www.bilibili.com/video/BV1garbage 这种）。
    解析不出来时退回原值——保持旧行为，不擅自抛错。
    """
    resolved = resolve_bilibili_bvid(bvid) or (extract_bvid(bvid) or bvid)
    return f"https://www.bilibili.com/video/{resolved}"
