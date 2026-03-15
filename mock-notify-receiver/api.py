import logging
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("api")
app = FastAPI(title="Mock Notify Receiver")

_config: dict = {}
_received: list = []


def init(config: dict):
    global _config
    _config = config


class DateRange(BaseModel):
    start: str
    end: str


class NotifyPayload(BaseModel):
    categories: List[str]
    date_range: DateRange
    object_name: str
    md5: str
    download_url: str


@app.post("/data/notify")
async def receive_notify(
    payload: NotifyPayload,
    authorization: Optional[str] = Header(None),
):
    _check_token(authorization)
    entry = {"received_at": datetime.now().isoformat(), "endpoint": "/data/notify", **payload.model_dump()}
    _received.append(entry)
    logger.info(
        f"[/data/notify] object_name={payload.object_name} md5={payload.md5} "
        f"categories={payload.categories} date_range={payload.date_range.start}~{payload.date_range.end}"
    )
    return {"status": "ok", "received_at": entry["received_at"]}


@app.post("/webhook")
async def receive_webhook(
    payload: NotifyPayload,
    authorization: Optional[str] = Header(None),
):
    _check_token(authorization)
    entry = {"received_at": datetime.now().isoformat(), "endpoint": "/webhook", **payload.model_dump()}
    _received.append(entry)
    logger.info(
        f"[/webhook] object_name={payload.object_name} md5={payload.md5} "
        f"categories={payload.categories} date_range={payload.date_range.start}~{payload.date_range.end}"
    )
    return {"status": "ok", "received_at": entry["received_at"]}


@app.get("/records")
async def list_records():
    """查看所有已接收的通知记录。"""
    return {"total": len(_received), "records": _received}


@app.delete("/records")
async def clear_records():
    """清空通知记录。"""
    _received.clear()
    return {"status": "cleared"}


def _check_token(authorization: Optional[str]):
    secret = _config.get("server", {}).get("secret", "")
    if not secret:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != secret:
        raise HTTPException(status_code=403, detail="Invalid token")
