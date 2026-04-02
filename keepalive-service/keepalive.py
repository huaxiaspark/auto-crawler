import logging
import os
import sys
import threading
from contextlib import contextmanager

import yaml

import runtime_guard

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", "crawler"))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from crawler.browser import BrowserManager


logger = logging.getLogger(__name__)

_keepalive_active = threading.local()


class _KeepAliveFilter(logging.Filter):
    """当保活任务活跃时，为所有日志记录添加 [KeepAlive] 前缀。

    通过 thread-local 变量控制，仅在 refresh_session 执行线程中生效，
    避免影响其他线程的日志输出。
    """

    def filter(self, record):
        if getattr(_keepalive_active, "flag", False):
            if not record.msg.startswith("[KeepAlive]"):
                record.msg = f"[KeepAlive] {record.msg}"
        return True


_ka_filter = _KeepAliveFilter()
logging.getLogger().addFilter(_ka_filter)


@contextmanager
def _keepalive_log_context():
    """在此上下文内，当前线程的所有日志自动添加 [KeepAlive] 前缀。"""
    _keepalive_active.flag = True
    try:
        yield
    finally:
        _keepalive_active.flag = False


def _load_crawler_config(config: dict) -> dict:
    config_path = os.path.abspath(config["crawler"]["config_path"])
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def refresh_session(config: dict):
    """
    刷新目标页面，维持登录态与 cookie 活跃。

    采用独立标签页执行保活，避免打断爬虫当前正在操作的业务页面。
    操作浏览器前后均检查爬虫运行状态，避免竞态窗口导致同时操作浏览器。

    执行期间，当前线程所有日志（含 BrowserManager 等子模块）自动添加
    [KeepAlive] 前缀，便于在混合日志中区分保活操作和爬虫操作。
    """
    with _keepalive_log_context():
        active_crawl = runtime_guard.get_active_crawl(config)
        if active_crawl:
            logger.info(
                "[KeepAlive] 检测到正式爬取正在执行，跳过本轮保活刷新，mode=%s，pid=%s",
                active_crawl.get("mode"),
                active_crawl.get("pid"),
            )
            return

        crawler_config = _load_crawler_config(config)
        target_url = crawler_config.get("target_url", "https://pmos.sx.sgcc.com.cn/#/dashboard")

        with BrowserManager(crawler_config) as browser:
            # 二次检查：连接浏览器后、实际操作页面前，再次确认无爬虫在运行
            active_crawl = runtime_guard.get_active_crawl(config)
            if active_crawl:
                logger.info(
                    "[KeepAlive] 连接浏览器后二次检查发现爬虫已启动，放弃本轮保活，"
                    "mode=%s，pid=%s",
                    active_crawl.get("mode"),
                    active_crawl.get("pid"),
                )
                return

            page = browser.get_or_create_keepalive_page(target_url)
            current_url = page.url or ""

            if current_url.startswith(target_url):
                logger.info("[KeepAlive] 刷新保活标签页，url=%s", current_url)
                page.reload(wait_until="domcontentloaded")
            else:
                logger.info("[KeepAlive] 保活标签页不在目标页，重新导航到 %s", target_url)
                page.goto(target_url, wait_until="domcontentloaded")

            logger.info("[KeepAlive] 保活刷新完成，当前页面=%s", page.url)
