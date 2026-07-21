import os
import sys
import subprocess
import logging

logger = logging.getLogger(__name__)


def run(config: dict, start: str, end: str, scheduled_run: bool = False,
        tasks: list = None):
    """调用 data-verify/analyze_excel.py 执行校验。

    tasks 非空时（单任务/指定任务回补）传递 --only-tasks，
    仅校验这些任务，避免对未爬取的其他任务误报缺失而触发连带重爬。
    """
    script = os.path.abspath(config["verify"]["script_path"])
    config_path = os.path.abspath(config["verify"]["config_path"])
    cmd = [sys.executable, script, "--config", config_path, "--start", start, "--end", end]
    cmd.append("--enable-schedule-alignment" if scheduled_run else "--disable-schedule-alignment")
    if tasks:
        cmd += ["--only-tasks", ",".join(tasks)]

    logger.info(
        f"[Step 2] 启动校验，start={start}，end={end}，scheduled_run={scheduled_run}，"
        f"tasks={tasks or '全部'}"
    )
    logger.debug(f"校验命令：{' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=os.path.dirname(script),
    )

    if result.stdout:
        logger.debug(f"校验 stdout：\n{result.stdout}")
    if result.returncode != 0:
        logger.error(
            f"[Step 2] 校验脚本非零退出，returncode={result.returncode}，"
            f"stderr：\n{result.stderr}"
        )
        raise RuntimeError(f"verify exited with code {result.returncode}")

    logger.info("[Step 2] 校验脚本执行完成")


def is_pass(loss_path: str) -> bool:
    """loss.txt 中无有效数据行则返回 True（校验通过）。"""
    if not os.path.exists(loss_path):
        logger.debug(f"loss.txt 不存在，视为 PASS：{loss_path}")
        return True
    missing = []
    with open(loss_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                missing.append(line)
    if missing:
        logger.debug(
            f"loss.txt 有效数据行（共 {len(missing)} 条）："
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}"
        )
        return False
    return True


def read_missing_lines(loss_path: str) -> list:
    """读取 loss.txt 中所有有效数据行。"""
    if not os.path.exists(loss_path):
        return []
    lines = []
    with open(loss_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return lines
