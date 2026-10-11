"""跨源共现度分析 —— "高热度"的真信号。

为什么需要它：
  单看某个源的热度值没有可比性 —— 抖音 1181 万、B站 520 万、头条 4746 万，
  量纲完全不同，直接相加毫无意义。
  真正说明"这个词真的火"的信号是：**它在多少个独立平台同时上榜**。

算法（三层）：
  1) 源内归一化：把各源热度映射到 0..100 的相对分位
     score_in_source = 100 * (1 - (rank-1)/n)      # 排名越前越高
     （不用热度值，因为量纲不可比；排名语义在各源是一致的）
     若该源有热度值，做二次加权：与热度分位取平均，抗排名抖动。
  2) 跨源加权：cross_source_count 越多，权重越高
     weight = 1 + log2(cross_source_count)           # 1源=1.0, 2源=2.0, 4源=3.0
  3) 综合热度指数 heat_index = Σ(源内分 * 源权重) / 源总数 * weight

产出：
  - 跨源共现榜（cross_source_count >= 2 的词条）
  - 每词条在哪些源、各自排名、源内分
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceHit:
    source: str
    category: str
    rank: int
    hot_value_num: int | None
    in_source_score: float


@dataclass
class CrossSourceWord:
    word: str
    word_hash: str
    sources: list[str] = field(default_factory=list)
    hits: list[SourceHit] = field(default_factory=list)
    cross_source_count: int = 0
    heat_index: float = 0.0
    best_rank: int = 999
    # 位置/性质标记（累积：任一源、任一轮被标记即置位）
    is_ad: bool = False
    labels: list[str] = field(default_factory=list)            # 平台展示标签
    topic_categories: list[str] = field(default_factory=list)  # 内容分类

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "sources": self.sources,
            "cross_source_count": self.cross_source_count,
            "heat_index": round(self.heat_index, 2),
            "best_rank": self.best_rank,
            "is_ad": self.is_ad,
            "labels": self.labels,
            "topic_categories": self.topic_categories,
            "hits": [
                {
                    "source": h.source,
                    "category": h.category,
                    "rank": h.rank,
                    "hot_value_num": h.hot_value_num,
                    "in_source_score": round(h.in_source_score, 2),
                }
                for h in self.hits
            ],
        }


def _in_source_score(hit: SourceHit, source_total: int) -> float:
    """源内归一化：排名分位 + 热度分位（若有）取平均，均映射到 0..100。

    用排名而非热度绝对值，因为各源量纲不可比，但"排第几"是可比的语义。
    """
    total = max(source_total, 1)
    rank = hit.rank if hit.rank and hit.rank > 0 else total  # rank=0 是置顶，按末位算
    rank_score = 100.0 * (1.0 - (rank - 1) / total)

    if hit.hot_value_num is None:
        return rank_score
    # 热度分位：需要同源热度分布，这里用对数压缩做近似（避免极值主导）
    heat_score = 100.0 * (1.0 - 1.0 / (1.0 + math.log1p(max(hit.hot_value_num, 0)) / 10.0))
    return 0.6 * rank_score + 0.4 * heat_score


def compute_cross_source(rows: list[dict]) -> list[CrossSourceWord]:
    """输入：每行 {source, category, rank, word, hot_value_num}（同一轮快照）。

    输出：按 heat_index 降序的跨源词条列表。
    """
    from hotwords.normalize import normalize_word
    import hashlib

    # 1) 统计每个 (source, category) 的条目总数，用于源内归一化
    src_total: dict[str, int] = {}
    for r in rows:
        key = f"{r['source']}/{r.get('category', 'main')}"
        src_total[key] = src_total.get(key, 0) + 1

    # 2) 按词条聚合
    buckets: dict[str, CrossSourceWord] = {}
    for r in rows:
        word = (r.get("word") or "").strip()
        if not word or r.get("rank") == 0:      # 跳过置顶
            continue
        key = normalize_word(word)
        if not key:
            continue
        # 同一词条在同一源只取最好名次（百度 realtime 与垂直榜可能重复出现）
        bucket_key = key
        bucket = buckets.get(bucket_key)
        if bucket is None:
            bucket = CrossSourceWord(word=word, word_hash=hashlib.sha1(key.encode()).hexdigest())
            buckets[bucket_key] = bucket

        # 标记累积：这些字段不参与打分，只用于看板标注（广告位/标签/分类）
        if r.get("is_ad"):
            bucket.is_ad = True
        lb = r.get("label")
        if lb and lb not in bucket.labels:
            bucket.labels.append(lb)
        tc = r.get("topic_category")
        if tc and tc not in bucket.topic_categories:
            bucket.topic_categories.append(tc)

        src_key = f"{r['source']}/{r.get('category', 'main')}"
        hit = SourceHit(
            source=r["source"],
            category=r.get("category", "main"),
            rank=int(r.get("rank") or 0),
            hot_value_num=r.get("hot_value_num"),
            in_source_score=0.0,
        )
        hit.in_source_score = _in_source_score(hit, src_total.get(src_key, 1))

        # 同源去重：保留源内分更高的那条
        existing = next((h for h in bucket.hits if h.source == hit.source), None)
        if existing is None:
            bucket.hits.append(hit)
        elif hit.in_source_score > existing.in_source_score:
            bucket.hits.remove(existing)
            bucket.hits.append(hit)

    # 3) 计算跨源权重与综合热度指数
    result: list[CrossSourceWord] = []
    for bucket in buckets.values():
        srcs = sorted({h.source for h in bucket.hits})
        bucket.sources = srcs
        bucket.cross_source_count = len(srcs)
        bucket.best_rank = min((h.rank for h in bucket.hits if h.rank > 0), default=999)

        weight = 1.0 + math.log2(len(srcs)) if len(srcs) > 1 else 1.0
        avg_score = sum(h.in_source_score for h in bucket.hits) / len(bucket.hits)
        bucket.heat_index = avg_score * weight
        result.append(bucket)

    result.sort(key=lambda w: (-w.heat_index, w.best_rank))
    return result


def load_recent_snapshot(repo, run_id: str | None = None) -> list[dict]:
    """从 snapshot 读取一轮快照（默认最新一轮）用于共现分析。"""
    if run_id is None:
        row = repo.conn.execute(
            "SELECT run_id FROM run WHERE status IN ('ok','partial') "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        run_id = row["run_id"]
    cur = repo.conn.execute(
        "SELECT source, category, rank, word, hot_value_num, label, "
        "       is_ad, is_top, topic_category "
        "FROM snapshot WHERE run_id=?",
        (run_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def load_snapshot_over_runs(repo, last_n: int = 10) -> list[dict]:
    """跨多轮聚合：用于"持续上榜"分析（比单轮更稳）。

    last_n <= 0 表示【全部轮次】（长跑之后默认只看最近 N 轮会漏掉大量词条，
    导致看板"累计词条"数远小于实际，容易误读）。
    """
    run_ids = _recent_run_ids(repo, last_n)
    if not run_ids:
        return []
    marks = ",".join("?" * len(run_ids))
    cur = repo.conn.execute(
        f"SELECT run_id, source, category, rank, word, hot_value_num, label, "
        f"       is_ad, is_top, topic_category "
        f"FROM snapshot WHERE run_id IN ({marks})",
        run_ids,
    )
    return [dict(r) for r in cur.fetchall()]


def _recent_run_ids(repo, last_n: int) -> list[str]:
    """按插入顺序（rowid）取最近 N 轮 run_id；last_n <= 0 取全部。

    不用 started_at：同一秒内跑多轮时时间戳退化，排序不确定。
    """
    sql = ("SELECT run_id FROM run WHERE status IN ('ok','partial') "
           "ORDER BY rowid DESC")
    params: tuple = ()
    if last_n and last_n > 0:
        sql += " LIMIT ?"
        params = (last_n,)
    return [r["run_id"] for r in repo.conn.execute(sql, params).fetchall()]


# ============================================================
# 持续上榜分析（V3 主线）
# ============================================================
# 断档阈值：相邻两轮的间隔超过该值即视为"中断"，streak 不再延续。
# 没有它，跨天（停一晚再挂）会被误算成"连续"，streak 一路虚高，
# "连续 N 轮"就失去意义 —— 实测踩到：隔 19 小时仍被判为相邻轮。
# 挂机整轮默认 10 分钟，3 小时容差足以覆盖重启/短暂中断，又能在跨天时正确断开。
MAX_GAP_MINUTES = 180
# 核心问题：单轮"跨源共现"有偶发噪声 —— 某词刚好两源同时出现，未必是真热点。
# 真正稳定的信号是"跨轮持续性"：
#     连续 N 轮都在榜  >>  只出现 1 轮
# 所以引入 current_streak（当前连续轮数）与 persistence_score（综合持续度）。
#
# 增量更新策略（避免全表重算）：
#   每轮采集结束后调用 track_persistence(run_id)：
#     - 本轮上榜的词：total_rounds+1、streak+1（若上轮也在榜）
#     - 本轮未上榜的词：streak 归零（断档）
#   "上轮是否在榜"由 last_seen_run 与倒数第二个 run_id 比对得出。
# ============================================================
@dataclass
class TrackRow:
    word: str
    word_hash: str
    total_rounds: int
    current_streak: int
    max_streak: int
    best_rank: int
    last_rank: int | None
    source_count: int
    sources: list[str]
    persistence_score: float
    first_seen_at: str
    last_seen_at: str
    is_ad: bool = False
    is_top: bool = False
    labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "total_rounds": self.total_rounds,
            "current_streak": self.current_streak,
            "max_streak": self.max_streak,
            "best_rank": self.best_rank,
            "last_rank": self.last_rank,
            "source_count": self.source_count,
            "sources": self.sources,
            "persistence_score": round(self.persistence_score, 2),
            "is_ad": self.is_ad,
            "is_top": self.is_top,
            "labels": self.labels,
        }


def compute_persistence_score(
    total_rounds: int,
    current_streak: int,
    max_streak: int,
    best_rank: int,
    source_count: int,
    total_runs: int,
) -> float:
    """持续度综合分（0..~150）。

    权重设计：
      - 连续轮数是"真热度"最强信号 → 权重最高，且用 max_streak 抗短期中断
      - 上榜占比（total_rounds/total_runs）反映整体稳定性
      - 最佳名次体现峰值强度
      - 源数体现跨平台广度
    """
    total_runs = max(total_runs, 1)
    denom = max(total_runs - 1, 1)

    streak_part = 100.0 * (current_streak - 1) / denom        # 当前连续（0..100）
    max_streak_part = 100.0 * (max_streak - 1) / denom        # 历史最长（抗中断）
    coverage_part = 100.0 * total_rounds / total_runs         # 上榜占比
    # 名次分：第 1 名=100，越靠后越低（rank 100 名约 50 分）
    rank_part = 100.0 / (1.0 + math.log1p(max(best_rank, 1)) / 3.0)
    breadth_part = 20.0 * (source_count - 1)                  # 每多一个源 +20

    return (
        0.35 * streak_part
        + 0.20 * max_streak_part
        + 0.20 * coverage_part
        + 0.15 * rank_part
        + 0.10 * breadth_part
    )


def _exclude_clause(
    exclude: set[tuple[str, str]] | None,
    exclude_labels: set[tuple[str, int]] | None = None,
) -> tuple[str, list]:
    """把排除规则编译成 SQL 片段。返回 (sql_fragment, params)。

    两类规则，粒度不同，得分开表达：
      - exclude        {(source, category)}：整张榜都排除。
        用于百度 movie/novel/teleplay 这类【推广位榜单】—— 其 #1 在 39/39
        轮里纹丝不动，整张榜都不构成搜索热度。
      - exclude_labels {(source, label_id)}：只排除带该徽标的【条目】。
        用于抖音「挑战」话题 —— 活动话题和真实热搜混在同一张榜里，
        整榜排除会把真热点一起扔掉，所以只能按条目排。
    """
    ors, params = [], []
    for src, cat in sorted(exclude or ()):
        ors.append("(source=? AND category=?)")
        params.extend([src, cat])
    for src, lab in sorted(exclude_labels or ()):
        ors.append("(source=? AND label_id=?)")
        params.extend([src, lab])
    if not ors:
        return "", []
    return " AND NOT (" + " OR ".join(ors) + ")", params


def _gap_minutes(prev_finished: str | None, cur_started: str | None) -> float | None:
    """两个 ISO 时间戳之间的分钟数；任一缺失或无法解析则返回 None。"""
    if not prev_finished or not cur_started:
        return None
    try:
        a = datetime.fromisoformat(prev_finished)
        b = datetime.fromisoformat(cur_started)
    except (ValueError, TypeError):
        return None
    return (b - a).total_seconds() / 60.0


def track_persistence(
    repo,
    run_id: str,
    total_runs: int | None = None,
    max_gap_minutes: int = MAX_GAP_MINUTES,
    exclude: set[tuple[str, str]] | None = None,
    exclude_labels: set[tuple[str, int]] | None = None,
) -> int:
    """增量更新 word_track。返回本轮涉及词条数。

    在每轮采集完成后调用（pipeline.run_once 末尾）。

    max_gap_minutes: 与上一轮的间隔超过该值即视为断档（streak 重置），
                     避免跨天的两段挂机被误算成"连续"。
    exclude:         {(source, category)} 黑名单，这些榜单不参与统计
                     （运营推广位）。数据仍保留在 snapshot 里。
    exclude_labels:  {(source, label_id)} 黑名单，带这些徽标的条目不参与统计
                     （抖音「挑战」活动话题）。同样是"只排除统计、不排除采集"。
    """
    import hashlib
    from hotwords.models import utcnow_iso
    from hotwords.normalize import normalize_word

    now = utcnow_iso()

    # 本轮各词条的最佳名次 + 来源（跳过置顶 rank=0；并剔除运营位/活动话题）
    where, params = _exclude_clause(exclude, exclude_labels)
    rows = repo.conn.execute(
        f"""SELECT word, MIN(CASE WHEN rank>0 THEN rank END) AS best_rank,
                  GROUP_CONCAT(DISTINCT source) AS srcs,
                  GROUP_CONCAT(DISTINCT label) AS labs,
                  MAX(is_ad) AS is_ad, MAX(is_top) AS is_top
           FROM snapshot WHERE run_id=?{where} GROUP BY word""",
        (run_id, *params),
    ).fetchall()

    if total_runs is None:
        total_runs = repo.conn.execute(
            "SELECT COUNT(*) c FROM run WHERE status IN ('ok','partial')"
        ).fetchone()["c"]

    # 上一轮：用 rowid 判定插入顺序，不依赖 started_at
    # （同一秒内跑多轮时 started_at 会退化，导致 prev_run 选错，实测踩到）
    prev_row = repo.conn.execute(
        """SELECT r.run_id, r.finished_at FROM run r
           WHERE r.status IN ('ok','partial')
             AND r.rowid < (SELECT rowid FROM run WHERE run_id = ?)
           ORDER BY r.rowid DESC LIMIT 1""",
        (run_id,),
    ).fetchone()

    prev_run = None
    if prev_row:
        cur_row = repo.conn.execute(
            "SELECT started_at FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        gap = _gap_minutes(
            prev_row["finished_at"], cur_row["started_at"] if cur_row else None
        )
        # gap 为 None（时间戳缺失/无法解析）时按"中断"处理：
        # 宁可重置 streak，也不让缺失的时间戳撑出虚高的连续记录。
        if gap is not None and gap <= max_gap_minutes:
            prev_run = prev_row["run_id"]
        else:
            logger.debug(
                "距上一轮 %s 分钟（阈值 %d），视为断档，streak 重置",
                f"{gap:.1f}" if gap is not None else "未知", max_gap_minutes,
            )

    touched = 0
    seen_hashes: set[str] = set()

    for r in rows:
        word = (r["word"] or "").strip()
        if not word:
            continue
        key = normalize_word(word)
        if not key:
            continue
        word_hash = hashlib.sha1(key.encode()).hexdigest()
        seen_hashes.add(word_hash)

        best_rank = int(r["best_rank"]) if r["best_rank"] is not None else 999
        # 注意：必须先做 set 差集再 sorted，否则 list - set 会 TypeError
        srcs = sorted({s for s in (r["srcs"] or "").split(",") if s})
        labs = sorted({x for x in (r["labs"] or "").split(",") if x})
        new_ad = int(r["is_ad"] or 0)
        new_top = int(r["is_top"] or 0)

        cur = repo.conn.execute(
            "SELECT * FROM word_track WHERE word_hash=?", (word_hash,)
        ).fetchone()

        if cur is None:
            repo.conn.execute(
                """INSERT INTO word_track
                   (word_hash, canonical_word, first_seen_run, last_seen_run,
                    first_seen_at, last_seen_at, total_rounds, current_streak,
                    max_streak, best_rank, last_rank, source_count, sources,
                    labels, persistence_score, is_ad, is_top, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (word_hash, word, run_id, run_id, now, now,
                 1, 1, 1, best_rank, best_rank, len(srcs) or 1,
                 ",".join(srcs), ",".join(labs) or None, 0.0, new_ad, new_top, now),
            )
        else:
            # streak 判断：上一轮（prev_run）是否也在榜 → 连续 +1，否则重置为 1
            continued = bool(prev_run) and cur["last_seen_run"] == prev_run
            streak = cur["current_streak"] + 1 if continued else 1
            total_rounds = cur["total_rounds"] + 1
            max_streak = max(cur["max_streak"], streak)
            all_srcs = sorted({s for s in (cur["sources"] or "").split(",") if s} | set(srcs))
            all_labs = sorted({x for x in (cur["labels"] or "").split(",") if x} | set(labs))
            best = min(cur["best_rank"], best_rank)
            score = compute_persistence_score(
                total_rounds, streak, max_streak, best, len(all_srcs) or 1, total_runs
            )
            # 标记只累积不回退：一旦被标为广告位/置顶就永久保留，
            # 否则同一词条在标记消失的那一轮会"洗白"，历史就不诚实了。
            repo.conn.execute(
                """UPDATE word_track SET last_seen_run=?, last_seen_at=?, total_rounds=?,
                   current_streak=?, max_streak=?, best_rank=?, last_rank=?,
                   source_count=?, sources=?, labels=?, persistence_score=?,
                   is_ad=?, is_top=?, updated_at=?
                   WHERE word_hash=?""",
                (run_id, now, total_rounds, streak, max_streak, best, best_rank,
                 len(all_srcs) or 1, ",".join(all_srcs),
                 ",".join(all_labs) or None, score,
                 int(bool(cur["is_ad"]) or bool(new_ad)),
                 int(bool(cur["is_top"]) or bool(new_top)),
                 now, word_hash),
            )
        touched += 1

    # 本轮未上榜的词：连续轮数归零（断档检测）
    placeholders = ",".join("?" * len(seen_hashes)) if seen_hashes else None
    if placeholders:
        repo.conn.execute(
            f"""UPDATE word_track SET current_streak=0, updated_at=?
                WHERE last_seen_run <> ? AND word_hash NOT IN ({placeholders})""",
            (now, run_id, *seen_hashes),
        )
    else:
        repo.conn.execute(
            "UPDATE word_track SET current_streak=0, updated_at=? WHERE last_seen_run <> ?",
            (now, run_id),
        )

    repo.conn.commit()
    return touched


