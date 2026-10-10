"""调度层：APScheduler（interval）。

⚠️ 为什么是「整轮全量」而不是「每源一个 job」——这是踩出来的：
   持续上榜追踪（word_track）以「完整一轮快照」为单位。
   track_persistence(run_id) 会把本轮 run 里没出现的词判为断档（streak 归零）。
   若每个源各自成 run，其它源的词条会被反复误判为断档，数据直接毁掉。
   而且每源独立 job 时根本无从触发 track_persistence（没有「轮」的边界）。
   所以调度必须保证：一个 run = 一次全量采集。

   代价：失去「快源快抓」的粒度，换来 streak 语义正确。
   整轮间隔默认取各启用源 interval 的【最大值】，即不对任何源加速
   （微博/抖音/头条当初是为合规主动降频到 10min 的，不能被整轮调度带回 5min）。
   需要更快时用 `--interval N` 自行覆盖，由使用者承担节奏风险。

单机场景不引入 Celery/Airflow —— 源数量个位数，重型调度器是过度工程。
无 APScheduler 时降级为「跑一轮就退出」，便于 cron / 任务计划程序调用。
"""
from __future__ import annotations

import logging

from hotwords.pipeline import Pipeline, build_pipeline

logger = logging.getLogger(__name__)


def run_forever(
    db_path: str = "hotwords.db",
    once: bool = False,
    interval_min: int | None = None,
) -> None:
    """常驻调度：每 interval 分钟跑一轮全量采集。

    interval_min 为 None 时取各启用源 interval 的最大值（不加速任何源）。
    """
    pipeline = build_pipeline(db_path)

    if once:
        pipeline.run_once()
        return

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning("未安装 APScheduler，降级为单次执行（请用 cron / 任务计划程序调度）")
        pipeline.run_once()
        return

    static_srcs = [s for s in pipeline.cfg.enabled_sources() if s.type == "static_json"]
    if not static_srcs:
        logger.error("没有启用的 static_json 源，调度器退出")
        return

    iv = int(interval_min) if interval_min else max(s.interval_min for s in static_srcs)

    sched = BlockingScheduler(timezone="Asia/Shanghai")
    sched.add_job(
        _run_full_round,
        trigger=IntervalTrigger(minutes=iv),
        args=[pipeline],
        id="full_round",
        name="全量采集一轮",
        max_instances=1,          # 上一轮没跑完则跳过本次触发，不并发
        coalesce=True,            # 错过多次只补一次
        misfire_grace_time=120,
    )
    logger.info(
        "调度器启动：每 %d 分钟跑一轮全量（%d 个源：%s）。Ctrl+C 退出。",
        iv, len(static_srcs), ", ".join(s.id for s in static_srcs),
    )
    logger.info("首轮将在 %d 分钟后触发（不立即跑，先手动验证一轮）", iv)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


def _run_full_round(pipeline: Pipeline) -> None:
    """一轮全量采集。

    run_once 内部已完成：抓取 -> 解析 -> 断言 -> 落库 -> 告警 -> 持续上榜追踪。
    这里刻意不捕获异常 —— 交给 APScheduler 记录 traceback 并继续下一轮调度。
    否则 APScheduler 会打出 "Job executed successfully"，把失败伪装成成功
    （实测踩到：库里一轮没涨，日志却说成功）。
    """
    res = pipeline.run_once()
    logger.info("整轮完成: status=%s stats=%s", res["status"], res["stats"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()
