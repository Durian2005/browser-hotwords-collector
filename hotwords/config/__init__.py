"""配置加载：YAML -> SourceConfig 对象。

新增源 = 改 sources.yaml + 注册 parser，不改主流程。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).with_name("sources.yaml")


@dataclass(slots=True)
class RetryConfig:
    max_attempts: int = 3
    backoff_base: float = 1.0
    retry_on_status: list[int] = field(default_factory=lambda: [429, 500, 502, 503, 504])


@dataclass(slots=True)
class AssertionConfig:
    min_items: int = 1
    required_fields: list[str] = field(default_factory=lambda: ["word"])
    # 多选一：至少一个字段有值即通过（应对同接口不同榜单字段名不同）
    required_fields_any: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceConfig:
    id: str
    name: str
    enabled: bool
    type: str
    interval_min: int
    parser: str
    request: dict[str, Any]
    assertions: AssertionConfig
    robots_checked: str | None = None
    robots_note: str | None = None
    # 不参与「持续上榜」统计的榜单（tab 名）。空 = 该源全部参与。
    #
    # 为什么需要：百度 top.baidu.com/api/board 的 movie / novel / teleplay 三个
    # 垂直榜是【运营推广位】，不是搜索热度 —— 实测 42 轮里 #1 纹丝不动
    # （功夫女足 / 绍宋 / 九门），而 realtime 榜 #1 只保持 20/39 轮。
    # 若不排除，这些"钉子"会因持续度算法里的「连续轮数」「最佳名次」两项
    # 天然拿满分，直接霸占持续上榜榜的前十几位，把真实跨源热点挤出榜单。
    track_exclude: tuple[str, ...] = ()
    # 不参与「持续上榜」统计的徽标（按 label_id，抖音专有）。
    #
    # 与 track_exclude 的分工：那个是【榜单级】（整张 tab 都排除），
    # 这个是【条目级】—— 同一个榜单里，带某个徽标的条目单独排除。
    #
    # 为什么需要：抖音「挑战」话题（label=9）与 word_sub_board=[3] 绑定，
    # 是平台官方活动话题，实测会连续 66~79 轮驻留在榜上不动；
    # 百度垂类榜那种"整榜排除"用不上（挑战条目和真实热搜混在同一张榜里），
    # 所以必须支持条目级。
    #
    # 按数字 id 而不是中文名来配：中文名是我们自己定的映射，
    # 哪天改个措辞就会让这里的规则静默失效。id 是平台给的，稳定。
    track_exclude_labels: tuple[int, ...] = ()

    @property
    def tabs(self) -> list[str]:
        return self.request.get("tabs") or ["main"]

    def tracked_tabs(self) -> list[str]:
        """参与持续上榜统计的 tab（排除运营位）。"""
        return [t for t in self.tabs if t not in self.track_exclude]


@dataclass(slots=True)
class AppConfig:
    defaults: dict[str, Any]
    sources: list[SourceConfig]

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    def track_excluded(self) -> set[tuple[str, str]]:
        """返回 {(source_id, tab)} 集合，表示这些榜单不参与持续上榜统计。"""
        out: set[tuple[str, str]] = set()
        for s in self.sources:
            for tab in s.track_exclude:
                out.add((s.id, tab))
        return out

    def track_excluded_labels(self) -> set[tuple[str, int]]:
        """返回 {(source_id, label_id)} 集合：这些徽标的条目不参与持续上榜统计。

        条目级排除（榜单级见 track_excluded）。
        """
        out: set[tuple[str, int]] = set()
        for s in self.sources:
            for lab in s.track_exclude_labels:
                out.add((s.id, int(lab)))
        return out


def load_config(path: str | Path = CONFIG_PATH) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    sources: list[SourceConfig] = []
    for s in raw.get("sources", []):
        assert_cfg = AssertionConfig(**(s.get("assertions") or {}))
        sources.append(
            SourceConfig(
                id=s["id"],
                name=s.get("name", s["id"]),
                enabled=bool(s.get("enabled", True)),
                type=s.get("type", "static_json"),
                interval_min=int(s.get("interval_min", 10)),
                parser=s["parser"],
                request=s.get("request", {}),
                assertions=assert_cfg,
                robots_checked=s.get("robots_checked"),
                robots_note=s.get("robots_note"),
                track_exclude=tuple(s.get("track_exclude") or ()),
                track_exclude_labels=tuple(
                    int(x) for x in (s.get("track_exclude_labels") or ())
                ),
            )
        )
    return AppConfig(defaults=raw.get("defaults", {}), sources=sources)
