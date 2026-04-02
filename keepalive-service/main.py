import os
import sys
import time

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", "crawler"))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from config_loader import load_config
from logger import setup_logger
import keepalive


if __name__ == "__main__":
    config = load_config("config.yaml")
    logger = setup_logger("main", config["log_dir"],
                          max_size_mb=config.get("log_max_size_mb", 5))

    interval_minutes = config.get("keepalive", {}).get("interval_minutes", 15)
    interval_seconds = interval_minutes * 60

    logger.info("keepalive-service 启动，每 %s 分钟刷新一次", interval_minutes)

    while True:
        try:
            keepalive.refresh_session(config)
            logger.info("[KeepAlive] 会话保活任务完成")
        except Exception:
            logger.error("[KeepAlive] 会话保活任务失败", exc_info=True)
        time.sleep(interval_seconds)
