import os
import logging
import time
from logging.handlers import TimedRotatingFileHandler


_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_MAX_MB = 5


class _SizedTimedRotatingFileHandler(TimedRotatingFileHandler):
    """在 TimedRotatingFileHandler 基础上增加单文件大小上限。"""

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
                new = f"{self.baseFilename}.{time.strftime(self.suffix, time.localtime())}_{self._size_rollover_count}"
                if self.stream:
                    self.stream.close()
                    self.stream = None
                os.rename(self.baseFilename, new)
                self.stream = self._open()
        except Exception:
            pass
        super().emit(record)


def setup_logger(name: str, log_dir: str, level: str = None, max_size_mb: int = _DEFAULT_MAX_MB) -> logging.Logger:
    """初始化日志，同时输出到控制台和按日期滚动的文件（单文件上限可配置）。
    同时配置 root logger，确保所有子模块日志均写入文件。
    level 优先级：参数 > 环境变量 LOG_LEVEL > INFO
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
    else:
        level = level.upper()

    numeric_level = getattr(logging, level, logging.INFO)
    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(numeric_level)

        ch = logging.StreamHandler()
        ch.setLevel(numeric_level)
        ch.setFormatter(formatter)
        root.addHandler(ch)

        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{name}.log")
        fh = _SizedTimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
            max_bytes=max_size_mb * 1024 * 1024,
        )
        fh.suffix = "%Y%m%d"
        fh.setLevel(numeric_level)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    return logging.getLogger(name)

