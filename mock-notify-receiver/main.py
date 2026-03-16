import logging

import uvicorn

from config_loader import load_config
from logger import setup_logger
import api


if __name__ == "__main__":
    config = load_config()
    log_dir = config.get("log_dir", "./logs")
    setup_logger("main", log_dir, max_size_mb=config.get("log_max_size_mb", 5))
    setup_logger("api", log_dir, max_size_mb=config.get("log_max_size_mb", 5))

    logger = logging.getLogger("main")
    logger.info("mock-notify-receiver 启动")

    api.init(config)

    server = config["server"]
    uvicorn.run(
        app=api.app,
        host=server.get("host", "0.0.0.0"),
        port=server.get("port", 8401),
        log_config=None,
    )
