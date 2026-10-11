"""归一化与解析的 smoke test。

跑法：python -m pytest tests/ -v  或  python tests/test_basic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwords.normalize import normalize_hot_value, normalize_word
from hotwords.parsers import get_parser, list_parsers


def test_normalize_hot_value():
    assert normalize_hot_value("1.2亿") == 120_000_000
    assert normalize_hot_value("8900万") == 89_000_000
    assert normalize_hot_value("100909") == 100909
    assert normalize_hot_value(5049246) == 5049246
    assert normalize_hot_value("") is None
    assert normalize_hot_value(None) is None
    assert normalize_hot_value("1,234") == 1234
    # dict 不应崩溃（由 parser 层负责解包）
    assert normalize_hot_value({"value": "1"}) is None


def test_normalize_word():
    assert normalize_word("  测试 词条 ") == "测试词条"
    assert normalize_word("ＡＢＣ") == "abc"          # 全角 -> 半角 + 小写
    assert normalize_word("Hello World") == "helloworld"
    assert normalize_word("") == ""


def test_baidu_realtime_parse():
    """百度热搜榜：字段是 word，无 hotScore。"""
    payload = {
        "data": {"cards": [{"content": [{"content": [
            {"isTop": True, "url": "u0", "word": "置顶词"},
            {"index": 1, "url": "u1", "word": "普通词一"},
            {"index": 2, "url": "u2", "word": "普通词二"},
        ]}]}]}
    }
    r = get_parser("baidu")(payload, "baidu_hot", "realtime")
    assert len(r.items) == 3
    assert r.items[0].rank == 0 and r.items[0].label == "置顶"
    assert r.items[1].rank == 1 and r.items[1].word == "普通词一"
    assert r.items[1].hot_value is None       # 热搜榜无热度值


def test_baidu_vertical_parse():
    """百度垂直榜：字段是 title，hotScore 是 dict。"""
    payload = {
        "data": {"cards": [{"content": [{"content": [
            {"index": 1, "title": "电视剧甲", "url": "u1",
             "hotScore": {"type": 0, "value": "95471", "desc": "热搜指数"}},
        ]}]}]}
    }
    r = get_parser("baidu")(payload, "baidu_hot", "teleplay")
    assert len(r.items) == 1
    assert r.items[0].word == "电视剧甲"          # title -> word
    assert r.items[0].hot_value_num == 95471      # dict 已解包


def test_bilibili_parse():
    payload = {"code": 0, "data": {"trending": {"list": [
        {"keyword": "kw", "show_name": "展示名", "heat_score": 5049246},
    ]}}}
    r = get_parser("bilibili")(payload, "bilibili_hot", "main")
    assert len(r.items) == 1
    assert r.items[0].word == "展示名"
    assert r.items[0].hot_value_num == 5049246


def test_parsers_registered():
    for name in ["baidu", "bilibili", "weibo", "douyin", "toutiao", "zhihu"]:
        assert name in list_parsers()


def test_toutiao_parse():
    """头条：字段首字母大写，Label 是英文枚举。"""
    payload = {
        "fixed_top_data": {"Title": "置顶新闻", "Url": "u0"},
        "data": [
            {"Title": "普通新闻", "Url": "u1", "HotValue": "47464037", "Label": "new"},
            {"Title": "另一条", "Url": "u2", "HotValue": "12345", "Label": "boom"},
        ],
    }
    r = get_parser("toutiao")(payload, "toutiao_hot", "main")
    assert len(r.items) == 3
    assert r.items[0].rank == 0 and r.items[0].label == "置顶"
    assert r.items[1].word == "普通新闻" and r.items[1].hot_value_num == 47464037
    assert r.items[1].label == "新"        # new -> 新
    assert r.items[2].label == "爆"        # boom -> 爆


def test_cross_source_weight():
    """跨源共现：2 源共现的 heat_index 应高于单源同名次。"""
    from hotwords.analysis import compute_cross_source

    rows = [
        # 词A 只在百度，排第 1
        {"source": "baidu_hot", "category": "realtime", "rank": 1, "word": "词A", "hot_value_num": None},
        # 词B 在百度和微博都排第 1
        {"source": "baidu_hot", "category": "realtime", "rank": 1, "word": "词B", "hot_value_num": None},
        {"source": "weibo_hot", "category": "main", "rank": 1, "word": "词B", "hot_value_num": None},
    ]
    words = {w.word: w for w in compute_cross_source(rows)}
    assert words["词A"].cross_source_count == 1
    assert words["词B"].cross_source_count == 2
    # 跨源权重使词B热度指数更高
    assert words["词B"].heat_index > words["词A"].heat_index
    assert words["词B"].sources == ["baidu_hot", "weibo_hot"]


def test_cross_source_skips_top():
    """置顶条目（rank=0）不参与共现统计。"""
    from hotwords.analysis import compute_cross_source

    rows = [
        {"source": "baidu_hot", "category": "realtime", "rank": 0, "word": "置顶词", "hot_value_num": None},
        {"source": "baidu_hot", "category": "realtime", "rank": 1, "word": "普通词", "hot_value_num": None},
    ]
    words = [w.word for w in compute_cross_source(rows)]
    assert "置顶词" not in words
    assert "普通词" in words


def test_persistence_score_monotonic():
    """持续度：连续轮数越多、名次越好，得分应越高。"""
    from hotwords.analysis import compute_persistence_score as S

    kw = dict(total_rounds=5, source_count=1, total_runs=5)
    low = S(current_streak=1, max_streak=1, best_rank=50, **kw)
    high = S(current_streak=5, max_streak=5, best_rank=1, **kw)
    assert high > low
    # 同样是连续 5 轮，名次更好的得分更高
    better_rank = S(current_streak=5, max_streak=5, best_rank=1, **kw)
    worse_rank = S(current_streak=5, max_streak=5, best_rank=80, **kw)
    assert better_rank > worse_rank
    # 跨源应加分
    multi_src = S(current_streak=5, max_streak=5, best_rank=1, total_rounds=5,
                  source_count=3, total_runs=5)
    assert multi_src > better_rank


def test_track_persistence_streak():
    """端到端：连续两轮同一词条，streak 应递增到 2。"""
    import tempfile
    from pathlib import Path

    from hotwords.storage import Repository
    from hotwords.analysis import track_persistence, load_persistence
    from hotwords.models import HotItem, ParseResult

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        repo = Repository(Path(td) / "t.db")
        try:
            for round_no in (1, 2, 3):
                rid = repo.start_run()
                items = [
                    HotItem(source="baidu_hot", category="realtime", rank=1, word="钉子户"),
                    HotItem(source="baidu_hot", category="realtime", rank=2, word=f"轮{round_no}专属"),
                ]
                repo.save_snapshot(rid, ParseResult("baidu_hot", "realtime", items, {"word"}))
                repo.upsert_words(items)
                repo.finish_run(rid, "ok", {"baidu_hot": 2})
                track_persistence(repo, rid)

            rows = {r.word: r for r in load_persistence(repo, limit=10)}
            # 钉子户连续 3 轮上榜
            assert rows["钉子户"].current_streak == 3
            assert rows["钉子户"].total_rounds == 3
            # 轮1专属 只出现 1 轮，随后断档 → streak 归零（这正是断档检测该做的）
            assert rows["轮1专属"].current_streak == 0
            assert rows["轮1专属"].total_rounds == 1
            # 轮3专属 是最后一轮的新词，streak=1
            assert rows["轮3专属"].current_streak == 1
        finally:
            repo.close()


def test_weibo_ad_flag():
    """微博广告位：is_ad/topic_ad 归一到 is_ad，flag_desc 作为内容分类。"""
    payload = {"data": {"realtime": [
        {"word": "品牌代言人", "num": 100, "is_ad": 1, "topic_ad": 1, "flag_desc": "综艺"},
        {"word": "正常热搜", "num": 200, "label_name": "热"},
    ]}}
    r = get_parser("weibo")(payload, "weibo_hot", "main")
    assert r.items[0].is_ad is True
    assert r.items[0].topic_category == "综艺"
    assert r.items[1].is_ad is False
    assert r.items[1].label == "热"


def test_douyin_label_map():
    """抖音 label 是内部数字枚举：只映射有把握的，未确认的一律 None（不猜）。"""
    payload = {"data": {"word_list": [
        {"word": "甲", "hot_value": 1, "label": 1},
        {"word": "乙", "hot_value": 2, "label": 3},
        {"word": "丙", "hot_value": 3, "label": 9},
    ]}}
    r = get_parser("douyin")(payload, "douyin_hot", "main")
    assert r.items[0].label == "新"
    assert r.items[1].label == "热"
    assert r.items[2].label is None


def test_toutiao_marks_and_category():
    """头条：置顶标记、LabelDesc 优先、InterestCategory 映射中文。"""
    payload = {
        "fixed_top_data": {"Title": "置顶", "InterestCategory": ["finance"]},
        "data": [
            {"Title": "新闻", "HotValue": "1", "Label": "refuteRumors",
             "LabelDesc": "辟谣", "InterestCategory": ["taiwan"]},
        ],
    }
    r = get_parser("toutiao")(payload, "toutiao_hot", "main")
    assert r.items[0].is_top is True
    assert r.items[0].topic_category == "财经"
    assert r.items[1].label == "辟谣"            # LabelDesc 优先于英文枚举
    assert r.items[1].topic_category == "中国台湾"


def test_baidu_marks():
    """百度：isTop 落到 is_top，标签取 newHotName（Category 实测恒空，不可用）。"""
    payload = {"data": {"cards": [{"content": [{"content": [
        {"isTop": True, "word": "置顶词", "index": 0},
        {"index": 1, "word": "普通词", "newHotName": "热", "Category": ""},
    ]}]}]}}
    r = get_parser("baidu")(payload, "baidu_hot", "realtime")
    assert r.items[0].is_top is True
    assert r.items[1].is_top is False
    assert r.items[1].label == "热"


def test_snapshot_marks_roundtrip():
    """标记字段落库后可读回。"""
    import tempfile
    from pathlib import Path

    from hotwords.storage import Repository
    from hotwords.models import HotItem, ParseResult

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        repo = Repository(Path(td) / "t.db")
        try:
            rid = repo.start_run()
            items = [HotItem(source="weibo_hot", category="main", rank=1, word="广告词",
                             is_ad=True, topic_category="综艺")]
            repo.save_snapshot(rid, ParseResult("weibo_hot", "main", items, {"word"}))
            row = repo.conn.execute(
                "SELECT is_ad, is_top, topic_category FROM snapshot"
            ).fetchone()
            assert row["is_ad"] == 1
            assert row["is_top"] == 0
            assert row["topic_category"] == "综艺"
        finally:
            repo.close()


def test_legacy_db_migration():
    """老库（无标记列）打开后自动补列，历史行取默认值。

    这是本次改动最危险的回归点：`CREATE TABLE IF NOT EXISTS` 不会给已存在的表
    补列，若不迁移，线上那个 1.2 万条快照的库会在 INSERT 时直接报
    "no such column: is_ad"。
    """
    import sqlite3
    import tempfile
    from pathlib import Path

    from hotwords.storage import Repository

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        p = Path(td) / "legacy.db"
        con = sqlite3.connect(str(p))
        con.executescript("""
            CREATE TABLE snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL, source TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'main',
                rank INTEGER NOT NULL, word TEXT NOT NULL, hot_value TEXT,
                hot_value_num INTEGER, norm_version TEXT, label TEXT, url TEXT,
                raw TEXT, fetched_at TEXT NOT NULL);
            CREATE TABLE word_track (
                word_hash TEXT PRIMARY KEY, canonical_word TEXT NOT NULL,
                first_seen_run TEXT NOT NULL, last_seen_run TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                total_rounds INTEGER NOT NULL DEFAULT 1,
                current_streak INTEGER NOT NULL DEFAULT 1,
                max_streak INTEGER NOT NULL DEFAULT 1,
                best_rank INTEGER NOT NULL DEFAULT 999, last_rank INTEGER,
                source_count INTEGER NOT NULL DEFAULT 1, sources TEXT,
                persistence_score REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
            INSERT INTO snapshot(run_id,source,category,rank,word,fetched_at)
                VALUES('r1','baidu_hot','realtime',1,'历史词','2026-01-01T00:00:00');
            INSERT INTO word_track(word_hash,canonical_word,first_seen_run,last_seen_run,
                first_seen_at,last_seen_at,updated_at)
                VALUES('h1','历史词','r1','r1','2026-01-01T00:00:00','2026-01-01T00:00:00',
                       '2026-01-01T00:00:00');
        """)
        con.commit()
        con.close()

        repo = Repository(p)
        try:
            cols = {r["name"] for r in repo.conn.execute("PRAGMA table_info(snapshot)")}
            assert {"is_ad", "is_top", "topic_category"} <= cols
            wt = {r["name"] for r in repo.conn.execute("PRAGMA table_info(word_track)")}
            assert {"is_ad", "is_top"} <= wt
            row = repo.conn.execute(
                "SELECT is_ad, is_top, topic_category FROM snapshot"
            ).fetchone()
            assert row["is_ad"] == 0 and row["is_top"] == 0
            assert row["topic_category"] is None
            # 历史行仍在（迁移没有重建表）
            assert repo.conn.execute("SELECT COUNT(*) c FROM snapshot").fetchone()["c"] == 1
        finally:
            repo.close()


def test_drift_ignores_optional_fields():
    """可选字段时有时无不应报漂移；关键字段缺失才报。

    坑 4 的回归测试：早期实现比较"字段集合是否完全相等"，
    而微博 is_ad(2.5%) / icon*(56%) 这类可选字段会让每轮都报漂移。
    """
    import tempfile
    from pathlib import Path

    from hotwords.storage import Repository
    from hotwords.models import HotItem, ParseResult

    def mk(fields):
        items = [HotItem(source="weibo_hot", category="main", rank=1, word="词")]
        return ParseResult("weibo_hot", "main", items, set(fields))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        repo = Repository(Path(td) / "t.db")
        try:
            # 第 1 轮：字段多（含可选字段）
            assert repo.check_drift(
                mk({"word", "num", "is_ad", "icon"}), must_have={"word"}
            ) is None
            # 第 2 轮：可选字段消失 —— 关键字段 word 仍在，不应报
            assert repo.check_drift(mk({"word", "num"}), must_have={"word"}) is None
            # 第 3 轮：关键字段 word 消失 -> 必须报
            p = repo.check_drift(mk({"num", "is_ad"}), must_have={"word"})
            assert p and "关键字段缺失" in p
        finally:
            repo.close()


def test_drift_any_of_and_bulk_change():
    """多选一字段（百度 word/title）与"字段大面积换血"兜底。"""
    import tempfile
    from pathlib import Path

    from hotwords.storage import Repository
    from hotwords.models import HotItem, ParseResult

    def mk(fields):
        items = [HotItem(source="baidu_hot", category="realtime", rank=1, word="词")]
        return ParseResult("baidu_hot", "realtime", items, set(fields))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        repo = Repository(Path(td) / "t.db")
        try:
            # 只有 title（垂直榜情形）-> any_of 满足，不报
            assert repo.check_drift(mk({"title", "url"}), any_of={"word", "title"}) is None
            # word 与 title 都没有 -> 报
            p = repo.check_drift(mk({"foo", "bar"}), any_of={"word", "title"})
            assert p and "候选关键字段全部缺失" in p
            # 旧字段几乎全消失（>50%）-> 兜底报
            repo.check_drift(mk({"a", "b", "c", "d", "word"}), any_of={"word", "title"})
            p2 = repo.check_drift(mk({"x", "y", "word"}), any_of={"word", "title"})
            assert p2 and "大面积变化" in p2
        finally:
            repo.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
