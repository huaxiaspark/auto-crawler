import os
import sys
import subprocess
import logging

logger = logging.getLogger(__name__)


def run(config: dict, start: str, end: str, tasks: list = None):
    """调用 crawler/main.py 执行爬取，非零退出时抛出异常。"""
    script = os.path.abspath(config["crawler"]["script_path"])
    cmd = [sys.executable, script, "--start", start, "--end", end]
    if tasks:
        cmd += ["--task", ",".join(tasks)]

    logger.info(f"[Step 1] 启动爬虫，start={start}，end={end}，tasks={tasks or '全部'}")
    logger.debug(f"爬虫命令：{' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(script))

    if result.stdout:
        logger.debug(f"爬虫 stdout：\n{result.stdout}")
    if result.returncode != 0:
        logger.error(
            f"[Step 1] 爬虫非零退出，returncode={result.returncode}，"
            f"stderr：\n{result.stderr}"
        )
        raise RuntimeError(f"crawler exited with code {result.returncode}")

    logger.info(f"[Step 1] 爬虫完成，start={start}，end={end}")


def run_with_loss_file(config: dict, loss_file_abs: str):
    """通过 --loss-file 参数调用爬虫补充下载缺失数据。"""
    script = os.path.abspath(config["crawler"]["script_path"])
    cmd = [sys.executable, script, "--loss-file", loss_file_abs]

    logger.info(f"[Step 3] 重爬，loss_file={loss_file_abs}")
    logger.debug(f"重爬命令：{' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(script))

    if result.stdout:
        logger.debug(f"重爬 stdout：\n{result.stdout}")
    if result.returncode != 0:
        logger.error(
            f"[Step 3] 重爬非零退出，returncode={result.returncode}，"
            f"stderr：\n{result.stderr}"
        )
        raise RuntimeError(f"crawler (loss-file) exited with code {result.returncode}")

    logger.info("[Step 3] 重爬完成")
