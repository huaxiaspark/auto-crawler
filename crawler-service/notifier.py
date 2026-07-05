import time
import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


def _notify_one(
    target: dict,
    payload: dict,
    default_retry_times: int,
    default_retry_interval: int,
):
    """通知单个下游服务，支持重试，409 视为成功。

    失败仅记录日志、不向上抛出，确保单个目标失败不影响其余目标。
    """
    name = target.get("name") or target.get("url", "<未命名>")

    if not target.get("enabled", True):
        logger.info(f"[Step 4] 通知目标「{name}」enabled=false，跳过")
        return

    url = target.get("url")
    if not url:
        logger.error(f"[Step 4] 通知目标「{name}」未配置 url，跳过")
        return

    headers = {"X-Secret": target.get("secret", "")}
    retry_times = target.get("retry_times", default_retry_times)
    retry_interval = target.get("retry_interval_seconds", default_retry_interval)

    logger.info(
        f"[Step 4] 通知目标「{name}」，url={url}，"
        f"object_name={payload['object_name']}，categories={payload['categories']}"
    )

    for attempt in range(retry_times):
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, json=payload, headers=headers)
                logger.debug(
                    f"「{name}」通知响应，status={resp.status_code}，body={resp.text[:200]}"
                )
                if resp.status_code == 409:
                    logger.info(f"目标「{name}」已在处理该任务（幂等），视为通知成功")
                    return
                resp.raise_for_status()
            logger.info(
                f"[Step 4] 通知目标「{name}」成功，object_name={payload['object_name']}"
            )
            return
        except Exception as e:
            if attempt < retry_times - 1:
                logger.warning(
                    f"通知目标「{name}」失败（第 {attempt + 1}/{retry_times} 次），"
                    f"{retry_interval}s 后重试，错误：{e}"
                )
                time.sleep(retry_interval)
            else:
                logger.error(
                    f"通知目标「{name}」失败，已达最大重试次数 {retry_times}，"
                    f"url={url}，错误：{e}",
                    exc_info=True,
                )


def notify_processor(
    object_name: str,
    md5: str,
    start: str,
    end: str,
    categories: list,
    config: dict,
):
    """依次通知 notify.targets 中配置的所有下游服务。

    各目标相互独立：单个目标失败（重试耗尽）仅记录日志，不影响其余目标。
    notify.enabled 为全局总开关；每个目标可通过自身 enabled 单独启停。
    retry_times / retry_interval_seconds 在 notify 顶层作为全局默认值，
    可被单个目标覆盖。
    """
    notify_cfg = config["notify"]

    if not notify_cfg.get("enabled", True):
        logger.info("[Step 4] notify.enabled=false，跳过全部通知")
        return

    targets = notify_cfg.get("targets") or []
    if not targets:
        logger.warning("[Step 4] notify.targets 为空，无通知目标，跳过通知")
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
    logger.debug(f"通知 payload：{payload}")

    default_retry_times = notify_cfg.get("retry_times", 3)
    default_retry_interval = notify_cfg.get("retry_interval_seconds", 30)

    enabled_count = sum(1 for t in targets if t.get("enabled", True))
    logger.info(
        f"[Step 4] 共 {len(targets)} 个通知目标，其中启用 {enabled_count} 个，开始逐个通知"
    )

    for target in targets:
        try:
            _notify_one(
                target, payload, default_retry_times, default_retry_interval
            )
        except Exception as e:
            # _notify_one 内部已对网络等异常兜底，此处再保险一层，
            # 确保单个目标的未预期异常绝不影响其余目标。
            name = target.get("name") or target.get("url", "<未命名>")
            logger.error(
                f"[Step 4] 通知目标「{name}」出现未预期异常：{e}", exc_info=True
            )
