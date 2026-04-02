import json
import logging
import os
import time
import threading
from contextlib import contextmanager


logger = logging.getLogger(__name__)
LOCK_FILENAME = "crawler-runtime.lock"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 5
DEFAULT_STALE_TIMEOUT_SECONDS = 30


class CrawlAlreadyRunningError(RuntimeError):
    """已有正式爬取任务在执行。"""


def _lock_path(config: dict) -> str:
    runtime_cfg = config.get("runtime_guard", {})
    lock_dir = runtime_cfg.get("lock_dir") or config["log_dir"]
    return os.path.join(os.path.abspath(lock_dir), LOCK_FILENAME)


def _heartbeat_interval_seconds(config: dict) -> int:
    runtime_cfg = config.get("runtime_guard", {})
    return max(1, int(runtime_cfg.get("heartbeat_interval_seconds", DEFAULT_HEARTBEAT_INTERVAL_SECONDS)))


def _stale_timeout_seconds(config: dict) -> int:
    runtime_cfg = config.get("runtime_guard", {})
    return max(
        _heartbeat_interval_seconds(config) + 1,
        int(runtime_cfg.get("stale_timeout_seconds", DEFAULT_STALE_TIMEOUT_SECONDS)),
    )


def _read_lock(lock_path: str):
    if not os.path.exists(lock_path):
        return None
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "pid": None,
            "mode": "unknown",
            "running": False,
            "started_at": None,
            "finished_at": None,
            "heartbeat_at": None,
        }


def _write_lock(lock_path: str, payload: dict):
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


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
    except SystemError:
        # Windows: os.kill(pid, 0) 对无效 PID 会抛出 SystemError
        return False
    return True


def _is_heartbeat_stale(lock_info: dict, stale_timeout_seconds: int) -> bool:
    heartbeat_at = lock_info.get("heartbeat_at") or lock_info.get("started_at")
    if not heartbeat_at:
        return True
    return (time.time() - heartbeat_at) > stale_timeout_seconds


def _remove_stale_lock(lock_path: str, stale_timeout_seconds: int):
    lock_info = _read_lock(lock_path)
    if not lock_info:
        return
    if not lock_info.get("running", False):
        try:
            os.remove(lock_path)
            logger.info("检测到已完成运行锁，已自动清理: %s", lock_path)
        except FileNotFoundError:
            pass
        return
    if _is_heartbeat_stale(lock_info, stale_timeout_seconds):
        try:
            os.remove(lock_path)
            logger.warning("检测到超时运行锁，已自动清理: %s", lock_path)
        except FileNotFoundError:
            pass
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
    stale_timeout_seconds = _stale_timeout_seconds(config)
    _remove_stale_lock(lock_path, stale_timeout_seconds)
    lock_info = _read_lock(lock_path)
    if not lock_info:
        return None
    if not lock_info.get("running", False):
        return None
    if _is_heartbeat_stale(lock_info, stale_timeout_seconds):
        _remove_stale_lock(lock_path, stale_timeout_seconds)
        return None
    pid = lock_info.get("pid")
    if not _pid_exists(pid):
        _remove_stale_lock(lock_path, stale_timeout_seconds)
        return None
    return lock_info


def is_crawl_running(config: dict) -> bool:
    return get_active_crawl(config) is not None


@contextmanager
def acquire_crawl_lock(config: dict, mode: str):
    os.makedirs(config["log_dir"], exist_ok=True)
    lock_path = _lock_path(config)
    stale_timeout_seconds = _stale_timeout_seconds(config)
    heartbeat_interval_seconds = _heartbeat_interval_seconds(config)
    _remove_stale_lock(lock_path, stale_timeout_seconds)

    payload = {
        "pid": os.getpid(),
        "mode": mode,
        "running": True,
        "started_at": int(time.time()),
        "finished_at": None,
        "heartbeat_at": int(time.time()),
    }
    stop_event = threading.Event()

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

        def _heartbeat_loop():
            while not stop_event.wait(heartbeat_interval_seconds):
                try:
                    current = _read_lock(lock_path)
                    if not current or current.get("pid") != payload["pid"] or not current.get("running", False):
                        return
                    current["heartbeat_at"] = int(time.time())
                    _write_lock(lock_path, current)
                except Exception:
                    logger.warning("更新运行锁心跳失败，mode=%s，pid=%s", mode, payload["pid"], exc_info=True)

        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"runtime-guard-heartbeat-{mode}",
            daemon=True,
        )
        heartbeat_thread.start()
        yield
    finally:
        stop_event.set()
        try:
            current = _read_lock(lock_path)
            if current and current.get("pid") == payload["pid"]:
                current["running"] = False
                current["finished_at"] = int(time.time())
                current["heartbeat_at"] = int(time.time())
                _write_lock(lock_path, current)
                logger.info("已更新运行锁为完成状态，mode=%s，pid=%s", mode, payload["pid"])
        except FileNotFoundError:
            pass
