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
    """抖音 label 全量映射为中文徽标，且原始数字进 label_id。

    这条测试的前身锁的是旧立场「只映射有把握的 1/3，其余一律 None（不猜）」。
    那个立场本身没错，但前提变了：2026-10-11 找到了不需要猜的证据 ——
    每条带 label 的条目都附徽标图 .../hotspot_detail_page/<label>.png，
    下载放大后 8 个值全部肉眼可读（见 labels.py / README 坑 21）。
    所以行为从"留空"改为"全量映射"，测试随之更新；
    但「未知值仍必须 None」这条底线保留不变。
    """
    payload = {"data": {"word_list": [
        {"word": "甲", "hot_value": 1, "label": 1},
        {"word": "乙", "hot_value": 2, "label": 3},
        {"word": "丙", "hot_value": 3, "label": 9, "word_sub_board": [3]},
        {"word": "丁", "hot_value": 4, "label": 17},
        {"word": "戊", "hot_value": 5, "label": 999},   # 未出现过的枚举
        {"word": "己", "hot_value": 6},                  # 无 label
    ]}}
    r = get_parser("douyin")(payload, "douyin_hot", "main")
    assert r.items[0].label == "新" and r.items[0].label_id == 1
    assert r.items[1].label == "热" and r.items[1].label_id == 3
    assert r.items[2].label == "挑战" and r.items[2].label_id == 9
    assert r.items[2].sub_board == "[3]"
    assert r.items[3].label == "热议" and r.items[3].label_id == 17
    # 底线：没见过的枚举值不能瞎编，中文名必须留空（但 id 原样保留）
    assert r.items[4].label is None and r.items[4].label_id == 999
    assert r.items[5].label is None and r.items[5].label_id is None
    assert r.items[5].sub_board is None


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
                             is_ad=True, topic_category="综艺"),
                     HotItem(source="douyin_hot", category="main", rank=2, word="挑战词",
                             label="挑战", label_id=9, sub_board="[3]")]
            repo.save_snapshot(rid, ParseResult("weibo_hot", "main", items, {"word"}))
            row = repo.conn.execute(
                "SELECT is_ad, is_top, topic_category, label_id, sub_board "
                "FROM snapshot WHERE word='广告词'"
            ).fetchone()
            assert row["is_ad"] == 1
            assert row["is_top"] == 0
            assert row["topic_category"] == "综艺"
            assert row["label_id"] is None and row["sub_board"] is None
            row2 = repo.conn.execute(
                "SELECT label, label_id, sub_board FROM snapshot WHERE word='挑战词'"
            ).fetchone()
            assert row2["label"] == "挑战"
            assert row2["label_id"] == 9
            assert row2["sub_board"] == "[3]"
        finally:
            repo.close()


