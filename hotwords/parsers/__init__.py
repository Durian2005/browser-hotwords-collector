"""解析层：Parser 注册表 + 各源 Parser。

职责边界（重要）：
  Parser 只做 "原始 JSON/HTML -> HotItem"，不做网络请求。
  这样换 Fetcher 类型（static -> dynamic -> stealth）不影响解析逻辑。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from hotwords.labels import douyin_badge
from hotwords.models import HotItem, ParseResult
from hotwords.normalize import NORM_VERSION, normalize_hot_value


class Parser(Protocol):
    """Parser 契约。"""

    name: str

    def __call__(self, payload: Any, source: str, category: str) -> ParseResult: ...


_REGISTRY: dict[str, Parser] = {}


def register(name: str) -> Callable[[Parser], Parser]:
    def deco(fn: Parser) -> Parser:
        _REGISTRY[name] = fn
        return fn

    return deco


def get_parser(name: str) -> Parser:
    if name not in _REGISTRY:
        raise KeyError(f"未注册的 parser: {name}（已注册: {sorted(_REGISTRY)}）")
    return _REGISTRY[name]


def list_parsers() -> list[str]:
    return sorted(_REGISTRY)


def _extract_hot(entry: dict) -> tuple[str | None, int | None]:
    """从条目中抽取热度值，兼容多种形态。

    实测（2026-10-09）：
      realtime（热搜）    -> 无 hotScore 字段（百度热搜榜不提供热度值）
      teleplay/novel 等   -> hotScore 是 dict: {"type":0,"value":"95471","desc":"热搜指数"}
      其他源              -> 可能是纯数字 / "1.2亿" 字符串
    """
    raw = entry.get("hotScore")
    if raw is None:
        raw = entry.get("hot_value")

    if isinstance(raw, dict):
        # 百度垂直榜嵌套结构：真正数值在 value 里
        val = raw.get("value") or raw.get("num")
        if val is None:
            return None, None
        return str(val), normalize_hot_value(val)

    if raw is None:
        return None, None
    return str(raw), normalize_hot_value(raw)


# ============================================================
# 百度热搜
# 实测真实结构（2026-10-09）：
#   data.cards[0].content[0].content[]   ← 多套了一层 content！
#
# ⚠️ 坑1：同一接口、同一层级，不同榜单字段名不同！
#     realtime（热搜）      -> word  / url / isTop
#     teleplay/movie/novel  -> title / url / hotScore(dict) / desc / Category
#   → Parser 必须做字段兼容，否则垂直榜全部拿到 0 条
# ⚠️ 坑2：hotScore 是 dict（{"value":"95471"}），不是数字，直接 int() 会丢数据
# ⚠️ 坑3：realtime 榜压根不提供热度值（只有排名语义），hot_value 就该是 None
# ============================================================
@register("baidu")
def parse_baidu(payload: dict, source: str, category: str) -> ParseResult:
    items: list[HotItem] = []
    fields: set[str] = set()

    cards = (payload.get("data") or {}).get("cards") or []
    if not cards:
        return ParseResult(source, category, items, fields)

    # 兼容两种嵌套：cards[0].content 是 list，其 [0] 里还有一层 content
    outer = cards[0].get("content")
    if isinstance(outer, list) and outer and isinstance(outer[0], dict) \
            and isinstance(outer[0].get("content"), list):
        entries = outer[0]["content"]
    elif isinstance(outer, list):
        entries = outer
    else:
        entries = []

    normal_rank = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fields.update(entry.keys())

        # 字段兼容：热搜用 word，垂直榜用 title
        word = (entry.get("word") or entry.get("title") or "").strip()
        if not word:
            continue

        is_top = bool(entry.get("isTop"))
        if is_top:
            rank = 0
            label = "置顶"
        else:
            try:
                rank = int(entry.get("index"))
            except (TypeError, ValueError):
                normal_rank += 1
                rank = normal_rank
            # ⚠️ 实测：realtime 榜除 isTop 外没有任何标签字段（label 恒为 None）；
            #    垂直榜才有 newHotName（新/热）与 labelTagName（热议/辟谣/当事人/直播中）。
            #    Category 字段看似是分类，实测值恒为空字符串，不能用。
            label = (
                entry.get("newHotName")
                or entry.get("labelTagName")
                or entry.get("label")
                or None
            )

        hot_value, hot_num = _extract_hot(entry)
        items.append(
            HotItem(
                source=source,
                category=category,
                rank=rank,
                word=word,
                hot_value=hot_value,
                hot_value_num=hot_num,
                norm_version=NORM_VERSION,
                label=label,
                url=entry.get("url") or entry.get("rawUrl"),
                is_top=is_top,
                raw=entry,
            )
        )

    return ParseResult(source, category, items, fields)


# ============================================================
# B站热搜
# 结构：data.trending.list[] -> {keyword, show_name, heat_score, ...}
# ============================================================
@register("bilibili")
def parse_bilibili(payload: dict, source: str, category: str) -> ParseResult:
    items: list[HotItem] = []
    fields: set[str] = set()

    data = payload.get("data") or {}
    listing = ((data.get("trending") or {}).get("list")) or []
    for idx, entry in enumerate(listing, start=1):
        if not isinstance(entry, dict):
            continue
        fields.update(entry.keys())
        word = (entry.get("show_name") or entry.get("keyword") or "").strip()
        if not word:
            continue
        heat = entry.get("heat_score")
        items.append(
            HotItem(
                source=source,
                category=category,
                rank=idx,
                word=word,
                hot_value=str(heat) if heat is not None else None,
                hot_value_num=normalize_hot_value(heat),
                norm_version=NORM_VERSION,
                label=None,
                # B站接口常不给 uri，自行拼搜索页
                url=f"https://search.bilibili.com/all?keyword={word}",
                raw=entry,
            )
        )
    return ParseResult(source, category, items, fields)

# ============================================================
# 微博热搜
# 结构：data.realtime[] -> {word, num, label_name/highlight, ...}
# 注意：num 可能是 "1.2亿" 这类字符串
# ============================================================
@register("weibo")
def parse_weibo(payload: dict, source: str, category: str) -> ParseResult:
    items: list[HotItem] = []
    fields: set[str] = set()

    data = payload.get("data") or {}
    for idx, entry in enumerate(data.get("realtime") or [], start=1):
        if not isinstance(entry, dict):
            continue
        fields.update(entry.keys())
        word = (entry.get("word") or "").strip()
        if not word:
            continue
        num = entry.get("num") or entry.get("raw_hot")
        # ⚠️ 广告位识别（实测 2026-10-11，全库 2204 条中 54 条命中）：
        #   is_ad=1 的条目清一色是品牌代言 / 上市推广
        #   （"迪丽热巴阿道夫品牌全球代言人"、"谢霆锋同款银河战舰700全球上市"），
        #   且这些条目同时【缺失 realpos】—— 即接口不给"榜内真实位置"，
        #   本身就是广告位的旁证。topic_ad（话题推广）实测是 is_ad 的子集。
        is_ad = bool(entry.get("is_ad") or entry.get("topic_ad"))
        items.append(
            HotItem(
                source=source,
                category=category,
                rank=idx,
                word=word,
                hot_value=str(num) if num is not None else None,
                hot_value_num=normalize_hot_value(num),
                norm_version=NORM_VERSION,
                label=(entry.get("label_name") or None),
                url=f"https://s.weibo.com/weibo?q=%23{word}%23",
                is_ad=is_ad,
                topic_category=entry.get("flag_desc") or None,  # 综艺/剧集/盛典/演出
                raw=entry,
            )
        )
    return ParseResult(source, category, items, fields)


# ============================================================
# 抖音热榜
# 结构：data.word_list[] -> {word, hot_value, label, word_sub_board, ...}
#
# ⚠️ label 是【平台内部数字枚举】，官方从未公开语义。
#    早期实现只映射了有把握的 1->新、3->热，其余宁可留空（当时的立场是
#    "猜错会把真实热点标成广告，比不标更糟"）。这个立场是对的，
#    但结论在 2026-10-11 被推翻 —— 因为找到了不需要猜的证据：
#    每条带 label 的条目都附了徽标图 URL，文件名就是 label 值本身，
#        .../hotspot_detail_page/<label>.png
#    下载放大后肉眼确认，8 个值全部可读（见 labels.py 与 README「坑 21」）。
#    → 现在全量映射，且不再有任何猜测成分。
#
# ⚠️ word_sub_board 只出现过 None 与 [3] 两种情况，且 [3] 与 label=9(挑战)
#    几乎完全重合（745 vs 753 条），同一词条从不在这两者间切换 ——
#    说明「挑战」是独立子榜，不是主榜条目。是否参与持续度统计交给配置决定。
# ============================================================


@register("douyin")
def parse_douyin(payload: dict, source: str, category: str) -> ParseResult:
    items: list[HotItem] = []
    fields: set[str] = set()

    data = payload.get("data") or {}
    for idx, entry in enumerate(data.get("word_list") or [], start=1):
        if not isinstance(entry, dict):
            continue
        fields.update(entry.keys())
        word = (entry.get("word") or "").strip()
        if not word:
            continue
        heat = entry.get("hot_value")
        raw_label = entry.get("label")
        try:
            label_id = int(raw_label)
        except (TypeError, ValueError):
            label_id = None
        sub = entry.get("word_sub_board")
        items.append(
            HotItem(
                source=source,
                category=category,
                rank=idx,
                word=word,
                hot_value=str(heat) if heat is not None else None,
                hot_value_num=normalize_hot_value(heat),
                norm_version=NORM_VERSION,
                label=douyin_badge(raw_label),
                label_id=label_id,
                sub_board=(json.dumps(sub, separators=(",", ":")) if sub else None),
                url=f"https://www.douyin.com/search/{word}",
                raw=entry,
            )
        )
    return ParseResult(source, category, items, fields)


# ============================================================
# 头条热榜
# 实测结构（2026-10-09）：
#   data[]            -> {Title, Url, HotValue, Label, LabelDesc, ClusterIdStr}
#   fixed_top_data    -> 置顶条目（与 data 同结构，单独一个字段）
# 坑1：字段名首字母大写（Title/HotValue/Url），不是小写！
# 坑2：Label 是英文枚举（new/hot/boom），需映射中文
# 坑3：置顶在 fixed_top_data 里，不在 data[0]
# ============================================================
_TOUTIAO_LABEL = {
    "new": "新",
    "hot": "热",
    "boom": "爆",
    "top": "置顶",
    "recommend": "荐",
    # 补全实测出现的枚举（原实现只有上面 5 个，其余会原样吐出英文，
    # 如 "refuteRumors" 直接进看板）
    "refuterumors": "辟谣",
    "depth": "深度",
    "onsite": "现场",
    "recentprogress": "新进展",
}

# 内容分类：头条给的是英文枚举数组，如 ["finance"]，取首项。
_INTEREST_CN = {
    "finance": "财经",
    "sports": "体育",
    "entertainment": "娱乐",
    "international": "国际",
    "education": "教育",
    "health": "健康",
    "military": "军事",
    "car": "汽车",
    "technology": "科技",
    "culture": "文化",
    "society": "社会",
    "taiwan": "中国台湾",
}


def _interest_of(entry: dict) -> str | None:
    cats = entry.get("InterestCategory")
    if isinstance(cats, list) and cats:
        first = str(cats[0]).lower()
        return _INTEREST_CN.get(first, str(cats[0]))
    return None


@register("toutiao")
def parse_toutiao(payload: dict, source: str, category: str) -> ParseResult:
    items: list[HotItem] = []
    fields: set[str] = set()

    def _label_of(entry: dict) -> str | None:
        # LabelDesc 是现成的中文描述（新事件上榜/热门事件/辟谣/深度/现场/新进展），
        # 比英文 Label 映射更准，优先用它；缺失时才回退到枚举映射。
        desc = entry.get("LabelDesc")
        if desc:
            return str(desc)
        raw_label = entry.get("Label")
        if not raw_label:
            return None
        return _TOUTIAO_LABEL.get(str(raw_label).lower(), str(raw_label))

    # 置顶条目
    top = payload.get("fixed_top_data")
    if isinstance(top, dict) and (top.get("Title") or top.get("title")):
        fields.update(top.keys())
        items.append(
            HotItem(
                source=source,
                category=category,
                rank=0,
                word=(top.get("Title") or top.get("title") or "").strip(),
                hot_value=None,
                hot_value_num=None,
                norm_version=NORM_VERSION,
                label="置顶",
                url=top.get("Url") or top.get("url"),
                is_top=True,
                topic_category=_interest_of(top),
                raw=top,
            )
        )

    for idx, entry in enumerate(payload.get("data") or [], start=1):
        if not isinstance(entry, dict):
            continue
        fields.update(entry.keys())
        word = (entry.get("Title") or entry.get("title") or "").strip()
        if not word:
            continue
        heat = entry.get("HotValue") or entry.get("hot_value")
        items.append(
            HotItem(
                source=source,
                category=category,
                rank=idx,
                word=word,
                hot_value=str(heat) if heat is not None else None,
                hot_value_num=normalize_hot_value(heat),
                norm_version=NORM_VERSION,
                label=_label_of(entry),
                url=entry.get("Url") or entry.get("url"),
                topic_category=_interest_of(entry),
                raw=entry,
            )
        )
    return ParseResult(source, category, items, fields)


# ============================================================
# 知乎热榜（V1 占位；接口 401，需登录态，暂不启用）
# ============================================================
@register("zhihu")
def parse_zhihu(payload: dict, source: str, category: str) -> ParseResult:
    items: list[HotItem] = []
    fields: set[str] = set()

    for idx, entry in enumerate(payload.get("data") or [], start=1):
        if not isinstance(entry, dict):
            continue
        target = entry.get("target") or {}
        fields.update(target.keys())
        word = (target.get("title") or "").strip()
        if not word:
            continue
        items.append(
            HotItem(
                source=source,
                category=category,
                rank=idx,
                word=word,
                hot_value=None,
                hot_value_num=None,
                norm_version=NORM_VERSION,
                label=None,
                url=target.get("url"),
                raw=entry,
            )
        )
    return ParseResult(source, category, items, fields)
