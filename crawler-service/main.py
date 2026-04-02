import argparse
import sys
from datetime import date, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config_loader import load_config
from logger import setup_logger
import keepalive
import pipeline
import runtime_guard


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
        keepalive_cfg = config.get("keepalive", {})
        keepalive_enabled = keepalive_cfg.get("enabled", True)
        keepalive_interval_minutes = keepalive_cfg.get("interval_minutes", 15)

        logger.info(
            f"定时日增模式已启动，cron={cron_expr}，timezone={timezone}，"
            "当前由 crawler/config.yaml 的任务级日期偏移规则控制实际取数日期，"
            "等待首次触发..."
        )

        def scheduled_job():
            trigger_date = date.today().strftime("%Y-%m-%d")
            logger.info(f"[定时任务] 触发，触发日期={trigger_date}")
            try:
                with runtime_guard.acquire_crawl_lock(config, mode="scheduled"):
                    pipeline.run(
                        config=config,
                        start=trigger_date,
                        end=trigger_date,
                        tasks=None,
                        scheduled_run=True,
                    )
                logger.info(f"[定时任务] 执行完毕，触发日期={trigger_date}")
            except runtime_guard.CrawlAlreadyRunningError as e:
                logger.warning("[定时任务] 跳过：%s", e)
            except Exception:
                logger.error(
                    "[定时任务] 执行异常，触发日期=%s", trigger_date, exc_info=True,
                )

        def keepalive_job():
            logger.info("[KeepAlive] 触发会话保活任务")
            try:
                keepalive.refresh_session(config)
                logger.info("[KeepAlive] 会话保活任务完成")
            except Exception:
                logger.error("[KeepAlive] 会话保活任务失败", exc_info=True)

        scheduler = BlockingScheduler(timezone=timezone)
        scheduler.add_job(
            scheduled_job,
            CronTrigger.from_crontab(cron_expr, timezone=timezone),
            id="scheduled_pipeline",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        if keepalive_enabled:
            scheduler.add_job(
                keepalive_job,
                IntervalTrigger(minutes=keepalive_interval_minutes, timezone=timezone),
                id="session_keepalive",
                next_run_time=datetime.now(),
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            logger.info(
                "会话保活已启用：每 %s 分钟自动刷新一次目标页面，服务运行期间持续执行",
                keepalive_interval_minutes,
            )
        else:
            logger.info("会话保活已禁用")
        try:
            scheduler.start()
        except KeyboardInterrupt:
            logger.info(
                "crawler-service 收到 KeyboardInterrupt 信号，正在停止调度器"
                "（定时任务 cron=%s，保活任务 enabled=%s）",
                cron_expr, keepalive_enabled,
            )
        except SystemExit as e:
            logger.info(
                "crawler-service 收到 SystemExit 信号（code=%s），正在停止调度器"
                "（定时任务 cron=%s，保活任务 enabled=%s）",
                e.code, cron_expr, keepalive_enabled,
            )
        finally:
            active = runtime_guard.get_active_crawl(config)
            if active:
                logger.warning(
                    "crawler-service 停止时仍有爬虫运行中：mode=%s，pid=%s",
                    active.get("mode"), active.get("pid"),
                )
            else:
                logger.info("crawler-service 已安全停止，无正在运行的爬虫任务")
    else:
        if not args.start or not args.end:
            logger.error("batch 模式必须同时指定 --start 和 --end 参数")
            sys.exit(1)
        tasks = args.task.split(",") if args.task else None
        logger.info(f"手动批量模式，start={args.start}，end={args.end}，tasks={tasks or '全部'}")
        try:
            with runtime_guard.acquire_crawl_lock(config, mode="batch"):
                pipeline.run(
                    config=config,
                    start=args.start,
                    end=args.end,
                    tasks=tasks,
                    scheduled_run=False,
                )
        except runtime_guard.CrawlAlreadyRunningError as e:
            logger.error("batch 模式启动失败：%s", e)
            sys.exit(1)
