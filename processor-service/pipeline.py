import json
import os
import shutil
import logging
from datetime import datetime

import uploader
import processor_runner
import platform_notifier

logger = logging.getLogger(__name__)


def run(object_name: str, md5: str, date_range: dict, categories: list, config: dict):
    """完整处理流程：下载解压 → 数据转换 → 打包上传 → 通知三方平台。"""
    t0 = datetime.now()
    start = date_range["start"]
    end = date_range["end"]
    logger.info(f"processor pipeline 开始，object_name={object_name}，start={start}，end={end}")

    data_dir = os.path.abspath(config["processor"]["data_dir"])
    output_dir = os.path.abspath(config["processor"]["output_dir"])
    cache_dir = os.path.abspath(config["storage"]["local_cache_dir"])
    tar_path = os.path.join(cache_dir, object_name)

    # Step 2: 下载
    try:
        logger.info(f"[Step 2] 开始下载，object_name={object_name}")
        uploader.download_file(object_name=object_name, dest_path=tar_path, config=config["storage"])
    except Exception:
        logger.error("[Step 2] 下载失败", exc_info=True)
        raise

    # MD5 校验
    actual_md5 = uploader._md5_file(tar_path)
    if actual_md5 != md5:
        msg = f"[Step 2] MD5 校验不一致，期望={md5}，实际={actual_md5}"
        logger.error(msg)
        raise ValueError(msg)
    logger.info(f"[Step 2] MD5 校验通过，md5={md5}")

    # 清空 data/ 和 output/ 目录
    for d in [data_dir, output_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
    logger.info("[Step 2] 已清空 data/ 和 output/ 目录")

    # 解压
    try:
        uploader.extract_strip_top(tar_path=tar_path, dest_dir=data_dir)
    except Exception:
        logger.error("[Step 2] 解压失败", exc_info=True)
        raise

    # 数据转换
    try:
        logger.info("[Step 2] 开始数据转换")
        processor_runner.run(config=config)
        logger.info("[Step 2] 数据转换完成")
    except Exception:
        logger.error("[Step 2] 数据转换失败", exc_info=True)
        raise

    # Step 3: 打包上传
    upload_start = datetime.now()
    try:
        out_md5, size_bytes = uploader.pack_and_upload(
            source_dir=output_dir,
            object_name=object_name,
            config=config["upload"],
        )
        uploader.write_manifest(
            log_dir=config["log_dir"],
            object_name=object_name,
            size_bytes=size_bytes,
            md5=out_md5,
            uploaded_at=upload_start.isoformat(),
        )
    except Exception:
        logger.error("[Step 3] 打包上传失败", exc_info=True)
        raise

    # 上传成功后清理
    if config["processor"].get("cleanup_after_upload", True):
        uploader.cleanup_dir(data_dir)
        uploader.cleanup_dir(output_dir)

    # Step 4: 通知三方平台
    payload = {
        "categories": categories,
        "date_range": date_range,
        "object_name": object_name,
        "md5": out_md5,
        "download_url": (
            f"{config['upload']['endpoint']}/{config['upload']['bucket']}/"
            f"{config['upload']['prefix']}{object_name}"
        ),
    }
    platform_notifier.notify_all(config=config, payload=payload)

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"processor pipeline 完成，耗时 {elapsed:.1f}s")
