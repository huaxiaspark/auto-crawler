import os
import logging
from logging.handlers import RotatingFileHandler


def setup_logger(name: str, log_dir: str, level: str = None) -> logging.Logger:
    """初始化日志，同时输出到控制台和滚动文件。
    level 优先级：参数 > 环境变量 LOG_LEVEL > INFO
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
    else:
        level = level.upper()

    numeric_level = getattr(logging, level, logging.INFO)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(numeric_level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 控制台
    ch = logging.StreamHandler()
    ch.setLevel(numeric_level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # 滚动文件
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{name}.log")
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
