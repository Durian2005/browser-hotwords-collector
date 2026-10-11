"""主流水线：调度 -> 抓取 -> 解析 -> 断言 -> 落库 -> 告警。

单次执行、单源串行，刻意不做并发（克制 > 对抗）。
"""
from __future__ import annotations

import json
import logging

from hotwords import assertions
from hotwords.collector import BaseFetcher, FetchError, fetch_with_retry
from hotwords.config import AppConfig, SourceConfig, load_config
from hotwords.parsers import get_parser
from hotwords.storage import Repository

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        cfg: AppConfig,
        repo: Repository,
        fetcher: BaseFetcher,
        notifier=None,
    ):
        self.cfg = cfg
        self.repo = repo
        self.fetcher = fetcher
        self.notifier = notifier  # 可调用对象(kind, source, detail) -> None
        self.retry_cfg = _RetryShim(cfg.defaults.get("retry", {}))
        self.timeout = int(cfg.defaults.get("timeout", 15))

    # ---------------- 单源 ----------------
    def run_source(self, src: SourceConfig, run_id: str) -> tuple[int, list[str]]:
        parser = get_parser(src.parser)
        written = 0
        problems_all: list[str] = []

        for category in src.tabs:
            url = src.request.get("url", "").format(tab=category)
            headers = dict(src.request.get("headers") or {})
            headers.setdefault("User-Agent", self.cfg.defaults.get("user_agent", ""))

            try:
                status, body = fetch_with_retry(
                    self.fetcher, url, headers, self.retry_cfg, self.timeout
                )
            except FetchError as e:
                detail = f"[{src.id}/{category}] 抓取失败: {e} (status={e.status})"
                logger.error(detail)
                self.repo.add_alert("http_error", src.id, detail, run_id)
                self._notify("http_error", src.id, detail)
                problems_all.append(detail)
                continue

            try:
                payload = json.loads(body)
            except json.JSONDecodeError as e:
                detail = f"[{src.id}/{category}] JSON 解析失败: {e}"
                logger.error(detail)
                self.repo.add_alert("parse_fail", src.id, detail, run_id)
                self._notify("parse_fail", src.id, detail)
                problems_all.append(detail)
                continue

            result = parser(payload, src.id, category)

            # 断言哨兵
            if src.assertions.required_fields_any:
                problems = assertions.check_any(
                    result, src.assertions.min_items,
                    src.assertions.required_fields_any,
                )
            else:
                problems = assertions.check(result, src.assertions)
            if problems:
                detail = f"[{src.id}/{category}] 结构断言失败: " + "; ".join(problems)
                logger.error(detail)
                self.repo.add_alert("parse_fail", src.id, detail, run_id)
                self._notify("parse_fail", src.id, detail)
                problems_all.append(detail)
                # 断言失败仍然落库（可能是降级数据），便于事后 rescue
            else:
                # 字段漂移检测（仅断言通过时才更新指纹，避免污染基线）
                drift = self.repo.check_drift(result)
                if drift:
                    d = f"[{src.id}/{category}] 字段漂移: {drift}"
                    logger.warning(d)
                    self.repo.add_alert("schema_drift", src.id, d, run_id)
                    self._notify("schema_drift", src.id, d)

            n = self.repo.save_snapshot(run_id, result)
            self.repo.upsert_words(result.items)
            written += n
            logger.info("[%s/%s] 写入 %d 条 (%s)", src.id, category, n, self.fetcher.name)

        return written, problems_all

    # ---------------- 一轮全量 ----------------
    def run_once(self) -> dict:
        run_id = self.repo.start_run()
        stats: dict[str, int] = {}
        all_problems: list[str] = []

        for src in self.cfg.enabled_sources():
            if src.type != "static_json":
                logger.info("跳过 %s（type=%s，非 V1 范围）", src.id, src.type)
                continue
            written, problems = self.run_source(src, run_id)
            stats[src.id] = written
            all_problems.extend(problems)

        status = "ok" if not all_problems else ("partial" if any(stats.values()) else "failed")
        self.repo.finish_run(run_id, status, stats)

        # 每轮结束后增量更新"持续上榜"追踪
        if status in ("ok", "partial"):
            from hotwords.analysis import track_persistence

            try:
                n = track_persistence(
                    self.repo, run_id, exclude=self.cfg.track_excluded()
                )
                logger.info("持续上榜追踪更新: %d 个词条", n)
            except Exception as e:  # 追踪失败不能影响采集主流程
                logger.warning("持续上榜追踪失败: %s", e)

        logger.info("run %s 完成: status=%s stats=%s", run_id, status, stats)
        return {"run_id": run_id, "status": status, "stats": stats, "problems": all_problems}

    def _notify(self, kind: str, source: str, detail: str) -> None:
        if self.notifier:
            try:
                self.notifier(kind, source, detail)
            except Exception as e:  # 通知失败不能影响主流程
                logger.warning("通知失败: %s", e)


class _RetryShim:
    """把 config 里的 dict 包成有属性的对象，给 fetch_with_retry 用。"""

    def __init__(self, d: dict):
        self.max_attempts = int(d.get("max_attempts", 3))
        self.backoff_base = float(d.get("backoff_base", 1.0))
        self.retry_on_status = list(d.get("retry_on_status", [429, 500, 502, 503, 504]))


def build_pipeline(db_path: str = "hotwords.db", config_path=None, notifier=None) -> Pipeline:
    from hotwords.collector import build_fetcher

    cfg = load_config(config_path) if config_path else load_config()
    repo = Repository(db_path)
    fetcher = build_fetcher()
    logger.info("使用 Fetcher: %s", fetcher.name)
    return Pipeline(cfg, repo, fetcher, notifier)
