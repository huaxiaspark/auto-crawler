import os
import sys
import subprocess
import logging

logger = logging.getLogger(__name__)


def run(config: dict):
    """调用 post-process/batch_process_data.py 执行宽表转换。"""
    script = os.path.abspath(config["processor"]["script_path"])
    cmd = [sys.executable, script]

    logger.info(f"[Step 2] 启动数据转换，script={script}")
    logger.debug(f"转换命令：{' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(script))

    if result.stdout:
        logger.debug(f"转换 stdout：\n{result.stdout}")
    if result.returncode != 0:
        logger.error(
            f"[Step 2] 转换脚本非零退出，returncode={result.returncode}，"
            f"stderr：\n{result.stderr}"
        )
        raise RuntimeError(f"batch_process_data exited with code {result.returncode}")

    logger.info("[Step 2] 数据转换完成")
