import os
import sys
import subprocess
import logging

logger = logging.getLogger(__name__)


def run(config: dict, start: str, end: str, tasks: list = None,
        scheduled_run: bool = False):
    """调用 crawler/main.py 执行爬取，非零退出时抛出异常。"""
    script = os.path.abspath(config["crawler"]["script_path"])
    config_path = os.path.abspath(config["crawler"]["config_path"])
    cmd = [sys.executable, script, "--config", config_path, "--start", start, "--end", end]
    if tasks:
        cmd += ["--task", ",".join(tasks)]
    if scheduled_run:
        cmd.append("--scheduled-run")

    logger.info(
        f"[Step 1] 启动爬虫，start={start}，end={end}，tasks={tasks or '全部'}，"
        f"scheduled_run={scheduled_run}"
    )
    logger.debug(f"爬虫命令：{' '.join(cmd)}")

    result = subprocess.run(cmd, text=True, cwd=os.path.dirname(script))

    if result.returncode != 0:
        logger.error(f"[Step 1] 爬虫非零退出，returncode={result.returncode}")
        raise RuntimeError(f"crawler exited with code {result.returncode}")

    logger.info(f"[Step 1] 爬虫完成，start={start}，end={end}，scheduled_run={scheduled_run}")


def run_with_loss_file(config: dict, loss_file_abs: str):
    """通过 --loss-file 参数调用爬虫补充下载缺失数据。"""
    script = os.path.abspath(config["crawler"]["script_path"])
    config_path = os.path.abspath(config["crawler"]["config_path"])
    cmd = [sys.executable, script, "--config", config_path, "--loss-file", loss_file_abs]

    logger.info(f"[Step 3] 重爬，loss_file={loss_file_abs}")
    logger.debug(f"重爬命令：{' '.join(cmd)}")

    result = subprocess.run(cmd, text=True, cwd=os.path.dirname(script))

    if result.returncode != 0:
        logger.error(f"[Step 3] 重爬非零退出，returncode={result.returncode}")
        raise RuntimeError(f"crawler (loss-file) exited with code {result.returncode}")

    logger.info("[Step 3] 重爬完成")
