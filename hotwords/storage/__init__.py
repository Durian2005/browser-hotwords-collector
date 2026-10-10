"""存储层：SQLite（WAL）。

契约：
  - snapshot 只 INSERT（全量留档），绝不 UPDATE 覆盖 —— 趋势分析的基础
  - word 表做词条唯一性聚合（去重只在这一层）
  - alert / parser_fingerprint 支撑"解析失效告警"
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Iterable, Sequence

from hotwords.models import HotItem, ParseResult, utcnow_iso
from hotwords.normalize import normalize_word

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Repository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    # ---------------- run ----------------
    def start_run(self) -> str:
        run_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO run(run_id, started_at, status) VALUES (?,?,?)",
            (run_id, utcnow_iso(), "running"),
        )
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str, stats: dict) -> None:
        import json

        self.conn.execute(
            "UPDATE run SET finished_at=?, status=?, stats_json=? WHERE run_id=?",
            (utcnow_iso(), status, json.dumps(stats, ensure_ascii=False), run_id),
        )
        self.conn.commit()

    # ---------------- snapshot ----------------
    def save_snapshot(self, run_id: str, result: ParseResult) -> int:
        """全量追加。返回写入行数。"""
        if not result.items:
            return 0
        fetched_at = utcnow_iso()
        rows = [
            (
                run_id,
                it.source,
                it.category,
                it.rank,
                it.word,
                it.hot_value,
                it.hot_value_num,
                it.norm_version,
                it.label,
                it.url,
                it.raw_json(),
                fetched_at,
            )
            for it in result.items
        ]
        cur = self.conn.executemany(
            """INSERT OR IGNORE INTO snapshot
               (run_id, source, category, rank, word, hot_value, hot_value_num,
                norm_version, label, url, raw, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    # ---------------- word 聚合 ----------------
    def upsert_words(self, items: Iterable[HotItem]) -> None:
        now = utcnow_iso()
        for it in items:
            key = normalize_word(it.word)
            if not key:
                continue
            word_hash = hashlib.sha1(key.encode("utf-8")).hexdigest()
            row = self.conn.execute(
                "SELECT sources, cross_source_count FROM word WHERE word_hash=?",
                (word_hash,),
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """INSERT INTO word
                       (word_hash, canonical_word, first_seen, last_seen,
                        appear_count, sources, cross_source_count)
                       VALUES (?,?,?,?,?,?,?)""",
                    (word_hash, it.word, now, now, 1, it.source, 1),
                )
            else:
                srcs = set((row["sources"] or "").split(",")) - {""}
                srcs.add(it.source)
                self.conn.execute(
                    """UPDATE word
                       SET last_seen=?, appear_count=appear_count+1,
                           sources=?, cross_source_count=?
                       WHERE word_hash=?""",
                    (now, ",".join(sorted(srcs)), len(srcs), word_hash),
                )
        self.conn.commit()

    # ---------------- 告警 ----------------
    def add_alert(self, kind: str, source: str | None, detail: str,
                  run_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO alert(run_id, source, kind, detail, created_at) "
            "VALUES (?,?,?,?,?)",
            (run_id, source, kind, detail, utcnow_iso()),
        )
        self.conn.commit()

    # ---------------- 指纹 / 漂移检测 ----------------
    def check_drift(self, result: ParseResult) -> str | None:
        """对比字段集合与上次成功解析，返回漂移描述或 None。

        按 source+category 维度记录 —— 同一源的不同榜单（百度 realtime
        用 word、垂直榜用 title）字段本就不同，不能混在一个指纹里比较。
        """
        key = f"{result.source}/{result.category}"
        fields = ",".join(sorted(result.field_names))
        row = self.conn.execute(
            "SELECT field_names FROM parser_fingerprint WHERE source=?",
            (key,),
        ).fetchone()
        prev = row["field_names"] if row else None
        self.conn.execute(
            """INSERT INTO parser_fingerprint(source, field_names, item_count, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                 field_names=excluded.field_names,
                 item_count=excluded.item_count,
                 updated_at=excluded.updated_at""",
            (key, fields, len(result.items), utcnow_iso()),
        )
        self.conn.commit()
        if prev and prev != fields:
            return f"字段集合变化: [{prev}] -> [{fields}]"
        return None

    # ---------------- 查询 ----------------
    def recent_alerts(self, limit: int = 20) -> Sequence[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM alert ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