def rebuild_track(
    repo,
    max_gap_minutes: int = MAX_GAP_MINUTES,
    exclude: set[tuple[str, str]] | None = None,
    exclude_labels: set[tuple[str, int]] | None = None,
) -> int:
    """从 snapshot 全量重放，重建 word_track。

    用途：
      - 修正历史脏数据（早期版本没有断档检测，跨天会被算成"连续"）
      - 调整算法或权重之后重算
      - 调整运营位 / 活动话题排除清单之后重算（exclude / exclude_labels）
    返回末轮涉及的词条数。
    """
    repo.conn.execute("DELETE FROM word_track")
    repo.conn.commit()

    run_ids = [
        r["run_id"]
        for r in repo.conn.execute(
            "SELECT run_id FROM run WHERE status IN ('ok','partial') ORDER BY rowid"
        ).fetchall()
    ]
    total = len(run_ids)
    touched = 0
    for rid in run_ids:
        touched = track_persistence(
            repo, rid, total_runs=max(total, 1),
            max_gap_minutes=max_gap_minutes,
            exclude=exclude, exclude_labels=exclude_labels,
        )
    logger.info("word_track 已从 %d 轮快照重建，末轮涉及 %d 个词条", total, touched)
    return touched


def load_persistence(repo, limit: int = 30, min_streak: int = 1) -> list[TrackRow]:
    """读取持续上榜榜（按持续度排序）。"""
    q = """SELECT canonical_word, word_hash, total_rounds, current_streak, max_streak,
                  best_rank, last_rank, source_count, sources, labels,
                  persistence_score, first_seen_at, last_seen_at, is_ad, is_top
           FROM word_track WHERE current_streak >= ? OR max_streak >= ?
           ORDER BY current_streak DESC, persistence_score DESC LIMIT ?"""
    rows = repo.conn.execute(q, (min_streak, min_streak, limit)).fetchall()
    out: list[TrackRow] = []
    for r in rows:
        out.append(
            TrackRow(
                word=r["canonical_word"],
                word_hash=r["word_hash"],
                total_rounds=r["total_rounds"],
                current_streak=r["current_streak"],
                max_streak=r["max_streak"],
                best_rank=r["best_rank"],
                last_rank=r["last_rank"],
                source_count=r["source_count"],
                sources=(r["sources"] or "").split(",") if r["sources"] else [],
                persistence_score=r["persistence_score"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
                is_ad=bool(r["is_ad"]),
                is_top=bool(r["is_top"]),
                labels=(r["labels"] or "").split(",") if r["labels"] else [],
            )
        )
    return out
