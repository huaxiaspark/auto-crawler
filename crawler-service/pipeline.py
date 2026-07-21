import os
import time
import shutil
import logging
from datetime import datetime

import crawler_runner
import verify_runner
import uploader
import notifier

logger = logging.getLogger(__name__)


def _get_categories(config: dict, tasks: list, scheduled_run: bool = False) -> list:
    """获取本次爬取的任务名列表作为 categories。

    定时触发（scheduled_run=True）且未显式指定 tasks 时，与爬虫侧保持一致：
    排除未纳入定时的任务（schedule_enabled=false）。
    """
    if tasks:
        return tasks
    import yaml
    config_path = os.path.abspath(config["crawler"]["config_path"])
    with open(config_path, encoding="utf-8") as f:
        crawler_cfg = yaml.safe_load(f)
    return [
        name
        for name, cfg in crawler_cfg.get("tasks", {}).items()
        if cfg.get("enabled")
        and (not scheduled_run or cfg.get("schedule_enabled", True))
    ]


def _flatten_and_classify(data_dir: str):
    """
    第一步：将所有子目录中的文件移动到根目录（按字典序覆盖）。
    第二步：按任务名（第一个 _ 之前的部分）重新归类到子目录。
    """
    # 第一步：展平
    subdirs = sorted(
        [d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))]
    )
    for subdir in subdirs:
        subdir_path = os.path.join(data_dir, subdir)
        for fname in os.listdir(subdir_path):
            src = os.path.join(subdir_path, fname)
            dst = os.path.join(data_dir, fname)
            if os.path.isfile(src):
                if os.path.exists(dst):
                    logger.warning(
                        "[Step 4] 文件整理：目标已存在，将被覆盖：%s（来源子目录：%s）",
                        fname, subdir,
                    )
                shutil.move(src, dst)
        # 删除已清空的子目录
        try:
            os.rmdir(subdir_path)
        except OSError:
            shutil.rmtree(subdir_path)

    # 第二步：按任务名归类
    for fname in os.listdir(data_dir):
        fpath = os.path.join(data_dir, fname)
        if not os.path.isfile(fpath):
            continue
        if not fname.endswith((".xlsx", ".csv")):
            continue
        # 使用 os.path.basename 处理路径，确保跨平台兼容
        pure_fname = os.path.basename(fname)
        task_name = pure_fname.split("_")[0] if "_" in pure_fname else pure_fname
        task_dir = os.path.join(data_dir, task_name)
        os.makedirs(task_dir, exist_ok=True)
        shutil.move(fpath, os.path.join(task_dir, fname))

    logger.info(f"[Step 4] 文件整理完成，data_dir={data_dir}")


def _run_verify_and_retry(start: str, end: str, config: dict,
                          scheduled_run: bool = False, tasks: list = None) -> bool:
    """执行校验，失败则批量重爬，最多重试 max_retry_rounds 轮。返回是否最终通过。

    tasks 非空时仅校验/重爬指定任务，避免单任务回补时对其他任务误报缺失。
    """
    max_rounds = config["verify"]["max_retry_rounds"]
    interval = config["verify"]["retry_interval_seconds"]
    loss_path = os.path.abspath(config["verify"]["loss_file"])

    logger.info(
        f"[Step 2] 开始数据校验，日期范围={start}~{end}，scheduled_run={scheduled_run}"
        f"，tasks={tasks or '全部'}"
    )
    verify_runner.run(config=config, start=start, end=end,
                      scheduled_run=scheduled_run, tasks=tasks)

    if verify_runner.is_pass(loss_path):
        logger.info("[Step 2] 初始校验通过，进入打包上传步骤")
        return True

    missing_lines = verify_runner.read_missing_lines(loss_path)
    logger.warning(f"[Step 2] 初始校验失败，缺失 {len(missing_lines)} 条，进入重爬流程")

    still_missing = missing_lines
    for round_num in range(1, max_rounds + 1):
        logger.warning(f"[Step 3] 第 {round_num}/{max_rounds} 轮重爬开始，loss_file={loss_path}")
        crawler_runner.run_with_loss_file(config=config, loss_file_abs=loss_path)
        logger.info(f"[Step 3] 第 {round_num} 轮重爬完成，开始校验")
        verify_runner.run(config=config, start=start, end=end,
                          scheduled_run=scheduled_run, tasks=tasks)

        if verify_runner.is_pass(loss_path):
            logger.info(f"[Step 3] 第 {round_num} 轮重爬后校验通过，进入打包上传步骤")
            return True

        still_missing = verify_runner.read_missing_lines(loss_path)
        logger.warning(f"[Step 3] 第 {round_num} 轮校验仍不通过，剩余缺失 {len(still_missing)} 条")
        if round_num < max_rounds:
            logger.info(f"[Step 3] 等待 {interval}s 后开启第 {round_num + 1} 轮重爬...")
            time.sleep(interval)

    logger.error(
        f"[Step 3] 已达最大重试轮次 {max_rounds}，仍存在缺失数据（{len(still_missing)} 条），"
        f"继续执行打包上传。缺失列表：{still_missing}"
    )
    return False