def test_label_exclude_is_item_level():
    """条目级排除：带指定徽标的条目不入 word_track，但榜内其它条目照常。

    这是坑 21 的核心回归点。与榜单级排除（track_exclude，整张 tab 排除）
    的区别必须守住：抖音「挑战」活动和真实热搜混在同一张榜里，
    若误用榜单级排除会把整张抖音榜一起扔掉。
    """
    import tempfile
    from pathlib import Path

    from hotwords.storage import Repository
    from hotwords.analysis import track_persistence, load_persistence
    from hotwords.models import HotItem, ParseResult

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        repo = Repository(Path(td) / "t.db")
        try:
            rid = repo.start_run()
            items = [
                HotItem(source="douyin_hot", category="main", rank=1, word="真实热点",
                        label="热", label_id=3),
                HotItem(source="douyin_hot", category="main", rank=2, word="活动话题",
                        label="挑战", label_id=9, sub_board="[3]"),
                HotItem(source="baidu_hot", category="realtime", rank=1, word="跨源热点"),
            ]
            repo.save_snapshot(rid, ParseResult("douyin_hot", "main", items, {"word"}))
            repo.upsert_words(items)
            repo.finish_run(rid, "ok", {})
            # 只排除"抖音 + label_id=9"，榜单与其它源都不受影响
            track_persistence(repo, rid, exclude_labels={("douyin_hot", 9)})

            words = {r.word for r in load_persistence(repo, limit=10)}
            assert "真实热点" in words, "同榜的真实热搜不能被牵连"
            assert "跨源热点" in words, "其它源不能被牵连"
            assert "活动话题" not in words, "带排除徽标的条目不应进入持续榜"

            # 但证据层必须完整：快照里那条必须还在
            n = repo.conn.execute(
                "SELECT COUNT(*) c FROM snapshot WHERE word='活动话题'"
            ).fetchone()["c"]
            assert n == 1, "只排除统计，不排除采集"
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
            assert {"is_ad", "is_top", "topic_category", "label_id", "sub_board"} <= cols
            wt = {r["name"] for r in repo.conn.execute("PRAGMA table_info(word_track)")}
            assert {"is_ad", "is_top"} <= wt
            row = repo.conn.execute(
                "SELECT is_ad, is_top, topic_category, label_id, sub_board FROM snapshot"
            ).fetchone()
            assert row["is_ad"] == 0 and row["is_top"] == 0
            assert row["topic_category"] is None
            # 老行的徽标列取默认值 NULL，不能被填成 0 ——
            # 0 在抖音语义里是"无徽标"，但老库里我们【根本不知道】，
            # 用 NULL 表达"未知"才诚实，也让 label_id=9 之类的过滤不会误命中
            assert row["label_id"] is None
            assert row["sub_board"] is None
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


def test_scheduler_stop_diagnostics():
    """中断/退出路径必须留下可归因的日志（事故直接教训）。

    背景：2026-10-11 挂机中途停跑，日志干净地断在「最后一轮成功」，
    既没有异常也没有「调度器已停止」，只能靠与手动 Ctrl+C 比对签名
    才推断出原因。这里锁死：收到信号必须写 WARNING + 收尾 + SystemExit，
    且 shutdown 自身出错不能影响退出。
    """
    import logging
    import signal as _signal

    from hotwords.scheduler import _log_atexit, _log_stop

    class Grab(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.DEBUG)
            self.records: list[logging.LogRecord] = []

        def emit(self, record):
            self.records.append(record)

    class FakeSched:
        def __init__(self, boom=False):
            self.calls: list[bool] = []
            self.boom = boom

        def shutdown(self, wait=True):
            self.calls.append(wait)
            if self.boom:
                raise RuntimeError("boom")

    lg = logging.getLogger("hotwords.scheduler")
    grab = Grab()
    old_level = lg.level
    lg.addHandler(grab)
    lg.setLevel(logging.DEBUG)
    try:
        # 1) 收到 SIGINT：写 WARNING、shutdown(wait=True)、以 SystemExit(0) 结束
        fs = FakeSched()
        try:
            _log_stop(int(_signal.SIGINT), fs)
        except SystemExit as e:
            assert e.code == 0
        else:
            raise AssertionError("_log_stop 必须以 SystemExit 结束进程")
        assert fs.calls == [True], f"应当 shutdown(wait=True)，实际 {fs.calls}"
        msgs = [r.getMessage() for r in grab.records]
        assert any("SIGINT" in m and "中断" in m for m in msgs), msgs
        assert all(r.levelno == logging.WARNING for r in grab.records), msgs

        # 2) shutdown 抛异常也必须照样退出（退出路径不能再炸）
        try:
            _log_stop(int(_signal.SIGINT), FakeSched(boom=True))
        except SystemExit as e:
            assert e.code == 0
        else:
            raise AssertionError("shutdown 失败时也必须 SystemExit")

        # 3) atexit 收尾也要留 WARNING
        grab.records.clear()
        _log_atexit()
        assert grab.records, "atexit 收尾必须写日志"
        last = grab.records[-1]
        assert last.levelno == logging.WARNING and "atexit" in last.getMessage()
    finally:
        lg.removeHandler(grab)
        lg.setLevel(old_level)


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
