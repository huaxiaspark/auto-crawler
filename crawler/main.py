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

    # 仅验证已有数据质量
    python main.py --validate

    # 定时调度模式
    python main.py --schedule
"""

import argparse
import os
import sys
import time
from datetime import datetime

import yaml

from crawler.browser import BrowserManager
from crawler.page_crawler import PageCrawler
from storage.csv_storage import CsvStorage
from utils.logger import setup_logger, get_logger
from utils.parser import parse_tieline_batch_file
from utils.validator import DataValidator, validate_csv_file


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    if not os.path.exists(config_path):
        print(f"错误：配置文件不存在: {config_path}")
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
                print(f"警告：未找到任务「{name}」，可用任务: {', '.join(all_tasks.keys())}")
        return filtered

    # 返回所有启用的任务
    return {name: cfg for name, cfg in all_tasks.items() if cfg.get("enabled", True)}


def run_crawler(config: dict, tasks: dict, start_date: str, end_date: str,
                tieline_batch_queries=None):
    """
    执行爬虫主流程

    Args:
        config: 全局配置
        tasks: 要执行的任务字典
        start_date: 起始日期
        end_date: 结束日期
        tieline_batch_queries: 仅当任务含「日前联络线计划」时有效；
            若不为 None，则为 [(start_date, end_date, 联络线名称), ...]，
            该任务将按此列表执行批量查询，不再使用 start_date/end_date 与全部下拉选项。
    """
    logger = get_logger()
    target_url = config.get("target_url", "https://pmos.sx.sgcc.com.cn/#/dashboard")

    logger.info("=" * 70)
    logger.info("山西电力交易平台爬虫 启动")
    logger.info("目标: %s", target_url)
    logger.info("日期: %s ~ %s", start_date, end_date)
    logger.info("任务: %d 个 (%s)", len(tasks), ", ".join(tasks.keys()))
    if tieline_batch_queries:
        logger.info("日前联络线计划: 使用批量查询文件，共 %d 条", len(tieline_batch_queries))
    logger.info("=" * 70)

    with BrowserManager(config) as browser:
        mode = config.get("browser", {}).get("mode", "connect")

        if mode == "connect":
            # connect 模式：Chrome 已打开且已登录，直接操作现有页面
            logger.info("已连接到现有页面: %s", browser.page.url)
        else:
            # launch 模式：启动新浏览器，需导航到目标网站
            browser.navigate(target_url)
            time.sleep(3)

        # 创建页面爬取器
        page_crawler = PageCrawler(browser.page, config)

        # 等待侧边栏菜单渲染完成（Vue 动态加载，首次需要额外等待）
        page_crawler.navigator.wait_for_sidebar_ready()

        # 逐任务执行
        for task_name, task_config in tasks.items():
            try:
                batch_queries = None
                if task_name == "日前联络线计划" and tieline_batch_queries:
                    batch_queries = tieline_batch_queries
                page_crawler.crawl_task(
                    task_name=task_name,
                    task_config=task_config,
                    start_date=start_date,
                    end_date=end_date,
                    batch_queries=batch_queries,
                )
            except KeyboardInterrupt:
                logger.warning("用户中断，停止爬取")
                break
            except Exception as e:
                logger.error("任务「%s」执行失败: %s", task_name, e, exc_info=True)
                continue

    logger.info("所有任务执行完毕")


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

    按配置的间隔循环执行爬取
    """
    import schedule as sched_module

    logger = get_logger()
    interval = config.get("schedule", {}).get("interval_hours", 24)

    logger.info("定时调度模式已启动，间隔: %d 小时", interval)

    def job():
        # 每次调度时更新 end_date 为当天
        current_end = datetime.now().strftime("%Y-%m-%d")
        logger.info("定时任务触发，结束日期更新为: %s", current_end)
        run_crawler(config, tasks, start_date, current_end)

    # 立即执行一次
    job()

    # 设置定时
    sched_module.every(interval).hours.do(job)

    logger.info("等待下次调度...")
    try:
        while True:
            sched_module.run_pending()
            time.sleep(60)
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
  python main.py --task 日前联络线计划 --batch-file tieline_batch.txt  # 日前联络线计划按文件批量查询
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
    parser.add_argument("--batch-file", default=None,
                        help="日前联络线计划专用：从文件读取「日期+筛选条件」批量执行，每行 日期,联络线名称 或 开始日期,结束日期,联络线名称")

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 初始化日志
    setup_logger(config)
    logger = get_logger()

    # 列出任务
    if args.list_tasks:
        all_tasks = config.get("tasks", {})
        print("\n可用爬取任务:")
        print("-" * 60)
        for name, cfg in all_tasks.items():
            status = "启用" if cfg.get("enabled", True) else "禁用"
            category = cfg.get("category", "")
            print(f"  [{status}] {category} > {name}")
        print(f"\n共 {len(all_tasks)} 个任务")
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

    # 日前联络线计划：解析批量文件（仅当指定了该任务且提供了 --batch-file 时生效）
    tieline_batch_queries = None
    if args.batch_file:
        if "日前联络线计划" not in tasks:
            logger.warning("--batch-file 仅对任务「日前联络线计划」有效，当前未指定该任务，已忽略")
        else:
            if not os.path.isfile(args.batch_file):
                logger.error("批量文件不存在: %s", args.batch_file)
                sys.exit(1)
            tieline_batch_queries = parse_tieline_batch_file(args.batch_file)
            if not tieline_batch_queries:
                logger.error("批量文件未解析出有效记录: %s", args.batch_file)
                sys.exit(1)
            logger.info("已从 %s 解析出 %d 条日前联络线计划查询", args.batch_file, len(tieline_batch_queries))

    # 验证日期
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
        run_crawler(config, tasks, start_date, end_date, tieline_batch_queries=tieline_batch_queries)


if __name__ == "__main__":
    main()
