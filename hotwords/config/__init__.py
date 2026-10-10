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

    @property
    def tabs(self) -> list[str]:
        return self.request.get("tabs") or ["main"]


@dataclass(slots=True)
class AppConfig:
    defaults: dict[str, Any]
    sources: list[SourceConfig]

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


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
            )
        )
    return AppConfig(defaults=raw.get("defaults", {}), sources=sources)
