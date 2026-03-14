import time
import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


def notify_processor(
    object_name: str,
    md5: str,
    start: str,
    end: str,
    categories: list,
    config: dict,
):
    """通知服务器 B 开始处理，支持重试，409 视为成功。"""
    if not config["notify"].get("enabled", True):
        logger.info("[Step 4] notify.enabled=false，跳过通知服务器 B")
        return

    prefix = config["upload"]["prefix"].lstrip("/")
    payload = {
        "object_name": object_name,
        "download_url": (
            f"{config['upload']['endpoint']}/{config['upload']['bucket']}/{prefix}{object_name}"
        ),
        "md5": md5,
        "date_range": {"start": start, "end": end},
        "categories": categories,
        "timestamp": datetime.now().isoformat(),
    }
    headers = {"X-Secret": config["notify"].get("secret", "")}
    retry_times = config["notify"].get("retry_times", 3)
    retry_interval = config["notify"].get("retry_interval_seconds", 30)
    url = config["notify"]["url"]

    logger.info(
        f"[Step 4] 通知服务器 B，url={url}，object_name={object_name}，categories={categories}"
    )
    logger.debug(f"通知 payload：{payload}")

    for attempt in range(retry_times):
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=payload, headers=headers)
                logger.debug(f"通知响应，status={resp.status_code}，body={resp.text[:200]}")
                if resp.status_code == 409:
                    logger.info("服务器 B 已在处理该任务（幂等），视为通知成功")
                    return
                resp.raise_for_status()
            logger.info(f"[Step 4] 通知服务器 B 成功，object_name={object_name}")
            return
        except Exception as e:
            if attempt < retry_times - 1:
                logger.warning(
                    f"通知服务器 B 失败（第 {attempt + 1}/{retry_times} 次），"
                    f"{retry_interval}s 后重试，错误：{e}"
                )
                time.sleep(retry_interval)
            else:
                logger.error(
                    f"通知服务器 B 失败，已达最大重试次数 {retry_times}，"
                    f"url={url}，错误：{e}",
                    exc_info=True,
                )
