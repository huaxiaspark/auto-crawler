import uvicorn

from config_loader import load_config
from logger import setup_logger
import api


if __name__ == "__main__":
    config = load_config("config.yaml")
    setup_logger("main", config["log_dir"])
    setup_logger("api", config["log_dir"])
    setup_logger("pipeline", config["log_dir"])
    setup_logger("processor_runner", config["log_dir"])
    setup_logger("uploader", config["log_dir"])
    setup_logger("platform_notifier", config["log_dir"])

    import logging
    logger = logging.getLogger("main")
    logger.info("processor-service 启动")

    api.init(config)

    server_cfg = config["server"]
    uvicorn.run(
        app=api.app,
        host=server_cfg.get("host", "0.0.0.0"),
        port=server_cfg.get("port", 8080),
        log_config=None,
    )