def run(config: dict, start: str, end: str, tasks: list = None,
        scheduled_run: bool = False):
    """完整流程：爬取 → 校验 → [重爬] → 打包上传 → 通知。"""
    t0 = datetime.now()
    logger.info(
        f"pipeline.run 开始，start={start}，end={end}，tasks={tasks or '全部'}，"
        f"scheduled_run={scheduled_run}"
    )

    categories = _get_categories(config, tasks, scheduled_run=scheduled_run)
    logger.debug(f"本次 categories={categories}")

    run_ctx = (
        f"start={start}，end={end}，tasks={tasks or '全部'}，"
        f"scheduled_run={scheduled_run}"
    )

    # Step 1: 爬取
    try:
        crawler_runner.run(
            config=config,
            start=start,
            end=end,
            tasks=tasks,
            scheduled_run=scheduled_run,
        )
    except Exception:
        logger.error(
            "[Step 1] 爬虫失败，终止当前流程（%s）", run_ctx, exc_info=True,
        )
        return

    # Step 2 & 3: 校验 + 重爬
    verify_passed = False
    try:
        verify_passed = _run_verify_and_retry(
            start=start,
            end=end,
            config=config,
            scheduled_run=scheduled_run,
            tasks=tasks,
        )
    except Exception:
        logger.error(
            "[Step 2/3] 校验/重爬异常，终止当前流程（%s）", run_ctx, exc_info=True,
        )
        return

    if verify_passed:
        logger.info("[Step 2/3] 数据校验最终结果：PASS（%s）", run_ctx)
    else:
        logger.warning(
            "[Step 2/3] 数据校验最终结果：FAIL（仍有缺失数据），继续打包上传（%s）",
            run_ctx,
        )

    # Step 4: 打包上传
    data_dir = os.path.abspath(config["crawler"]["data_dir"])
    object_name = f"{start}-{end}.tar.gz" if start != end else f"{start}.tar.gz"
    upload_start = datetime.now()

    try:
        logger.info("[Step 4] 开始整理文件（%s）", run_ctx)
        _flatten_and_classify(data_dir)

        md5, size_bytes = uploader.pack_and_upload(
            source_dir=data_dir,
            object_name=object_name,
            config=config["upload"],
        )
        uploader.write_manifest(
            log_dir=config["log_dir"],
            object_name=object_name,
            size_bytes=size_bytes,
            md5=md5,
            uploaded_at=upload_start.isoformat(),
        )
    except Exception:
        logger.error(
            "[Step 4] 打包上传失败，跳过通知和清理（%s）", run_ctx, exc_info=True,
        )
        return

    # 上传成功后清理
    if config["crawler"].get("cleanup_after_upload", False):
        uploader.cleanup_dir(data_dir)

    # 通知服务器 B
    try:
        notifier.notify_processor(
            object_name=object_name,
            md5=md5,
            start=start,
            end=end,
            categories=categories,
            config=config,
        )
    except Exception:
        logger.error(
            "[Step 4] 通知服务器 B 异常（%s）", run_ctx, exc_info=True,
        )

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"pipeline.run 完成，耗时 {elapsed:.1f}s（{run_ctx}）")
