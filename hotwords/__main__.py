"""CLI 入口：python -m hotwords {run,once,report,sources,alerts}

用法：
  python -m hotwords run       # 启动常驻调度（APScheduler）
  python -m hotwords once      # 只跑一轮（推荐先跑这个验证）
  python -m hotwords sources   # 列出已配置的源
  python -m hotwords alerts    # 查看最近告警
  python -m hotwords report    # 查看最近快照概览
"""
from __future__ import annotations

import argparse
import logging
import sys

from hotwords.config import load_config
from hotwords.storage import Repository

LOG_FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def _setup_logging(verbose: bool, log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        from pathlib import Path

        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(p, encoding="utf-8"))

        # 原生崩溃（libcurl 段错误这类）不会产生 Python traceback，
        # 表现就是"日志凭空断掉"，事后无法区分「崩溃」与「被人 Ctrl+C」。
        # 实测踩到：2026-10-11 挂机中途停跑，只能靠与手动 Ctrl+C 的日志
        # 签名逐行比对来推断原因。faulthandler 把 C 层崩溃也落到独立文件，
        # 下次出现"断尾"时先看这里有没有东西即可一眼定性。
        import faulthandler

        crash_path = p.with_name(p.name + ".crash")
        faulthandler.enable(
            file=open(crash_path, "a", encoding="utf-8"),  # noqa: SIM115 - 需常驻
            all_threads=True,
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FMT,
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def cmd_sources(args) -> int:
    cfg = load_config(args.config) if args.config else load_config()
    print(f"{'ID':<16}{'启用':<6}{'类型':<14}{'间隔':<8}{'Parser':<12}名称")
    print("-" * 76)
    for s in cfg.sources:
        print(f"{s.id:<16}{'✓' if s.enabled else '✗':<6}{s.type:<14}"
              f"{str(s.interval_min)+'min':<8}{s.parser:<12}{s.name}")
    return 0


def cmd_alerts(args) -> int:
    repo = Repository(args.db)
    rows = repo.recent_alerts(args.limit)
    if not rows:
        print("暂无告警")
        return 0
    for r in rows:
        print(f"[{r['created_at']}] {r['kind']:<12} {r['source'] or '-':<16} {r['detail']}")
    return 0


def cmd_report(args) -> int:
    repo = Repository(args.db)
    rows = repo.conn.execute(
        """SELECT source, category, COUNT(*) AS n, MAX(fetched_at) AS last_at
           FROM snapshot GROUP BY source, category ORDER BY last_at DESC"""
    ).fetchall()
    if not rows:
        print("暂无数据")
        return 0
    print(f"{'源':<16}{'榜单':<12}{'条数':<8}最近抓取")
    print("-" * 64)
    for r in rows:
        print(f"{r['source']:<16}{r['category']:<12}{r['n']:<8}{r['last_at']}")
    total = repo.conn.execute("SELECT COUNT(*) c FROM word").fetchone()["c"]
    print(f"\n词条去重总数: {total}")
    return 0


def cmd_once(args) -> int:
    from hotwords.pipeline import build_pipeline

    pipeline = build_pipeline(args.db, args.config)
    res = pipeline.run_once()
    print(f"\nrun_id={res['run_id']} status={res['status']}")
    for k, v in res["stats"].items():
        print(f"  {k}: {v} 条")
    if res["problems"]:
        print("\n发现问题:")
        for p in res["problems"]:
            print("  -", p)
    return 0 if res["status"] != "failed" else 1


def cmd_track(args) -> int:
    from hotwords.analysis import load_persistence

    repo = Repository(args.db)
    rows = load_persistence(repo, limit=args.limit, min_streak=args.min_streak)
    if not rows:
        print("暂无追踪数据，先跑一轮：python -m hotwords once")
        return 1
    total_runs = repo.conn.execute(
        "SELECT COUNT(*) c FROM run WHERE status IN ('ok','partial')").fetchone()["c"]
    print(f"共 {total_runs} 轮数据。持续上榜榜（按连续轮数排序）:\n")
    print(f"{'词条':<26}{'连续':<6}{'累计':<6}{'最长':<6}{'最佳':<6}{'源数':<6}{'持续度':<10}来源")
    print("-" * 92)
    for r in rows:
        srcs = "+".join(s.replace("_hot", "") for s in r.sources if s)
        print(f"{r.word[:24]:<26}{r.current_streak:<6}{r.total_rounds:<6}"
              f"{r.max_streak:<6}{r.best_rank:<6}{r.source_count:<6}"
              f"{r.persistence_score:<10.1f}{srcs}")
    return 0


def cmd_retrack(args) -> int:
    from hotwords.analysis import rebuild_track

    repo = Repository(args.db)
    cfg = load_config(args.config) if args.config else load_config()
    n = rebuild_track(
        repo, max_gap_minutes=args.max_gap, exclude=cfg.track_excluded()
    )
    runs = repo.conn.execute(
        "SELECT COUNT(*) c FROM run WHERE status IN ('ok','partial')"
    ).fetchone()["c"]
    excl = cfg.track_excluded()
    if excl:
        listed = ", ".join(f"{s}/{c}" for s, c in sorted(excl))
        print(f"（已排除运营位: {listed}）")
    print(f"已从 {runs} 轮快照重建持续上榜数据，末轮涉及 {n} 个词条。")
    return 0


def cmd_cross(args) -> int:
    from hotwords.analysis import compute_cross_source, load_snapshot_over_runs

    repo = Repository(args.db)
    rows = load_snapshot_over_runs(repo, args.last)
    if not rows:
        print("暂无数据，先跑一轮：python -m hotwords once")
        return 1
    words = compute_cross_source(rows)
    multi = [w for w in words if w.cross_source_count >= 2]
    print(f"词条总数 {len(words)}，其中跨源共现（≥2 源）{len(multi)} 条\n")
    print(f"{'词条':<26}{'源数':<6}{'热度指数':<10}{'最佳名次':<9}来源")
    print("-" * 88)
    for w in words[: args.limit]:
        srcs = "+".join(s.replace("_hot", "") for s in w.sources)
        print(f"{w.word[:24]:<26}{w.cross_source_count:<6}"
              f"{w.heat_index:<10.1f}{w.best_rank:<9}{srcs}")
    return 0


def cmd_trend(args) -> int:
    from hotwords.render import render_dashboard

    repo = Repository(args.db)
    out = render_dashboard(repo, args.out, last_n=args.last)
    print(f"已生成: {out}")
    return 0


def cmd_run(args) -> int:
    from hotwords.scheduler import run_forever

    run_forever(args.db, once=False, interval_min=args.interval)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hotwords", description="浏览器/搜索入口高热度词条采集")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--db", default="hotwords.db", help="SQLite 路径")
    p.add_argument("--config", default=None, help="源配置路径")
    p.add_argument("--log-file", default=None, help="同时把日志追加写入该文件")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("once", help="跑一轮").set_defaults(func=cmd_once)
    sub.add_parser("sources", help="列出源").set_defaults(func=cmd_sources)

    pr = sub.add_parser("run", help="常驻调度（整轮全量）")
    pr.add_argument("--interval", type=int, default=None,
                    help="整轮间隔分钟（默认取各启用源的最大值，即不加速任何源）")
    pr.set_defaults(func=cmd_run)

    pa = sub.add_parser("alerts", help="查看告警")
    pa.add_argument("--limit", type=int, default=20)
    pa.set_defaults(func=cmd_alerts)

    sub.add_parser("report", help="数据概览").set_defaults(func=cmd_report)

    pc = sub.add_parser("cross", help="跨源共现榜")
    pc.add_argument("--limit", type=int, default=30)
    pc.add_argument("--last", type=int, default=0,
                    help="聚合最近 N 轮；0（默认）= 全部轮次")
    pc.set_defaults(func=cmd_cross)

    pk = sub.add_parser("track", help="持续上榜榜")
    pk.add_argument("--limit", type=int, default=30)
    pk.add_argument("--min-streak", type=int, default=1, dest="min_streak")
    pk.set_defaults(func=cmd_track)

    pb = sub.add_parser("retrack", help="从快照全量重建持续上榜数据")
    pb.add_argument("--max-gap", type=int, default=180, dest="max_gap",
                    help="断档阈值（分钟）：相邻两轮间隔超过该值即视为中断")
    pb.set_defaults(func=cmd_retrack)

    pt = sub.add_parser("trend", help="生成趋势可视化 HTML")
    pt.add_argument("--out", default="dashboard.html")
    pt.add_argument("--last", type=int, default=0,
                    help="聚合最近 N 轮；0（默认）= 全部轮次")
    pt.set_defaults(func=cmd_trend)

    args = p.parse_args(argv)
    _setup_logging(args.verbose, args.log_file)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
