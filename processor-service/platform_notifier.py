import time
import httpx
import logging

logger = logging.getLogger(__name__)


def notify_with_retry(platform: dict, payload: dict):
    """通知单个三方平台，支持重试。"""
    if not platform.get("enabled", True):
        logger.info(f"[Step 4] 平台 {platform['name']} enabled=false，跳过")
        return

    retry_times = platform.get("retry_times", 5)
    retry_interval = platform.get("retry_interval_seconds", 30)
    name = platform["name"]
    url = platform["url"]

    logger.info(f"[Step 4] 通知三方平台 {name}，url={url}")
    logger.debug(f"通知 payload：{payload}")

    for attempt in range(retry_times):
        try:
            token = platform.get("token", "")
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            resp = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=30,
            )
            logger.debug(f"平台 {name} 响应，status={resp.status_code}，body={resp.text[:200]}")
            resp.raise_for_status()
            logger.info(f"[Step 4] 通知平台 {name} 成功")
            return
        except Exception as e:
            if attempt < retry_times - 1:
                logger.warning(
                    f"通知平台 {name} 失败（第 {attempt + 1}/{retry_times} 次），"
                    f"{retry_interval}s 后重试，错误：{e}"
                )
                time.sleep(retry_interval)
            else:
                logger.error(
                    f"通知平台 {name} 失败，已达最大重试次数 {retry_times}，"
                    f"url={url}，错误：{e}",
                    exc_info=True,
                )


def notify_all(config: dict, payload: dict):
    """通知所有启用的三方平台，各平台独立执行，互不影响。"""
    platforms = config.get("notify", {}).get("platforms", [])
    for platform in platforms:
        try:
            notify_with_retry(platform=platform, payload=payload)
        except Exception:
            logger.error(f"通知平台 {platform.get('name')} 异常", exc_info=True)
