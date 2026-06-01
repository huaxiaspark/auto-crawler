import os
import sys

import yaml

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", "crawler"))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from config_loader import load_config
from logger import setup_logger
from utils.timing import configure as configure_sleep, sleep_seconds
import keepalive


def _load_crawler_config(keepalive_config: dict) -> dict:
    """加载爬虫的 config.yaml，获取 timing 和 browser 配置。"""
    config_path = os.path.abspath(keepalive_config["crawler"]["config_path"])
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    config = load_config("config.yaml")
    logger = setup_logger("main", config["log_dir"],
                          max_size_mb=config.get("log_max_size_mb", 5))

    # timing 参数统一从爬虫 config.yaml 读取，避免重复维护
    crawler_config = _load_crawler_config(config)
    configure_sleep(crawler_config)

    interval_minutes = config.get("keepalive", {}).get("interval_minutes", 15)
    interval_seconds = interval_minutes * 60

    logger.info("keepalive-service 启动，每 %s 分钟刷新一次（带抖动）", interval_minutes)

    while True:
        try:
            keepalive.refresh_session(config)
            logger.info("[KeepAlive] 会话保活任务完成")
        except Exception:
            logger.error("[KeepAlive] 会话保活任务失败", exc_info=True)
        # 使用带抖动的休眠替代固定 time.sleep，避免风控检测到固定节奏
        sleep_seconds(interval_seconds)
