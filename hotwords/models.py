"""数据模型：采集层与存储层之间的契约。

Collector 拿到原始数据 -> Parser 产出 HotItem -> Repository 落库。
三者只依赖这里的数据结构，互不耦合。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def utcnow_iso() -> str:
    """统一时间格式（带时区），避免本地时区歧义。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(slots=True)
class HotItem:
    """一条结构化热词。Parser 的唯一产出物。"""

    source: str                 # baidu_hot
    category: str               # realtime / teleplay / main ...
    rank: int                   # 排名（1 起）
    word: str                   # 词条原文
    hot_value: str | None = None       # 热度原始值（保留字符串！）
    hot_value_num: int | None = None   # 归一化数值
    norm_version: str | None = None    # 归一化规则版本
    label: str | None = None           # 热/沸/新/爆
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # 原始条目

    def raw_json(self) -> str:
        return json.dumps(self.raw, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class ParseResult:
    """解析结果：既带数据，也带"结构自检"结论。"""

    source: str
    category: str
    items: list[HotItem]
    field_names: set[str] = field(default_factory=set)  # 实际出现的字段集合

    @property
    def ok(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "category": self.category,
            "count": len(self.items),
            "field_names": sorted(self.field_names),
            "items": [asdict(i) for i in self.items],
        }
