import json
import logging
import os
import time
from contextlib import contextmanager


logger = logging.getLogger(__name__)
LOCK_FILENAME = "crawler-runtime.lock"


class CrawlAlreadyRunningError(RuntimeError):
    """已有正式爬取任务在执行。"""


def _lock_path(config: dict) -> str:
    return os.path.join(os.path.abspath(config["log_dir"]), LOCK_FILENAME)


def _read_lock(lock_path: str):
    if not os.path.exists(lock_path):
        return None
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"pid": None, "mode": "unknown", "started_at": None}


def _pid_exists(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _remove_stale_lock(lock_path: str):
    lock_info = _read_lock(lock_path)
    if not lock_info:
        return
    pid = lock_info.get("pid")
    if _pid_exists(pid):
        return
    try:
        os.remove(lock_path)
        logger.warning("检测到陈旧运行锁，已自动清理: %s", lock_path)
    except FileNotFoundError:
        pass


def get_active_crawl(config: dict):
    lock_path = _lock_path(config)
    _remove_stale_lock(lock_path)
    lock_info = _read_lock(lock_path)
    if not lock_info:
        return None
    pid = lock_info.get("pid")
    if not _pid_exists(pid):
        _remove_stale_lock(lock_path)
        return None
    return lock_info


def is_crawl_running(config: dict) -> bool:
    return get_active_crawl(config) is not None


@contextmanager
def acquire_crawl_lock(config: dict, mode: str):
    os.makedirs(config["log_dir"], exist_ok=True)
    lock_path = _lock_path(config)
    _remove_stale_lock(lock_path)

    payload = {
        "pid": os.getpid(),
        "mode": mode,
        "started_at": int(time.time()),
    }

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        lock_info = get_active_crawl(config) or {"mode": "unknown", "pid": "unknown"}
        raise CrawlAlreadyRunningError(
            f"已有爬取任务正在执行，mode={lock_info.get('mode')}，pid={lock_info.get('pid')}"
        )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        logger.info("已获取运行锁，mode=%s，pid=%s", mode, payload["pid"])
        yield
    finally:
        try:
            current = _read_lock(lock_path)
            if current and current.get("pid") == payload["pid"]:
                os.remove(lock_path)
                logger.info("已释放运行锁，mode=%s，pid=%s", mode, payload["pid"])
        except FileNotFoundError:
            pass
