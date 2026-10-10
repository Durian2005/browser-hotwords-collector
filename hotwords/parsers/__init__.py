"""解析层：Parser 注册表 + 各源 Parser。

职责边界（重要）：
  Parser 只做 "原始 JSON/HTML -> HotItem"，不做网络请求。
  这样换 Fetcher 类型（static -> dynamic -> stealth）不影响解析逻辑。
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

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
            label = entry.get("label") or entry.get("Category") or None

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
                raw=entry,
            )
        )
    return ParseResult(source, category, items, fields)


# ============================================================
# 抖音热榜
# 结构：data.word_list[] -> {word, hot_value, label, ...}
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
}


@register("toutiao")
def parse_toutiao(payload: dict, source: str, category: str) -> ParseResult:
    items: list[HotItem] = []
    fields: set[str] = set()

    def _label_of(entry: dict) -> str | None:
        raw_label = entry.get("Label")
        if not raw_label:
            return entry.get("LabelDesc") or None
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
