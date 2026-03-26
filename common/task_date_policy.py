"""任务日期策略工具。

为 crawler 和 data-verify 提供统一的定时任务日期偏移计算逻辑。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple


DATE_FMT = "%Y-%m-%d"


def shift_date_str(date_str: str, offset_days: int) -> str:
    """将 YYYY-MM-DD 日期字符串按天偏移。"""
    dt = datetime.strptime(date_str, DATE_FMT).date()
    return (dt + timedelta(days=offset_days)).strftime(DATE_FMT)


def generate_date_list(start_date: str, end_date: str) -> List[str]:
    """生成闭区间日期列表。"""
    start = datetime.strptime(start_date, DATE_FMT).date()
    end = datetime.strptime(end_date, DATE_FMT).date()
    dates: List[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime(DATE_FMT))
        current += timedelta(days=1)
    return dates


def resolve_schedule_base_range(
    start_date: str,
    end_date: str,
    schedule_cfg: dict,
    today: Optional[date] = None,
) -> Tuple[str, str]:
    """解析调度模式下的基准日期范围。"""
    if today is None:
        today = datetime.now().date()

    date_mode = (schedule_cfg or {}).get("date_mode", "yesterday")
    today_str = today.strftime(DATE_FMT)

    if date_mode == "yesterday":
        target = (today - timedelta(days=1)).strftime(DATE_FMT)
        return target, target
    if date_mode == "today":
        return today_str, today_str
    if date_mode == "tomorrow":
        target = (today + timedelta(days=1)).strftime(DATE_FMT)
        return target, target
    return start_date, today_str


def resolve_task_offset_days(task_config: Optional[dict], schedule_cfg: Optional[dict]) -> int:
    """解析任务在定时模式下的日期偏移天数。"""
    task_cfg = task_config or {}
    sched_cfg = schedule_cfg or {}

    if "schedule_date_offset_days" in task_cfg:
        return int(task_cfg["schedule_date_offset_days"])

    if sched_cfg.get("use_task_date_offsets", False):
        return int(sched_cfg.get("default_task_date_offset_days", 0))

    return 0


def apply_task_offset_to_range(
    start_date: str,
    end_date: str,
    task_config: Optional[dict],
    schedule_cfg: Optional[dict],
) -> Tuple[str, str]:
    """将任务偏移应用到日期范围。"""
    offset_days = resolve_task_offset_days(task_config, schedule_cfg)
    if offset_days == 0:
        return start_date, end_date
    return shift_date_str(start_date, offset_days), shift_date_str(end_date, offset_days)


def build_task_schedule_ranges(
    tasks: Dict[str, dict],
    start_date: str,
    end_date: str,
    schedule_cfg: Optional[dict],
    today: Optional[date] = None,
) -> Dict[str, Tuple[str, str]]:
    """为每个任务构造定时执行时的实际爬取日期范围。"""
    base_start, base_end = resolve_schedule_base_range(start_date, end_date, schedule_cfg or {}, today=today)
    ranges: Dict[str, Tuple[str, str]] = {}
    for task_name, task_config in tasks.items():
        ranges[task_name] = apply_task_offset_to_range(base_start, base_end, task_config, schedule_cfg)
    return ranges


def apply_task_offset_to_dates(
    dates: Iterable[str],
    task_config: Optional[dict],
    schedule_cfg: Optional[dict],
) -> List[str]:
    """将任务偏移应用到日期列表。"""
    offset_days = resolve_task_offset_days(task_config, schedule_cfg)
    if offset_days == 0:
        return list(dates)
    return [shift_date_str(d, offset_days) for d in dates]
