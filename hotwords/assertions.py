"""结构断言：解析后必须满足的最低条件。

这是"解析失效告警"的第一道哨兵 —— 比 Scrapling 的 adaptive 更直接有效，
因为我们的源绝大多数是 JSON 接口，失效表现为"字段改名 / 条目变空"，
而不是"DOM 结构漂移"。
"""
from __future__ import annotations

from hotwords.config import AssertionConfig
from hotwords.models import ParseResult


def check(result: ParseResult, cfg: AssertionConfig) -> list[str]:
    """返回违规列表，空列表表示通过。"""
    problems: list[str] = []

    if len(result.items) < cfg.min_items:
        problems.append(f"条目数不足: {len(result.items)} < {cfg.min_items}")

    # 必填字段：以第一条非空 item 的 raw 里是否存在该字段判断
    for field_name in cfg.required_fields:
        if not any(bool(it.raw.get(field_name)) for it in result.items):
            problems.append(f"必填字段缺失或全空: {field_name}")

    # 词条文本不能为空
    empty_words = sum(1 for it in result.items if not it.word.strip())
    if empty_words:
        problems.append(f"空词条数: {empty_words}")

    return problems


def check_any(result: ParseResult, min_items: int, any_fields: list[str]) -> list[str]:
    """宽松断言：any_fields 中至少有一个字段有值即通过。

    用于"同一接口不同榜单字段名不同"的场景（如百度 word/title）。
    """
    problems: list[str] = []

    if len(result.items) < min_items:
        problems.append(f"条目数不足: {len(result.items)} < {min_items}")

    if any_fields:
        hit = any(
            any(bool(it.raw.get(f)) for f in any_fields)
            for it in result.items
        )
        if not hit:
            problems.append(f"候选字段全部缺失: {any_fields}")

    empty_words = sum(1 for it in result.items if not it.word.strip())
    if empty_words:
        problems.append(f"空词条数: {empty_words}")

    return problems
