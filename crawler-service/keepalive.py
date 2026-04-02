import logging
import os
import sys

import yaml

import runtime_guard

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", "crawler"))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from crawler.browser import BrowserManager


logger = logging.getLogger(__name__)


def _load_crawler_config(config: dict) -> dict:
    config_path = os.path.abspath(config["crawler"]["config_path"])
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def refresh_session(config: dict):
    """
    刷新目标页面，维持登录态与 cookie 活跃。

    采用独立标签页执行保活，避免打断爬虫当前正在操作的业务页面。
    """
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
        page = browser.get_or_create_keepalive_page(target_url)
        current_url = page.url or ""

        if current_url.startswith(target_url):
            logger.info("[KeepAlive] 刷新保活标签页，url=%s", current_url)
            page.reload(wait_until="domcontentloaded")
        else:
            logger.info("[KeepAlive] 保活标签页不在目标页，重新导航到 %s", target_url)
            page.goto(target_url, wait_until="domcontentloaded")

        logger.info("[KeepAlive] 保活刷新完成，当前页面=%s", page.url)
