"""突现 / 涨跌分析：从"谁长期在榜"扩展到"什么正在爆发"。

============================================================
和 analysis.py 的分工
============================================================
analysis.py 回答的是"这个词**长期**怎么样"——靠跨轮累加（streak / 覆盖度）；
本模块回答的是"这个词**此刻**发生了什么"——只看**这一轮相对它上次出现**
的变化。两者输入相同（snapshot），但一个看历史长度、一个看瞬时变化，
塞进同一个文件只会让两边都难读。

============================================================
为什么"突现"要单独算，不能用持续度反着看
============================================================
持续度对**新词天然不利**：一个刚上榜的词累加轮数少、覆盖度低，
分数一定排在老词后面。而"什么正在爆发"恰恰需要放大新词的信号。
所以必须换一套判据，核心是三个可观测事实：
  1) 它是不是**刚出现**（全新 / 断档后回归）
  2) 它出现时**站在什么位置**（首秀即前 10 名，比首秀 45 名强得多）
  3) 它**同时在几个源**出现（跨源共现几乎不可能是巧合）
另外对"连续在榜但快速爬升"的词，看名次跃升幅度。

============================================================
突现分的构成（刻意做成可解释的线性加权，不做黑盒）
============================================================
    新词            +35     历史首次出现
    断档后回归      +20     之前出现过，但中间断了 ≥1 轮
    位置强度        +(30-rank)  rank<30 才计分，第 1 名 → +29
    名次跃升        +min(delta,30)*0.8   仅当上轮也在榜且名次上升
    跨源广度        +12*(源数-1)         当轮同时在多个源出现
取值范围 0 ~ 约 135。权重都是整数、可手算，方便解释"为什么它排在前面"。

注意：这里只做**排序**，不做"是不是真热点"的判断 ——
运营位/活动话题仍按 config 的排除清单剔除，与持续榜口径保持一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hotwords.analysis import _exclude_clause

W_NEW = 35.0          # 历史首次出现
W_REENTRY = 20.0      # 断档后回归
W_RANK_BASE = 30.0    # 位置强度：rank 越靠前越高
W_DELTA = 0.8         # 名次跃升系数
W_CROSS = 12.0        # 每多一个源


@dataclass
class Emergence:
    word: str
    best_rank: int
    sources: list[str] = field(default_factory=list)
    prev_rank: int | None = None
    rounds_absent: int | None = None   # None = 从未出现过（全新词）
    is_new: bool = False
    rank_delta: int | None = None      # prev_rank - best_rank，正数=上升
    rise_per_round: float | None = None  # 名次/轮（跃升速度）
    score: float = 0.0
    reason: str = ""                   # 人类可读的得分构成

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "rank": self.best_rank,
            "sources": self.sources,
            "source_count": len(self.sources),
            "prev_rank": self.prev_rank,
            "rounds_absent": self.rounds_absent,
            "is_new": self.is_new,
            "rank_delta": self.rank_delta,
            "rise_per_round": (round(self.rise_per_round, 2)
                               if self.rise_per_round is not None else None),
            "score": round(self.score, 2),
            "reason": self.reason,
        }


def _run_index(repo) -> tuple[dict[int, int], list[int]]:
    """返回 (rowid -> 顺序下标, 顺序下标列表)。只算 ok/partial 轮。"""
    rows = repo.conn.execute(
        "SELECT rowid FROM run WHERE status IN ('ok','partial') ORDER BY rowid"
    ).fetchall()
    order = [r["rowid"] for r in rows]
    return {rn: i for i, rn in enumerate(order)}, order


def detect_emergence(
    repo,
    limit: int = 30,
    min_score: float = 0.0,
    exclude: set[tuple[str, str]] | None = None,
    exclude_labels: set[tuple[str, int]] | None = None,
) -> list[Emergence]:
    """检测**最后一轮**里正在突现 / 跃升的词条。

    为什么只看最后一轮：突现是"此刻"的事。要看历史某轮，把 DB 恢复到那时
    或用 --last 配合 reload 即可；做成"任意轮"会让 CLI 与看板都要传一个
    游标，收益不抵复杂度。
    """
    order_map, order = _run_index(repo)
    if not order:
        return []
    cur_rn = order[-1]

    where, params = _exclude_clause(exclude, exclude_labels, prefix="s.")

    # 本轮各词条的最佳名次与来源（置顶 rank=0 不参与名次比较）
    cur_rows = repo.conn.execute(
        f"""SELECT s.word,
                   MIN(CASE WHEN s.rank>0 THEN s.rank END) AS best_rank,
                   GROUP_CONCAT(DISTINCT s.source) AS srcs
            FROM snapshot s JOIN run r ON r.run_id = s.run_id
            WHERE r.rowid = ?{where}
            GROUP BY s.word""",
        (cur_rn, *params),
    ).fetchall()
    if not cur_rows:
        return []

    words = [r["word"] for r in cur_rows]
    marks = ",".join("?" * len(words))

    # 每个词的"首次出现轮"与"上一次出现轮"，一次查全（跨全历史，不受窗口影响）
    # sqlite3.Row 没有 .get()，先转成普通 dict 再用
    hist = {
        r["word"]: {"prev_rn": r["prev_rn"], "first_rn": r["first_rn"]}
        for r in repo.conn.execute(
            f"""SELECT s.word,
                       MAX(CASE WHEN r.rowid < ? THEN r.rowid END) AS prev_rn,
                       MIN(r.rowid) AS first_rn
                FROM snapshot s JOIN run r ON r.run_id = s.run_id
                WHERE r.status IN ('ok','partial') AND s.word IN ({marks})
                GROUP BY s.word""",
            (cur_rn, *words),
        ).fetchall()
    }

    out: list[Emergence] = []
    for r in cur_rows:
        word = r["word"]
        rank = int(r["best_rank"]) if r["best_rank"] is not None else 999
        srcs = sorted({s for s in (r["srcs"] or "").split(",") if s})
        h = hist.get(word) or {}
        first_rn = h.get("first_rn")
        prev_rn = h.get("prev_rn")

        is_new = first_rn == cur_rn
        rounds_absent = None
        prev_rank = None
        if prev_rn is not None and prev_rn in order_map:
            rounds_absent = order_map[cur_rn] - order_map[prev_rn] - 1
            pr = repo.conn.execute(
                """SELECT MIN(CASE WHEN rank>0 THEN rank END) AS r
                   FROM snapshot s JOIN run r2 ON r2.run_id=s.run_id
                   WHERE r2.rowid=? AND s.word=?""",
                (prev_rn, word),
            ).fetchone()
            prev_rank = int(pr["r"]) if pr and pr["r"] is not None else None

        delta = (prev_rank - rank) if (prev_rank is not None and rank < 999) else None
        rise = None
        if delta is not None and delta > 0:
            rise = delta / max((rounds_absent or 0) + 1, 1)

        # ---- 得分 ----
        parts: list[str] = []
        score = 0.0
        if is_new:
            score += W_NEW
            parts.append(f"新词+{W_NEW:.0f}")
        elif rounds_absent:
            score += W_REENTRY
            parts.append(f"断{rounds_absent}轮后回归+{W_REENTRY:.0f}")
        if rank < W_RANK_BASE:
            pos = W_RANK_BASE - rank
            score += pos
            parts.append(f"第{rank}名+{pos:.0f}")
        if delta is not None and delta > 0:
            jump = min(delta, 30) * W_DELTA
            score += jump
            parts.append(f"升{delta}位+{jump:.1f}")
        if len(srcs) > 1:
            cross = W_CROSS * (len(srcs) - 1)
            score += cross
            parts.append(f"{len(srcs)}源+{cross:.0f}")

        if score < min_score:
            continue
        out.append(
            Emergence(
                word=word, best_rank=rank, sources=srcs,
                prev_rank=prev_rank, rounds_absent=rounds_absent,
                is_new=is_new, rank_delta=delta, rise_per_round=rise,
                score=score, reason=" + ".join(parts) or "无显著变化",
            )
        )

    out.sort(key=lambda e: (-e.score, e.best_rank))
    return out[:limit]


def rank_series(repo, words: list[str], last_n: int | None = None) -> tuple[list[str], dict[str, list[int | None]]]:
    """取出若干词条的逐轮最佳名次，供看板画生命周期曲线。

    返回 (轮次标签, {词: [每轮名次或 None]})。None 表示该轮不在榜 ——
    折线里必须断开，不能连成直线，否则会假造出"一直在榜"的错觉。
    """
    order_map, order = _run_index(repo)
    if last_n:
        order = order[-last_n:]
    if not order or not words:
        return [], {}

    labels = [
        r["started_at"][5:16]
        for r in repo.conn.execute(
            f"SELECT started_at FROM run WHERE rowid IN ({','.join('?'*len(order))}) ORDER BY rowid",
            order,
        ).fetchall()
    ]
    idx = {rn: i for i, rn in enumerate(order)}
    marks = ",".join("?" * len(words))
    series: dict[str, list[int | None]] = {w: [None] * len(order) for w in words}
    for r in repo.conn.execute(
        f"""SELECT r2.rowid AS rn, s.word,
                   MIN(CASE WHEN s.rank>0 THEN s.rank END) AS rank
            FROM snapshot s JOIN run r2 ON r2.run_id = s.run_id
            WHERE r2.status IN ('ok','partial') AND s.word IN ({marks})
            GROUP BY r2.rowid, s.word""",
        words,
    ):
        w = r["word"]
        i = idx.get(r["rn"])
        if i is not None and w in series and r["rank"] is not None:
            series[w][i] = int(r["rank"])
    return labels, series
