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
