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
    # 后加的列：`CREATE TABLE IF NOT EXISTS` 不会给【已存在】的表补列，
    # 所以老库（本项目线上那个已有 1.2 万条快照）必须显式 ALTER。
    # 不引入 migration 框架 —— SQLite 的 ADD COLUMN 是常数级操作，
    # 且新列都带默认值，历史行自动取默认值，够用即可。
    _ADDED_COLUMNS = {
        "snapshot": [
            ("is_ad", "INTEGER NOT NULL DEFAULT 0"),
            ("is_top", "INTEGER NOT NULL DEFAULT 0"),
            ("topic_category", "TEXT"),
            ("label_id", "INTEGER"),
            ("sub_board", "TEXT"),
        ],
        "word_track": [
            ("is_ad", "INTEGER NOT NULL DEFAULT 0"),
            ("is_top", "INTEGER NOT NULL DEFAULT 0"),
            ("labels", "TEXT"),
        ],
    }

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False 是必需的，不是图省事：
        # APScheduler 在线程池里执行 job，而连接是在主线程创建的，
        # 默认设置下第一轮就会抛 ProgrammingError 且被吞成"静默失败"。
        # 安全性依据：调度器为单 job（max_instances=1）串行执行，
        # 同一时刻只有一个线程在用这个连接，不存在并发写。
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """给已存在的老表补齐后加的列（幂等）。"""
        for table, cols in self._ADDED_COLUMNS.items():
            existing = {
                r["name"]
                for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, ddl in cols:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
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
                it.is_ad,
                int(it.is_top),
                it.topic_category,
                it.label_id,
                it.sub_board,
                it.raw_json(),
                fetched_at,
            )
            for it in result.items
        ]
        cur = self.conn.executemany(
            """INSERT OR IGNORE INTO snapshot
               (run_id, source, category, rank, word, hot_value, hot_value_num,
                norm_version, label, url, is_ad, is_top, topic_category,
                label_id, sub_board, raw, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
    def check_drift(
        self,
        result: ParseResult,
        must_have: Iterable[str] | None = None,
        any_of: Iterable[str] | None = None,
    ) -> str | None:
        """检测"解析结构是否失效"，返回问题描述或 None。

        只有两类情况算漂移：
          1) 关键字段（产出 item 所依赖的字段）缺失；
          2) 字段集合大面积换血（旧字段消失过半）—— 兜底，防止
             "关键字段恰好没变、但整个结构已经换了"这种漏网。

        ⚠️ 为什么不比较"字段集合是否完全相等"：
        各源都有**可选字段** —— 有则出现、无则缺席。实测（全库 12089 条）：
          weibo  is_ad 只出现在 2.5% 的条目、icon* 56%、flag_desc 18%
          douyin label_url 63%、post_aweme_info 12%、word_sub_board 13%
          toutiao InterestCategory 80%、LabelDesc 36%
          baidu  newHotTag 14%、labelTag 5%
        这些字段让**每一轮的字段集合都不一样**，于是每轮都报漂移，
        刷屏到真正的漂移反而看不见 —— 典型的"狼来了"。
        按 source/category 拆指纹只解决了"不同榜单字段不同"（坑 6），
        解决不了"同一榜单字段时有时无"（本坑）。

        按 source+category 维度记录：同一源的不同榜单（百度 realtime
        用 word、垂直榜用 title）字段本就不同，不能混在一个指纹里比较。
        """
        key = f"{result.source}/{result.category}"
        cur_fields = set(result.field_names)
        fields = ",".join(sorted(cur_fields))

        row = self.conn.execute(
            "SELECT field_names FROM parser_fingerprint WHERE source=?",
            (key,),
        ).fetchone()
        prev_set = set((row["field_names"] or "").split(",")) - {""} if row else set()

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

        problems: list[str] = []

        missing = sorted(set(must_have or ()) - cur_fields)
        if missing:
            problems.append(f"关键字段缺失: {missing}")

        # "多选一"的关键字段（如百度 word/title 看榜单而定）：一个都没有才算失效
        cand = set(any_of or ())
        if cand and not (cand & cur_fields):
            problems.append(f"候选关键字段全部缺失: {sorted(cand)}")

        if prev_set:
            gone = prev_set - cur_fields
            if len(gone) / len(prev_set) > 0.5:
                problems.append(
                    f"字段集合大面积变化: 消失 {len(gone)}/{len(prev_set)} 个，"
                    f"如 {sorted(gone)[:6]}"
                )

        return "; ".join(problems) if problems else None

    # ---------------- 查询 ----------------
    def recent_alerts(self, limit: int = 20) -> Sequence[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM alert ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
