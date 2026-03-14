import json
import os
import threading
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

import pipeline as proc_pipeline

logger = logging.getLogger(__name__)

app = FastAPI()

# 全局配置和幂等状态，由 main.py 注入
_config: dict = {}
_jobs: dict = {}
_jobs_lock = threading.Lock()
_jobs_file: str = ""


def init(config: dict):
    global _config, _jobs, _jobs_file
    _config = config
    _jobs_file = os.path.abspath(config["server"]["jobs_file"])
    _jobs = _load_jobs(_jobs_file)
    _reset_stale_processing_jobs(config["server"].get("processing_timeout", 600))


def _load_jobs(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception as e:
        logger.warning(f"加载 jobs_file 失败，初始化为空：{e}")
        return {}


def _save_jobs():
    with open(_jobs_file, "w", encoding="utf-8") as f:
        json.dump(_jobs, f, ensure_ascii=False, indent=2)


def _reset_stale_processing_jobs(timeout_seconds: int):
    now = datetime.now(timezone.utc)
    for obj_name, job in list(_jobs.items()):
        if job.get("status") == "processing":
            started_at_str = job.get("started_at", "")
            try:
                started_at = datetime.fromisoformat(started_at_str)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                elapsed = (now - started_at).total_seconds()
                if elapsed > timeout_seconds:
                    logger.warning(
                        f"启动时重置超时任务：object_name={obj_name}，"
                        f"原始 started_at={started_at_str}，已超时 {elapsed:.0f}s"
                    )
                    _jobs[obj_name]["status"] = "failed"
            except Exception as e:
                logger.warning(f"解析 started_at 失败，跳过重置：{obj_name}，错误：{e}")
    _save_jobs()


class DateRange(BaseModel):
    start: str
    end: str


class TriggerRequest(BaseModel):
    object_name: str
    download_url: str
    md5: str
    date_range: DateRange
    categories: list[str]
    timestamp: str


@app.post("/api/trigger")
async def trigger(request: TriggerRequest):
    obj = request.object_name

    with _jobs_lock:
        job = _jobs.get(obj)
        if job and job.get("status") in ("processing", "done"):
            logger.info(f"幂等拦截，object_name={obj}，status={job['status']}")
            return JSONResponse(
                status_code=409,
                content={"detail": f"Job {obj} already {job['status']}"},
            )
        _jobs[obj] = {
            "status": "processing",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }
        _save_jobs()

    logger.info(f"[Step 1] 接收触发信号，object_name={obj}，categories={request.categories}")

    thread = threading.Thread(
        target=_process_job,
        args=(request,),
        daemon=True,
    )
    thread.start()

    return JSONResponse(status_code=202, content={"detail": "accepted", "object_name": obj})


def _process_job(request: TriggerRequest):
    obj = request.object_name
    try:
        proc_pipeline.run(
            object_name=obj,
            md5=request.md5,
            date_range={"start": request.date_range.start, "end": request.date_range.end},
            categories=request.categories,
            config=_config,
        )
        with _jobs_lock:
            _jobs[obj]["status"] = "done"
            _jobs[obj]["finished_at"] = datetime.now().isoformat()
            _save_jobs()
        logger.info(f"任务完成，object_name={obj}")
    except Exception:
        logger.error(f"任务失败，object_name={obj}", exc_info=True)
        with _jobs_lock:
            _jobs[obj]["status"] = "failed"
            _jobs[obj]["finished_at"] = datetime.now().isoformat()
            _save_jobs()
