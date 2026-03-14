import os
import tarfile
import hashlib
import tempfile
import logging
from datetime import datetime

from minio import Minio

logger = logging.getLogger(__name__)


def get_client(config: dict) -> Minio:
    endpoint = config["endpoint"].replace("http://", "").replace("https://", "")
    return Minio(
        endpoint=endpoint,
        access_key=config["access_key"],
        secret_key=config["secret_key"],
        secure=config.get("secure", False),
    )


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def pack_and_upload(source_dir: str, object_name: str, config: dict) -> tuple:
    """将 source_dir 整体打包为 .tar.gz 后上传到 MinIO。返回 (md5, size_bytes)。"""
    logger.info(f"[Step 4] 开始打包，source_dir={source_dir}，object_name={object_name}")
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            tar.add(source_dir, arcname=os.path.basename(source_dir))

        size_bytes = os.path.getsize(tmp_path)
        md5 = _md5_file(tmp_path)
        logger.debug(f"打包完成，tmp_path={tmp_path}，size={size_bytes} bytes，md5={md5}")

        full_object = f"{config['prefix']}{object_name}"
        logger.info(
            f"[Step 4] 开始上传，bucket={config['bucket']}，"
            f"object={full_object}，size={size_bytes} bytes"
        )
        client = get_client(config)
        client.fput_object(
            bucket_name=config["bucket"],
            object_name=full_object,
            file_path=tmp_path,
            content_type="application/gzip",
        )
        logger.info(f"[Step 4] 上传完成，object={full_object}，md5={md5}，size={size_bytes} bytes")
        return md5, size_bytes
    except Exception as e:
        logger.error(f"[Step 4] 上传失败，object_name={object_name}，错误：{e}", exc_info=True)
        raise
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
            logger.debug(f"临时文件已清理：{tmp_path}")


def write_manifest(log_dir: str, object_name: str, size_bytes: int, md5: str, uploaded_at: str):
    """将上传清单写入 JSON 文件。"""
    import json
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest = {
        "object_name": object_name,
        "size_bytes": size_bytes,
        "md5": md5,
        "uploaded_at": uploaded_at,
    }
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"upload_manifest_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    logger.info(f"[Step 4] 上传清单已写入：{path}，摘要：{manifest}")


def cleanup_dir(dir_path: str):
    """清空目录下所有内容（保留目录本身）。"""
    import shutil
    if not os.path.exists(dir_path):
        return
    for item in os.listdir(dir_path):
        item_path = os.path.join(dir_path, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)
    logger.info(f"已清理目录：{dir_path}")
