"""
日志管理模块
提供统一的日志配置和管理
"""

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler


def setup_logger(config: dict) -> logging.Logger:
    """
    初始化并配置日志器

    Args:
        config: 日志配置字典，包含 level, log_dir, max_size_mb, backup_count

    Returns:
        配置好的 Logger 实例
    """
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
    log_dir = log_config.get("log_dir", "./logs")
    max_size = log_config.get("max_size_mb", 10) * 1024 * 1024
    backup_count = log_config.get("backup_count", 5)

    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("shanxi_power_crawler")
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler（按大小滚动）
    log_file = os.path.join(
        log_dir, f"crawler_{datetime.now().strftime('%Y%m%d')}.log"
    )
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info("日志系统初始化完成，日志文件: %s", log_file)
    return logger


def get_logger() -> logging.Logger:
    """获取已配置的日志器"""
    return logging.getLogger("shanxi_power_crawler")
