"""Smoke tests for Bilibili link/ID parsing（2026-10-03 重写）。

Covers:
  1. extract_bvid 严格语法（长度 12 + BV1 + 合法 base58）
  2. extract_aid 严格语法（只认 av 前缀 / /video/av{数字}，**裸数字不认**）
  3. av <-> BV 互转（用官方锚点向量 av170001 <-> BV17x411w7KC）
  4. 超范围 aid 返回空串（绝不返回错的 BV）
  5. parse_bilibili_refs 优先级 + 消费区间 + 顺序 + 去重 + 裸数字开关
  6. resolve_bilibili_bvid 统一入口（BV / av / av URL / b23 短链 / 一堆文本）
  7. av 形式 URL 能解析出 bvid（修「字幕取不到只能降级 ASR」的缺口）

不联网 —— b23 短链展开那条用 stub 掉网络。
从仓库根运行：python tests\\smoke_bilibili_parse.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"src")

from video_to_article.providers.bilibili import (  # noqa: E402
    RE_BARE_DIGITS,
    aid_to_bvid,
    bvid_to_aid,
    extract_aid,
    extract_bvid,
    is_valid_bvid,
    parse_bilibili_refs,
    resolve_bilibili_bvid,
)

# 官方算法锚点向量（多语言实现一致输出）
ANCHOR = (170001, "BV17x411w7KC")


def test_extract_bvid_strict():
    """1. BV 严格语法。"""
    # 正常
    assert extract_bvid("BV1xx411c7mD") == "BV1xx411c7mD"
    assert extract_bvid("https://www.bilibili.com/video/BV1FpLU62EZW") == "BV1FpLU62EZW"
    assert extract_bvid("BV1mK4y1C7Bz") == "BV1mK4y1C7Bz"
    # 旧的 `BV[\w]+` 会把这些也当 BV 返回，下游必然 404
    assert extract_bvid("BV1garbage!!!") == "", "带非法字符的不该认"
    assert extract_bvid("BV1") == "", "只有前缀的不该认"
    assert extract_bvid("随便一段文字") == "", "普通文字不该认"
    assert extract_bvid("") == ""
    # base58 排除 1/i/l/o 作为第 4 位起（B 站设计如此）
    assert not is_valid_bvid("BV1il1o1a1B"), "i/l/o 不该出现在 BV 主体里"
    print("OK 1: extract_bvid 严格语法（拒绝 BV1garbage / BV1 / 普通文字）\n")


def test_extract_aid_strict():
    """2. extract_aid 严格语法 —— 这是本次修的核心 bug。"""
    # 之前用 `(?:av|/video/av)?(\d{5,})`，前缀可选 = 任意 5+ 位数字都算 aid
    assert extract_aid("介绍 painter 这个 2026 年的展览，共 12345 件作品") == "", (
        "文本里的 12345 不该被当 aid"
    )
    assert extract_aid("课程回放 20260927220839") == "", "时间戳不该被当 aid"
    assert extract_aid("2026") == "", "年份不该被当 aid"
    # 仍然要认的
    assert extract_aid("av170001") == "170001"
    assert extract_aid("AV170001") == "170001", "大写 AV 也该认"
    assert extract_aid("https://www.bilibili.com/video/av170001") == "170001"
    assert extract_aid("https://www.bilibili.com/video/av113102813136198") == "113102813136198", (
        "18 位 aid 不能被截断"
    )
    # 裸数字默认不认
    assert extract_aid("170001") == "", "裸数字默认不认（要认请显式写 av 前缀）"
    assert extract_aid("") == ""
    print("OK 2: extract_aid 严格（裸数字/年份/时间戳不再误判，av 前缀与 18 位仍认）\n")


def test_av_bv_convert():
    """3. av <-> BV 互转，用官方锚点向量验证（不靠 round-trip 自证）。"""
    aid, bv = ANCHOR
    assert aid_to_bvid(aid) == bv, f"锚点向量错: av{aid} -> {aid_to_bvid(aid)}，期望 {bv}"
    assert bvid_to_aid(bv) == str(aid), f"锚点向量错: {bv} -> {bvid_to_aid(bv)}，期望 {aid}"
    # 多个真实量级的 aid 双向自洽
    for n in (1, 170001, 25824905, 497002679, 69124672, 29460791295):
        got = aid_to_bvid(n)
        assert is_valid_bvid(got), f"av{n} -> {got!r} 不是合法 BV"
        assert bvid_to_aid(got) == str(n), f"av{n} -> {got} -> {bvid_to_aid(got)} 未还原"
    # 无效输入
    assert aid_to_bvid("") == ""
    assert aid_to_bvid("abc") == ""
    assert bvid_to_aid("") == ""
    assert bvid_to_aid("BV1garbage!!!") == "", "非法 BV 不该解出数字"
    print(f"OK 3: av<->BV 互转对（锚点 av{aid} <-> {bv} + 6 个量级 round-trip）\n")


def test_av_out_of_range():
    """4. 超范围 aid 必须返回空串 —— 绝不能返回一个错的 BV。

    错的 BV 会静默拉到别的视频字幕，比返回空难查得多。
    """
    assert aid_to_bvid(2 ** 35) == "", "2^35 恰好超范围（官方注明 < 2^35）"
    assert aid_to_bvid(999999999999) == "", "12 位 aid 超范围"
    assert aid_to_bvid(0) == "", "0 不是合法 aid"
    assert aid_to_bvid(-1) == "", "负数不是合法 aid"
    # 边界内最大
    assert is_valid_bvid(aid_to_bvid(2 ** 35 - 1)), "2^35-1 应在范围内"
    print("OK 4: 超范围 aid 返回空串（不返回错的 BV），边界 2^35 正确切分\n")


def test_parse_refs():
    """5. parse_bilibili_refs：优先级 + 消费区间 + 顺序 + 去重 + 裸数字开关。"""
    # 消费区间：URL 里的 av170001 不该再被"显式 av"规则重复解析
    refs = parse_bilibili_refs("https://www.bilibili.com/video/av170001")
    assert len(refs) == 1, f"av URL 应只出 1 条，实得 {len(refs)}: {refs}"
    assert refs[0].kind == "av" and refs[0].value == "170001", f"解析错: {refs[0]}"

    refs = parse_bilibili_refs("https://www.bilibili.com/video/BV1FpLU62EZW")
    assert len(refs) == 1, f"BV URL 应只出 1 条，实得 {refs}"
    assert refs[0].kind == "bv" and refs[0].value == "BV1FpLU62EZW", f"解析错: {refs[0]}"

    # 顺序 + 多类型混合
    refs = parse_bilibili_refs("BV1mK4y1C7Bz https://b23.tv/aBcDeF av999")
    assert [r.kind for r in refs] == ["bv", "short_url", "av"], f"顺序/类型错: {refs}"
    assert [r.value for r in refs] == ["BV1mK4y1C7Bz", "https://b23.tv/aBcDeF", "999"], (
        f"值错: {[r.value for r in refs]}"
    )

    # 去重（同一条出现两次只留一条，且保留首次位置）
    refs = parse_bilibili_refs("BV1xx411c7mD BV1xx411c7mD BV17x411w7KC")
    assert [r.value for r in refs] == ["BV1xx411c7mD", "BV17x411w7KC"], f"去重错: {refs}"

    # 裸数字开关
    text = "2026 年的 12345 件 170001"
    assert parse_bilibili_refs(text) == [], f"裸数字默认不该认: {parse_bilibili_refs(text)}"
    got = [r.value for r in parse_bilibili_refs(text, allow_bare_numbers=True)]
    assert got == ["170001"], f"开启后应只认 6-16 位（12345 是 5 位不算），实得 {got}"

    # 空输入
    assert parse_bilibili_refs("") == []
    assert parse_bilibili_refs(None) == []

    # 现行 aid 是 15 位（如 113102813136198），在 6-16 裸数字范围内，会被认
    assert RE_BARE_DIGITS.findall("113102813136198") == ["113102813136198"], (
        "15 位现行 aid 在裸数字范围内，应该被认"
    )
    # 18 位（动态/评论 ID 一类）在范围外，且不该被截成前 16 位
    assert RE_BARE_DIGITS.findall("123456789012345678") == [], (
        "18 位不该被裸数字规则匹配，更不该截成前 16 位"
    )
    print("OK 5: parse_bilibili_refs（消费区间/顺序/去重/裸数字开关）\n")


def test_resolve_bvid():
    """6. resolve_bilibili_bvid 统一入口。"""
    aid, bv = ANCHOR
    # BV 直给
    assert resolve_bilibili_bvid(bv) == bv
    # av 号 / av URL → 本地转换
    assert resolve_bilibili_bvid(f"av{aid}") == bv, "av 号应能转出 BV"
    assert resolve_bilibili_bvid(f"https://www.bilibili.com/video/av{aid}") == bv, (
        "av URL 应能转出 BV —— 这是修好的字幕缺口"
    )
    # 裸数字默认不认（与 parse 一致的设计取舍：宁可空串也不误判）
    # 真有裸 aid 请显式写 av 前缀
    assert resolve_bilibili_bvid(str(aid)) == "", "裸数字默认不该被当成 aid"
    assert resolve_bilibili_bvid(f"av{aid}") == bv
    # 一堆文本里认第一个
    assert resolve_bilibili_bvid("随便一段话 BV1FpLU62EZW 后面还有 av123") == "BV1FpLU62EZW"
    # 认不出来的
    assert resolve_bilibili_bvid("") == ""
    assert resolve_bilibili_bvid("完全没有 B 站信息的一段话") == ""
    print("OK 6: resolve_bilibili_bvid（BV/av/av URL/混合文本）\n")


def test_resolve_shortlink(monkey_sandbox=True):
    """7. b23 短链展开（stub 掉网络，不发真实请求）。"""
    from video_to_article.providers import bilibili as B

    aid, bv = ANCHOR
    calls: list = []

    class FakeResp:
        def __init__(self, url):
            self.url = url

        def close(self):
            pass

    class FakeRequests:
        @staticmethod
        def get(url, **kwargs):
            calls.append(url)
            return FakeResp(f"https://www.bilibili.com/video/{bv}")

    orig_import = B.import_required
    B.import_required = lambda mod, pkg=None: FakeRequests if mod == "requests" else orig_import(mod, pkg)
    try:
        got = B.expand_b23_shortlink("https://b23.tv/aBcDeF")
        assert got.endswith(bv), f"短链应展开到真实 URL，实得 {got}"
        assert calls, "应发起过一次请求"
        # 走 resolve 也要能穿透
        assert resolve_bilibili_bvid("https://b23.tv/aBcDeF") == bv
    finally:
        B.import_required = orig_import

    # 展开失败不该抛错
    class BoomRequests:
        @staticmethod
        def get(url, **kwargs):
            raise RuntimeError("network down")

    B.import_required = lambda mod, pkg=None: BoomRequests if mod == "requests" else orig_import(mod, pkg)
    try:
        assert B.expand_b23_shortlink("https://b23.tv/x") == "", "展开失败应返回空串而非抛错"
    finally:
        B.import_required = orig_import

    # 非短链不进网络
    assert B.expand_b23_shortlink("https://www.bilibili.com/video/BV1xx411c7mD") == ""
    print("OK 7: b23 短链展开（成功穿透 / 失败返空不抛错 / 非短链不发请求）\n")


def test_get_video_url():
    """8. get_bilibili_video_url 不再把垃圾串塞进 URL。"""
    from video_to_article.providers.bilibili import get_bilibili_video_url

    aid, bv = ANCHOR
    assert get_bilibili_video_url(bv) == f"https://www.bilibili.com/video/{bv}"
    # 传 av 号也能规范成 BV 形式（旧实现会拼出 /video/170001 这种坏 URL）
    assert get_bilibili_video_url(f"av{aid}") == f"https://www.bilibili.com/video/{bv}"
    # 认不出来时退回原值（保持旧行为，不擅自抛错）
    assert get_bilibili_video_url("") == "https://www.bilibili.com/video/"
    print("OK 8: get_bilibili_video_url 规范化（av 号也能拼出可用 URL）\n")


def test_bv_case_normalize():
    """9. BV 大小写：只归一化前缀，主体必须保持原样（2026-10-03 用户实测带出）。

    用户粘的「稍后再看」URL 里 BV 号在查询串上，query 参数常被各种工具转成小写。
    B 站 BV 号前缀固定大写 BV1，但**主体是 base58、区分大小写**——
    BV1FpLU62EZW 和 BV1FPLU62EZW 是两个不同的视频，整体 upper() 会静默拉错片。
    """
    from video_to_article.providers.bilibili import normalize_bvid

    # 前缀四种大小写组合都要归一化
    for raw in ("bv1FpLU62EZW", "bV1FpLU62EZW", "Bv1FpLU62EZW", "BV1FpLU62EZW"):
        assert resolve_bilibili_bvid(raw) == "BV1FpLU62EZW", (
            f"前缀大小写归一失败: {raw!r} -> {resolve_bilibili_bvid(raw)!r}"
        )
    assert normalize_bvid("bv1FpLU62EZW") == "BV1FpLU62EZW"

    # 关键：主体不能被转大写
    assert resolve_bilibili_bvid("BV1FpLU62EZW") == "BV1FpLU62EZW", "主体被 upper 了"
    assert resolve_bilibili_bvid("BV1FpLU62EZW") != "BV1FPLU62EZW", (
        "整体 upper 会得到另一个视频的号，绝对不能这么做"
    )

    # 稍后再看 URL（原样，含 spm_id_from / vd_source 一堆噪音参数）
    watchlater = (
        "https://www.bilibili.com/list/watchlater?oid=117360182826804"
        "&bvid=BV1a7YF6qEW2&spm_id_from=333.788"
        ".top_right_bar_window_view_later.content.click"
        "&vd_source=013004b341810962cceab210bf4343b7"
    )
    assert resolve_bilibili_bvid(watchlater) == "BV1a7YF6qEW2", (
        "稍后再看 URL 里的 BV（在查询串上）应能取出"
    )
    # 前缀小写版也要能取出并归一
    assert resolve_bilibili_bvid(watchlater.replace("BV1a7YF6qEW2", "bv1a7YF6qEW2")) == (
        "BV1a7YF6qEW2"
    )
    print("OK 9: BV 大小写（只归一化前缀，主体保持原写 + 稍后再看 URL）\n")


def test_url_shapes():
    """10. 常见 B 站 URL 形态都要能识别（防「无法识别」）。"""
    ok_cases = [
        ("https://www.bilibili.com/video/BV1FpLU62EZW", "BV1FpLU62EZW"),
        ("https://www.bilibili.com/video/BV1FpLU62EZW/?p=2&vd_source=abc", "BV1FpLU62EZW"),
        ("https://m.bilibili.com/video/BV1FpLU62EZW", "BV1FpLU62EZW"),
        ("//www.bilibili.com/video/BV1FpLU62EZW", "BV1FpLU62EZW"),
        ("www.bilibili.com/video/BV1FpLU62EZW", "BV1FpLU62EZW"),
        ("https://www.bilibili.com/video/BV1FpLU62EZW/", "BV1FpLU62EZW"),
        ("https://www.bilibili.com/video/av170001?p=1", "BV17x411w7KC"),
        # 分享文案：标题 + 链接 + 引导语混在一段
        ("【这个视频超好看】https://www.bilibili.com/video/BV1FpLU62EZW 记得三连", "BV1FpLU62EZW"),
        # HTML 片段
        ('<a href="https://www.bilibili.com/video/BV1FpLU62EZW">点我</a>', "BV1FpLU62EZW"),
        ("BV1FpLU62EZW", "BV1FpLU62EZW"),
        ("av170001", "BV17x411w7KC"),
    ]
    for text, want in ok_cases:
        got = resolve_bilibili_bvid(text)
        assert got == want, f"{text[:56]!r} -> {got!r}，期望 {want}"

    # 非视频类 URL 不该被误认（宁可空串也别给个错的）
    for text in (
        "https://space.bilibili.com/117360182826804",   # UP 主空间（不是单个视频）
        "https://www.bilibili.com/bangumi/play/ep123456/",  # 番剧单集（另一类内容）
        "https://www.bilibili.com/list/watchlater?oid=117360182826804",  # 列表页无 bvid
        "完全无关的一段文字",
    ):
        assert resolve_bilibili_bvid(text) == "", f"不该识别为视频: {text!r}"
    print(f"OK 10: {len(ok_cases)} 种 URL 形态全识别 + 4 类非视频 URL 不误认\n")


def main():
    test_extract_bvid_strict()
    test_extract_aid_strict()
    test_av_bv_convert()
    test_av_out_of_range()
    test_parse_refs()
    test_resolve_bvid()
    test_resolve_shortlink()
    test_get_video_url()
    test_bv_case_normalize()
    test_url_shapes()
    print("=" * 50)
    print("ALL bilibili-parse smoke tests passed ✓")
    print("=" * 50)


if __name__ == "__main__":
    main()
