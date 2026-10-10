"""调度层：APScheduler（cron/interval）。

单机场景不引入 Celery/Airflow —— 源数量个位数，重型调度器是过度工程。
无 APScheduler 时降级为"跑一轮就退出"，便于 cron / 任务计划程序调用。
"""
from __future__ import annotations

import logging
import time

from hotwords.pipeline import Pipeline, build_pipeline

logger = logging.getLogger(__name__)


def run_forever(db_path: str = "hotwords.db", once: bool = False) -> None:
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

    sched = BlockingScheduler(timezone="Asia/Shanghai")

    # 每个源一个 job，间隔取自源配置 —— 源之间互不影响
    for src in pipeline.cfg.enabled_sources():
        if src.type != "static_json":
            continue
        sched.add_job(
            _run_one_source,
            trigger=IntervalTrigger(minutes=src.interval_min),
            args=[pipeline, src.id],
            id=f"src_{src.id}",
            name=src.name,
            max_instances=1,          # 禁止同一源并发
            coalesce=True,            # 错过多次只补一次
            misfire_grace_time=60,
            next_run_time=None,       # 启动时不立即跑（先手动跑一轮验证）
        )
        logger.info("已注册调度: %s 每 %d 分钟", src.id, src.interval_min)

    logger.info("调度器启动，Ctrl+C 退出")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


def _run_one_source(pipeline: Pipeline, source_id: str) -> None:
    src = next((s for s in pipeline.cfg.sources if s.id == source_id), None)
    if not src:
        return
    run_id = pipeline.repo.start_run()
    try:
        written, problems = pipeline.run_source(src, run_id)
        pipeline.repo.finish_run(run_id, "ok" if not problems else "partial",
                                 {src.id: written})
    except Exception as e:  # 单个 job 失败不能拖垮调度器
        logger.exception("源 %s 执行异常", source_id)
        pipeline.repo.finish_run(run_id, "failed", {src.id: 0})
        pipeline.repo.add_alert("http_error", src.id, str(e), run_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()
