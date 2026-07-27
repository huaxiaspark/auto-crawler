#!/usr/bin/env python3
"""
山西电力交易平台爬虫 - 主入口

功能：
- 爬取山西电力交易平台电力现货市场信息披露数据
- 支持按任务/日期/筛选条件批量爬取
- 支持导出和表格解析双模式
- 支持增量更新和定时调度

使用方式：
    # 爬取所有已启用任务
    python main.py

    # 爬取指定任务
    python main.py --task 日前备用总量

    # 指定日期范围
    python main.py --start 2025-06-01 --end 2025-06-30

    # 使用自定义配置文件
    python main.py --config my_config.yaml

    # 从数据校验缺失文件批量补充下载
    python main.py --loss-file ../data-verify/loss.txt

    # 仅验证已有数据质量
    python main.py --validate

    # 定时调度模式
    python main.py --schedule
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import yaml

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from common.task_date_policy import build_task_schedule_ranges
from crawler.browser import BrowserManager
from crawler.page_crawler import PageCrawler
from storage.csv_storage import CsvStorage
from utils.logger import setup_logger, get_logger
from utils.parser import parse_loss_file
from utils.timing import configure as configure_sleep, sleep as ui_sleep, sleep_seconds
from utils.validator import DataValidator, validate_csv_file


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    if not os.path.exists(config_path):
        logging.error("配置文件不存在: %s", config_path)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


def get_date_range(config: dict, args) -> tuple:
    """
    确定日期范围

    优先使用命令行参数，否则使用配置文件
    """
    date_config = config.get("date_range", {})

    start_date = args.start if args.start else date_config.get("start_date", "2025-01-01")
    end_date = args.end if args.end else date_config.get("end_date", "")

    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    return start_date, end_date


def get_enabled_tasks(config: dict, task_filter: str = None) -> dict:
    """
    获取要执行的任务列表

    Args:
        config: 配置字典
        task_filter: 命令行指定的任务名称（可选）

    Returns:
        任务配置字典
    """
    all_tasks = config.get("tasks", {})

    if task_filter:
        # 支持逗号分隔的多任务
        filter_names = [n.strip() for n in task_filter.split(",")]
        filtered = {}
        for name in filter_names:
            if name in all_tasks:
                filtered[name] = all_tasks[name]
            else:
                logging.warning("警告：未找到任务「%s」，可用任务: %s", name, ', '.join(all_tasks.keys()))
        return filtered

    # 返回所有启用的任务
    return {
        name: cfg
        for name, cfg in all_tasks.items()
        if cfg.get("enabled", True)
    }


def run_crawler(config: dict, tasks: dict, start_date: str, end_date: str,
                loss_queries=None, task_date_ranges=None,
                apply_schedule_offsets: bool = False):
    """
    执行爬虫主流程

    Args:
        config: 全局配置
        tasks: 要执行的任务字典
        start_date: 起始日期
        end_date: 结束日期
        loss_queries: 由 loss.txt 解析的缺失数据字典，格式为
            {任务名称: [(日期, 通道名称或None), ...]}，用于补充下载缺失文件。
        task_date_ranges: 可选的任务日期范围覆盖，格式为
            {任务名称: (start_date, end_date)}。定时调度模式下用于按任务应用日期偏移。
        apply_schedule_offsets: 为 True 时，按 config.schedule 中的任务偏移规则
            基于传入的 start_date/end_date 为每个任务计算实际日期范围。
    """
    logger = get_logger()
    target_url = config.get("target_url", "https://pmos.sx.sgcc.com.cn/#/dashboard")
    schedule_cfg = config.get("schedule", {})

    if apply_schedule_offsets and not loss_queries and not task_date_ranges:
        trigger_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        task_date_ranges = build_task_schedule_ranges(
            tasks=tasks,
            start_date=start_date,
            end_date=end_date,
            schedule_cfg=schedule_cfg,
            today=trigger_date,
        )

    logger.info("=" * 70)
    logger.info("山西电力交易平台爬虫 启动")
    logger.info("目标: %s", target_url)
    logger.info("日期: %s ~ %s", start_date, end_date)
    logger.info("任务: %d 个 (%s)", len(tasks), ", ".join(tasks.keys()))
    if loss_queries:
        total_loss = sum(len(v) for v in loss_queries.values())
        logger.info("缺失补充模式: 共 %d 个任务，%d 条记录", len(loss_queries), total_loss)
    logger.info("=" * 70)

    with BrowserManager(config) as browser:
        mode = config.get("browser", {}).get("mode", "connect")

        if mode == "connect":
            # connect 模式：Chrome 已打开且已登录，直接操作现有页面
            logger.info("已连接到现有页面: %s", browser.page.url)
        else:
            # launch 模式：启动新浏览器，需导航到目标网站
            browser.navigate(target_url)
            ui_sleep("xlong")

        # 创建页面爬取器
        page_crawler = PageCrawler(browser.page, config)

        # 等待侧边栏菜单渲染完成（Vue 动态加载，首次需要额外等待）
        page_crawler.navigator.wait_for_sidebar_ready()

        # 逐任务执行
        task_names = list(tasks.keys())
        total_tasks = len(task_names)
        interrupted = False

        for task_idx, (task_name, task_config) in enumerate(tasks.items(), 1):
            try:
                batch_queries = None
                task_date_list = None
                task_start_date, task_end_date = start_date, end_date

                if task_date_ranges and task_name in task_date_ranges:
                    task_start_date, task_end_date = task_date_ranges[task_name]
                    logger.info(
                        "任务「%s」使用独立日期范围: %s ~ %s",
                        task_name, task_start_date, task_end_date
                    )

                if loss_queries and task_name in loss_queries:
                    # 缺失补充模式：从 loss_queries 中提取该任务的查询参数
                    entries = loss_queries[task_name]
                    if task_config.get("dropdown_skip_none", False):
                        # 含下拉筛选且跳过「不选」的任务：
                        # - 有具体通道名的 → batch_queries（精确补爬）
                        # - channel=None（META_MISSING）→ date_list（全量重爬该日期）
                        batch_queries = [(d, d, ch) for d, ch in entries if ch]
                        meta_missing_dates = sorted(set(
                            d for d, ch in entries if not ch
                        ))
                        if meta_missing_dates:
                            logger.info(
                                "任务「%s」有 %d 个日期需全量重爬（元数据缺失）: %s",
                                task_name, len(meta_missing_dates),
                                ", ".join(meta_missing_dates[:5])
                                + ("..." if len(meta_missing_dates) > 5 else ""),
                            )
                        if not batch_queries and not meta_missing_dates:
                            logger.warning("任务「%s」在 loss 文件中无有效筛选选项记录，跳过", task_name)
                            continue
                        # 先执行精确补爬
                        if batch_queries:
                            page_crawler.crawl_task(
                                task_name=task_name,
                                task_config=task_config,
                                start_date=task_start_date,
                                end_date=task_end_date,
                                batch_queries=batch_queries,
                            )
                        # 再执行元数据缺失日期的全量重爬
                        if meta_missing_dates:
                            page_crawler.crawl_task(
                                task_name=task_name,
                                task_config=task_config,
                                start_date=task_start_date,
                                end_date=task_end_date,
                                date_list=meta_missing_dates,
                            )
                        continue
                    else:
                        # 无通道名称：仅按日期列表爬取
                        task_date_list = sorted(set(d for d, _ in entries))
                        if not task_date_list:
                            logger.warning("任务「%s」在 loss 文件中无有效日期记录，跳过", task_name)
                            continue

                page_crawler.crawl_task(
                    task_name=task_name,
                    task_config=task_config,
                    start_date=task_start_date,
                    end_date=task_end_date,
                    batch_queries=batch_queries,
                    date_list=task_date_list,
                )
            except KeyboardInterrupt:
                logger.warning(
                    "用户中断（KeyboardInterrupt），停止爬取。"
                    "当前任务：「%s」（%d/%d），日期范围：%s ~ %s",
                    task_name, task_idx, total_tasks,
                    task_start_date, task_end_date,
                )
                interrupted = True
                break
            except Exception as e:
                logger.error(
                    "任务「%s」（%d/%d）执行失败，日期范围 %s ~ %s: %s",
                    task_name, task_idx, total_tasks,
                    task_start_date, task_end_date, e,
                    exc_info=True,
                )
                continue

    if interrupted:
        logger.warning("爬虫因用户中断提前退出，已完成 %d/%d 个任务", task_idx - 1, total_tasks)
        sys.exit(130)
    else:
        logger.info("所有任务执行完毕（共 %d 个任务）", total_tasks)


def run_validation(config: dict):
    """
    执行数据质量校验

    遍历所有已存储的 CSV 文件进行质量检查
    """
    logger = get_logger()
    output_dir = config.get("storage", {}).get("output_dir", "./data")

    logger.info("=" * 70)
    logger.info("开始数据质量校验")
    logger.info("数据目录: %s", output_dir)
    logger.info("=" * 70)

    total_files = 0
    passed_files = 0
    failed_files = 0

    for root, dirs, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".csv"):
                filepath = os.path.join(root, f)
                total_files += 1

                passed, report = validate_csv_file(filepath)
                if passed:
                    passed_files += 1
                    logger.info("[PASS] %s - %s", f, report)
                else:
                    failed_files += 1
                    logger.warning("[FAIL] %s - %s", f, report)

    logger.info("=" * 70)
    logger.info("校验完成: 共 %d 个文件, 通过 %d, 失败 %d",
                total_files, passed_files, failed_files)
    logger.info("=" * 70)


def run_schedule(config: dict, tasks: dict, start_date: str, end_date: str):
    """
    定时调度模式

    支持两种调度方式：
    - schedule.time 不为空：每天在指定时刻（如 "08:30"）触发
    - schedule.time 为空：立即执行一次，然后每隔 interval_hours 小时重复

    date_mode 控制每次调度的基准日期范围：
    - "yesterday"：仅爬昨天（start=yesterday, end=yesterday）
    - "today"：仅爬今天（start=today, end=today）
    - "tomorrow"：仅爬明天（start=tomorrow, end=tomorrow）
    - "range"：使用传入的 start_date/end_date，end_date 每次更新为当天

    当 schedule.use_task_date_offsets=true 时，
    每个任务会在上述基准日期范围之上继续叠加任务级偏移：
    - task.schedule_date_offset_days = 0：抓基准日
    - task.schedule_date_offset_days = 1：抓基准日+1
    - 未单独配置的任务使用 schedule.default_task_date_offset_days
    """
    import schedule as sched_module

    logger = get_logger()
    sched_cfg = config.get("schedule", {})
    trigger_time = sched_cfg.get("time", "")
    interval = sched_cfg.get("interval_hours", 24)
    date_mode = sched_cfg.get("date_mode", "yesterday")

    def job():
        today = datetime.now().date()
        task_date_ranges = build_task_schedule_ranges(
            tasks=tasks,
            start_date=start_date,
            end_date=end_date,
            schedule_cfg=sched_cfg,
            today=today,
        )

        unique_ranges = sorted(set(task_date_ranges.values()))
        if len(unique_ranges) == 1:
            job_start, job_end = unique_ranges[0]
            logger.info("定时任务触发，任务日期范围统一为: %s ~ %s", job_start, job_end)
        else:
            logger.info("定时任务触发，任务日期范围已按配置拆分:")
            for task_name, (task_start, task_end) in task_date_ranges.items():
                logger.info("  - %s: %s ~ %s", task_name, task_start, task_end)

        run_crawler(
            config,
            tasks,
            start_date,
            end_date,
            task_date_ranges=task_date_ranges,
        )

    if trigger_time:
        logger.info("定时调度模式已启动，每天 %s 触发，date_mode=%s", trigger_time, date_mode)
        sched_module.every().day.at(trigger_time).do(job)
        logger.info("等待首次触发（%s）...", trigger_time)
    else:
        logger.info("定时调度模式已启动，间隔: %d 小时，date_mode=%s", interval, date_mode)
        job()  # 立即执行一次
        sched_module.every(interval).hours.do(job)
        logger.info("等待下次调度...")

    try:
        while True:
            sched_module.run_pending()
            sleep_seconds(60)
    except KeyboardInterrupt:
        logger.info("调度已停止")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="山西电力交易平台爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python main.py                              # 爬取所有已启用任务
  python main.py --task 日前备用总量           # 爬取指定任务
  python main.py --task "日前备用总量,断面约束" # 爬取多个任务
  python main.py --start 2025-06-01 --end 2025-06-30  # 指定日期范围
  python main.py --output-dir /path/to/data           # 指定数据输出根目录
  python main.py --loss-file ../data-verify/loss.txt  # 从校验缺失文件批量补充下载
  python main.py --validate                   # 仅验证数据质量
  python main.py --schedule                   # 定时调度模式
  python main.py --list-tasks                 # 列出所有可用任务
        """,
    )

    parser.add_argument("--config", default="config.yaml",
                        help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("--task", default=None,
                        help="指定爬取任务名称（逗号分隔多个）")
    parser.add_argument("--start", default=None,
                        help="起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", default=None,
                        help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--validate", action="store_true",
                        help="仅执行数据质量校验")
    parser.add_argument("--schedule", action="store_true",
                        help="以定时调度模式运行")
    parser.add_argument("--list-tasks", action="store_true",
                        help="列出所有可用任务")
    parser.add_argument("--loss-file", default=None,
                        help="从数据校验生成的缺失文件列表（loss.txt）批量补充下载，格式：名称,日期[,通道名称]")
    parser.add_argument("--output-dir", default=None,
                        help="数据输出根目录，覆盖配置中的 storage.output_dir，"
                             "导出下载目录同步指向该目录下的 exports 子目录")
    parser.add_argument("--scheduled-run", action="store_true",
                        help="按 schedule 配置将传入日期视为调度触发日期范围，并对任务应用日期偏移")

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 命令行指定输出目录时，覆盖配置中的存储目录与导出下载目录
    if args.output_dir:
        output_dir = os.path.abspath(os.path.expanduser(args.output_dir))
        config.setdefault("storage", {})["output_dir"] = output_dir
        config.setdefault("browser", {})["download_dir"] = os.path.join(output_dir, "exports")

    # 初始化日志
    setup_logger(config)
    logger = get_logger()

    # 初始化短 sleep 分档（让 utils.timing.sleep 读取本次配置）
    configure_sleep(config)

    # 列出任务
    if args.list_tasks:
        all_tasks = config.get("tasks", {})
        logger.info("可用爬取任务:")
        logger.info("-" * 60)
        for name, cfg in all_tasks.items():
            status = "启用" if cfg.get("enabled", True) else "禁用"
            category = cfg.get("category", "")
            logger.info("  [%s] %s > %s", status, category, name)
        logger.info("共 %d 个任务", len(all_tasks))
        return

    # 仅校验模式
    if args.validate:
        run_validation(config)
        return

    # 获取任务和日期范围
    tasks = get_enabled_tasks(config, args.task)
    if not tasks:
        logger.error("没有要执行的任务，请检查配置或 --task 参数")
        sys.exit(1)

    start_date, end_date = get_date_range(config, args)

    # 缺失补充模式：解析 loss.txt，自动按任务分组批量下载
    loss_queries = None
    if args.loss_file:
        if not os.path.isfile(args.loss_file):
            logger.error("缺失文件不存在: %s", args.loss_file)
            sys.exit(1)
        loss_queries = parse_loss_file(args.loss_file)
        if not loss_queries:
            logger.error("缺失文件未解析出有效记录: %s", args.loss_file)
            sys.exit(1)
        # 仅保留当前 tasks 中存在的任务
        unknown = [t for t in loss_queries if t not in tasks]
        if unknown:
            logger.warning("loss 文件中以下任务在配置中不存在，将被忽略: %s", ", ".join(unknown))
        loss_queries = {t: v for t, v in loss_queries.items() if t in tasks}
        if not loss_queries:
            logger.error("loss 文件中无可执行的任务（均不在配置中）")
            sys.exit(1)
        # 将 tasks 限定为 loss 文件中涉及的任务
        tasks = {t: tasks[t] for t in loss_queries}
        total_loss = sum(len(v) for v in loss_queries.values())
        logger.info("已从 %s 解析出 %d 个任务，共 %d 条缺失记录", args.loss_file, len(loss_queries), total_loss)
        # 从 loss 文件中推导实际日期范围，用于日志展示
        all_dates = [d for entries in loss_queries.values() for d, _ in entries]
        if all_dates:
            start_date = min(all_dates)
            end_date = max(all_dates)

    # 验证日期（loss 模式下日期来自文件，跳过范围验证）
    if not loss_queries:
        try:
            s = datetime.strptime(start_date, "%Y-%m-%d")
            e = datetime.strptime(end_date, "%Y-%m-%d")
            if s > e:
                logger.error("起始日期 (%s) 不能晚于结束日期 (%s)", start_date, end_date)
                sys.exit(1)
        except ValueError as ve:
            logger.error("日期格式错误: %s", ve)
            sys.exit(1)

    # 执行
    if args.schedule:
        run_schedule(config, tasks, start_date, end_date)
    else:
        run_crawler(config, tasks, start_date, end_date,
                    loss_queries=loss_queries,
                    apply_schedule_offsets=args.scheduled_run)


if __name__ == "__main__":
    main()
