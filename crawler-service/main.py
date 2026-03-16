import argparse
import sys
from datetime import date, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config_loader import load_config
from logger import setup_logger
import pipeline


def parse_args():
    parser = argparse.ArgumentParser(description="crawler-service 主入口")
    parser.add_argument("--mode", choices=["scheduled", "batch"], default="scheduled")
    parser.add_argument("--start", help="批量模式起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="批量模式结束日期 YYYY-MM-DD")
    parser.add_argument("--task", help="批量模式任务名，逗号分隔；不指定则爬取全部")
    return parser.parse_args()


if __name__ == "__main__":
    config = load_config("config.yaml")
    logger = setup_logger("main", config["log_dir"], max_size_mb=config.get("log_max_size_mb", 5))
    args = parse_args()

    logger.info(f"crawler-service 启动，mode={args.mode}")

    if args.mode == "scheduled":
        sched_cfg = config["schedule"]
        cron_expr = sched_cfg.get("cron", "30 8 * * *")
        timezone = sched_cfg.get("timezone", "Asia/Shanghai")
        offset_days = sched_cfg.get("date_offset_days", -1)

        logger.info(
            f"定时日增模式已启动，cron={cron_expr}，timezone={timezone}，"
            f"date_offset_days={offset_days}，等待首次触发..."
        )

        def scheduled_job():
            target = (date.today() + timedelta(days=offset_days)).strftime("%Y-%m-%d")
            logger.info(f"定时任务触发，目标日期={target}")
            pipeline.run(config=config, start=target, end=target, tasks=None)

        scheduler = BlockingScheduler(timezone=timezone)
        scheduler.add_job(scheduled_job, CronTrigger.from_crontab(cron_expr, timezone=timezone))
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("crawler-service 已停止")
    else:
        if not args.start or not args.end:
            logger.error("batch 模式必须同时指定 --start 和 --end 参数")
            sys.exit(1)
        tasks = args.task.split(",") if args.task else None
        logger.info(f"手动批量模式，start={args.start}，end={args.end}，tasks={tasks or '全部'}")
        pipeline.run(config=config, start=args.start, end=args.end, tasks=tasks)
