"""
日志管理模块
提供统一的日志配置和管理
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler


_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_MAX_MB = 5


class _SizedTimedRotatingFileHandler(TimedRotatingFileHandler):
    """在 TimedRotatingFileHandler 基础上增加单文件大小上限。
    超过 max_bytes 时立即执行一次滚动，文件名追加 _N 序号以避免覆盖当日已有备份。
    """

    def __init__(self, *args, max_bytes: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_bytes = max_bytes
        self._size_rollover_count = 0

    def emit(self, record):
        try:
            if (
                self.max_bytes > 0
                and os.path.exists(self.baseFilename)
                and os.path.getsize(self.baseFilename) >= self.max_bytes
            ):
                self._size_rollover_count += 1
                old = self.baseFilename
                new = f"{old}.{self.suffix_time}_{self._size_rollover_count}"
                if self.stream:
                    self.stream.close()
                    self.stream = None
                os.rename(old, new)
                self.stream = self._open()
        except Exception:
            pass
        super().emit(record)

    @property
    def suffix_time(self):
        import time
        return time.strftime(self.suffix, time.localtime())


def setup_logger(config: dict) -> logging.Logger:
    """
    初始化并配置日志器。同时配置 root logger，确保所有子模块日志均写入文件。

    Args:
        config: 配置字典，logging 子键包含 level、log_dir、backup_count

    Returns:
        配置好的 Logger 实例
    """
    log_config = config.get("logging", {})
    level_str = os.environ.get("LOG_LEVEL") or log_config.get("level", "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)
    log_dir = log_config.get("log_dir", "./logs")
    backup_count = log_config.get("backup_count", 30)
    max_bytes = log_config.get("max_size_mb", _DEFAULT_MAX_MB) * 1024 * 1024

    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(fmt=_LOG_FMT, datefmt=_DATE_FMT)

    # 配置 root logger，覆盖所有子模块
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(level)

        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(formatter)
        root.addHandler(ch)

        log_file = os.path.join(log_dir, "crawler.log")
        fh = _SizedTimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
            max_bytes=max_bytes,
        )
        fh.suffix = "%Y%m%d"
        fh.setLevel(level)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    logger = logging.getLogger("shanxi_power_crawler")
    logger.info("日志系统初始化完成，日志文件: %s", os.path.join(log_dir, "crawler.log"))
    return logger


def get_logger() -> logging.Logger:
    """获取已配置的日志器"""
    return logging.getLogger("shanxi_power_crawler")
