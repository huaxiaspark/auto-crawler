#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量处理 data 目录下各子目录的 Excel/CSV 数据，生成规范的宽表 CSV 文件。

所有硬编码配置均通过 config.yaml 驱动，支持灵活扩展新数据类型。
时间格式统一为 YYYY-MM-DD HH:MM:SS.000
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import warnings
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_MAX_MB = 5


class _SizedTimedRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, *args, max_bytes: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_bytes = max_bytes
        self._size_rollover_count = 0

    def emit(self, record):
        try:
            if (
                self.max_bytes > 0
                and os.path.exists(self.baseFilename)
                and os.path.getsize(self.baseFilename) >= self.max_bytes
            ):
                self._size_rollover_count += 1
                new = f"{self.baseFilename}.{time.strftime(self.suffix, time.localtime())}_{self._size_rollover_count}"
                if self.stream:
                    self.stream.close()
                    self.stream = None
                os.rename(self.baseFilename, new)
                self.stream = self._open()
        except Exception:
            pass
        super().emit(record)

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# ── 默认配置（当 config.yaml 缺少对应字段时使用）──────────────
_DEFAULTS = {
    "time_format": "%Y-%m-%d %H:%M:%S.000",
    "data_root": "data",
    "output_root": "output",
    "max_file_size_mb": 5,
}


def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    g = {**_DEFAULTS, **(cfg.get("global") or {})}
    cfg["global"] = g
    return cfg


# ── 全局上下文（由 main 初始化）────────────────────────────────
_CFG: dict = {}
_DATA_ROOT: Path = Path("data")
_OUTPUT_ROOT: Path = Path("output")
_TIME_FMT: str = _DEFAULTS["time_format"]
_MAX_FILE_SIZE: int = 5 * 1024 * 1024


def _init_globals(cfg: dict, base_dir: Path) -> None:
    global _CFG, _DATA_ROOT, _OUTPUT_ROOT, _TIME_FMT, _MAX_FILE_SIZE
    _CFG = cfg
    g = cfg["global"]
    _DATA_ROOT = (base_dir / g["data_root"]).resolve()
    _OUTPUT_ROOT = (base_dir / g["output_root"]).resolve()
    _TIME_FMT = g["time_format"]
    _MAX_FILE_SIZE = int(g["max_file_size_mb"]) * 1024 * 1024


# ── 工具函数 ────────────────────────────────────────────────────

def ensure_output_dir(subdir: str) -> Path:
    out = _OUTPUT_ROOT / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_csv_with_split(df: pd.DataFrame, base_path: Path, **to_csv_kwargs) -> None:
    """保存 DataFrame 为 CSV；超过 max_file_size 时自动拆分。"""
    base_path = Path(base_path)
    if base_path.suffix != ".csv":
        base_path = base_path.with_suffix(".csv")
    opts = {"index": False, "encoding": "utf-8", **to_csv_kwargs}
    df.to_csv(base_path, **opts)
    size = base_path.stat().st_size
    if size <= _MAX_FILE_SIZE:
        return
    base_path.unlink()
    n = len(df)
    n_parts = max(2, (size + _MAX_FILE_SIZE - 1) // _MAX_FILE_SIZE)
    chunk_size = (n + n_parts - 1) // n_parts
    stem, parent = base_path.stem, base_path.parent
    for i in range(n_parts):
        part = df.iloc[i * chunk_size: min((i + 1) * chunk_size, n)]
        part.to_csv(parent / f"{stem}_part{i + 1}.csv", **opts)
    logging.info("已拆分为 %d 个文件", n_parts)


def extract_date_000(val) -> str:
    """日期转为当日 00:00:00.000"""
    try:
        if pd.isna(val):
            return ""
        return pd.to_datetime(val).strftime("%Y-%m-%d 00:00:00.000")
    except Exception:
        return ""


def merge_datetime(date_val, time_val) -> str:
    """合并日期和时点为标准 timestamp"""
    try:
        date_str = str(date_val).split()[0][:10]
        time_str = str(time_val).strip().zfill(5)
        if len(time_str) == 4:
            time_str = "0" + time_str
        dt = datetime.strptime(f"{date_str} {time_str}:00", "%Y-%m-%d %H:%M:%S")
        return dt.strftime(_TIME_FMT)
    except Exception:
        return ""


def get_value_map(map_name: str) -> dict:
    """从配置中获取文本->数值映射表"""
    return _CFG.get("value_maps", {}).get(map_name, {})


def apply_value_map(val, map_name: str) -> int:
    """将文本值按映射表转换为数值，未命中则返回 '其他' 对应值"""
    vmap = get_value_map(map_name)
    v = str(val).strip()
    return vmap.get(v, vmap.get("其他", len(vmap)))


def write_mapping_csv(out_dir: Path, filename: str, map_name: str, col_name: str) -> None:
    """将映射表写出为 CSV 文件"""
    vmap = get_value_map(map_name)
    rows = [{"label": k, "value": v} for k, v in vmap.items()]
    df = pd.DataFrame(rows).rename(columns={"label": col_name, "value": "映射值"})
    df.to_csv(out_dir / filename, index=False, encoding="utf-8")


def _find_col(df: pd.DataFrame, keyword: str, fallback_idx: int) -> str:
    for c in df.columns:
        if keyword in str(c):
            return c
    return df.columns[fallback_idx] if len(df.columns) > fallback_idx else df.columns[-1]


# ── 处理器实现 ──────────────────────────────────────────────────

def process_section_constraints(input_dir: Path, task: dict) -> None:
    """断面约束：正向/反向传输极限宽表 + 断面信息映射"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    sheet = task.get("sheet_name", "REPORT0")
    skip = task.get("skip_rows", 2)
    all_data, section_info = [], {}

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name=sheet, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            for c in cols[4:]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["日期", cols[2]])
            all_data.append(df)
            for _, row in df.iterrows():
                sn = str(row[cols[2]]).strip()
                if sn and sn != "nan":
                    section_info[sn] = str(row[cols[3]]).strip()
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged["timestamp"] = merged["日期"].apply(extract_date_000)
    merged = merged[merged["timestamp"] != ""]

    for out_cfg in task.get("outputs", []):
        col, fname = out_cfg["col"], out_cfg["file"]
        if col not in merged.columns:
            continue
        wide = merged.pivot_table(index="timestamp", columns=cols[2], values=col, aggfunc="first")
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / fname)

    if task.get("mapping_file"):
        info_df = pd.DataFrame([{cols[2]: k, cols[3]: v} for k, v in sorted(section_info.items())])
        info_df.to_csv(out_dir / task["mapping_file"], index=False, encoding="utf-8")
    logging.info("已输出: %s", out_dir)


def process_reserve_capacity(input_dir: Path, task: dict) -> None:
    """备用总量：timestamp + 多个数值列"""
    out_dir = ensure_output_dir(task["output_dir"])
    skip = task.get("skip_rows", 2)
    date_idx = task.get("date_col_idx", 1)
    value_cols_cfg = task.get("value_cols", [])
    rows = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] < skip + 1:
                continue
            row = df.iloc[skip]
            date_val = row.iloc[date_idx]
            if pd.isna(date_val):
                continue
            ts = pd.to_datetime(date_val).strftime(_TIME_FMT)
            rec = {"timestamp": ts}
            for vc in value_cols_cfg:
                v = row.iloc[vc["idx"]]
                rec[vc["name"]] = float(v) if pd.notna(v) else None
            rows.append(rec)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not rows:
        logging.warning("未读取到有效数据")
        return
    out_df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(out_df, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_clearing_quantity(input_dir: Path, task: dict) -> None:
    """实时各时段出清现货电量"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 3)
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df[cols[3]] = pd.to_numeric(df[cols[3]], errors="coerce")
            df = df.dropna(subset=["日期", cols[2]])
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged["timestamp"] = merged.apply(lambda r: merge_datetime(r["日期"], r[cols[2]]), axis=1)
    merged = merged[merged["timestamp"] != ""][["timestamp", cols[3]]]
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_channel_numeric(input_dir: Path, task: dict) -> None:
    """通道/断面类数值宽表（pivot: timestamp × name_col -> value_col）"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    name_col = task["name_col"]
    value_col = task["value_col"]
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            if value_col in df.columns:
                df["数值"] = pd.to_numeric(df[value_col], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            df["timestamp"] = df.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
            df = df[df["timestamp"] != ""][["timestamp", name_col, "数值"]]
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values="数值", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_channel_text(input_dir: Path, task: dict) -> None:
    """通道/断面类文本宽表（值映射为数值后 pivot）"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    name_col = task["name_col"]
    value_col = task["value_col"]
    map_name = task["value_map"]
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            df["timestamp"] = df.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
            df = df[df["timestamp"] != ""][["timestamp", name_col, value_col]].copy()
            df["数值"] = df[value_col].apply(lambda v: apply_value_map(v, map_name))
            all_data.append(df[["timestamp", name_col, "数值"]])
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values="数值", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / task["output_file"])

    if task.get("mapping_file"):
        write_mapping_csv(out_dir, task["mapping_file"], map_name, value_col)
    logging.info("已输出: %s", out_dir)


def _collect_node_lmp_files(input_dir: Path, task: dict) -> list[Path]:
    """收集节点边际电价文件，支持目录前缀匹配"""
    files: list[Path] = []
    if input_dir.exists():
        files.extend(sorted(input_dir.glob("*.xlsx")))
    if task.get("dir_prefix_match") and not files:
        prefix = task["name"]
        for d in sorted(input_dir.parent.iterdir()):
            if d.is_dir() and d.name.startswith(prefix):
                files.extend(sorted(d.glob("*.xlsx")))
    return files


def process_node_lmp(input_dir: Path, task: dict) -> None:
    """节点边际电价：输出多个宽表（电能量价格/阻塞价格/节点电价）"""
    out_dir = ensure_output_dir(task["output_dir"])
    prefix = task["output_prefix"]
    lmp_cols_cfg = task.get("lmp_columns", [])
    all_data = []

    for f in _collect_node_lmp_files(input_dir, task):
        try:
            df = pd.read_excel(f)
            if "节点名称" not in df.columns and df.shape[1] >= 6:
                df.columns = ["节点名称", "日期", "时点", "电能量价格", "阻塞价格", "节点电价"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            df["timestamp"] = df.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
            df = df[df["timestamp"] != ""]
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    for lc in lmp_cols_cfg:
        col = _find_col(merged, lc["keyword"], lc["fallback_idx"])
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
        sub = merged[["timestamp", "节点名称", col]].copy()
        wide = sub.pivot_table(index="timestamp", columns="节点名称", values=col, aggfunc="first")
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / f"{prefix}_{lc['suffix']}")
    logging.info("已输出: %s", out_dir)


def process_reservoir_level(input_dir: Path, task: dict) -> None:
    """抽蓄电站水位：日期×描述 宽表"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    name_col = task["name_col"]
    value_col = task["value_col"]
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
            df = df.dropna(subset=["日期", name_col])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            all_data.append(df[df["timestamp"] != ""][["timestamp", name_col, value_col]])
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values=value_col, aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_reserve_demand(input_dir: Path, task: dict) -> None:
    """日前正负备用需求：日期×备用类型 宽表"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    name_col = task["name_col"]
    value_col = task["value_col"]
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
            df = df.dropna(subset=["日期", name_col])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            all_data.append(df[df["timestamp"] != ""][["timestamp", name_col, value_col]])
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values=value_col, aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_clearing_overview_excel(input_dir: Path, task: dict) -> None:
    """市场出清概况（Excel 格式）"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期"])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            all_data.append(df[df["timestamp"] != ""][["timestamp", cols[2]]])
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_clearing_overview_csv(input_dir: Path, task: dict) -> None:
    """市场出清概况（CSV 格式）"""
    out_dir = ensure_output_dir(task["output_dir"])
    encoding = task.get("file_encoding", "utf-8")
    header_row = task.get("header_row", 1)
    all_data = []

    for f in sorted(input_dir.glob("*.csv")):
        try:
            df = pd.read_csv(f, encoding=encoding, header=header_row)
            if df.shape[0] < 1:
                continue
            if "日期" not in df.columns and df.shape[1] >= 2:
                df.columns = ["日期", "出清概况"] + list(df.columns[2:])
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期"])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            all_data.append(df[df["timestamp"] != ""][["timestamp", "出清概况"]])
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_unit_commitment(input_dir: Path, task: dict) -> None:
    """日前机组开机安排：timestamp×机组 -> 开停状态数值"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task.get("columns", ["机组名称", "日期", "时点", "开停状态"])
    name_col = task["name_col"]
    status_keywords = task.get("status_col_keywords", ["开停", "状态"])
    map_name = task["value_map"]
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f)
            if name_col not in df.columns and df.shape[1] >= len(cols):
                df.columns = cols[:df.shape[1]]
            status_col = next(
                (c for c in df.columns if any(k in str(c) for k in status_keywords)),
                df.columns[3] if len(df.columns) > 3 else None,
            )
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            df["timestamp"] = df.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
            df = df[df["timestamp"] != ""].copy()
            if status_col:
                df["状态值"] = df[status_col].apply(lambda v: apply_value_map(v, map_name))
            nc = name_col if name_col in df.columns else df.columns[0]
            all_data.append(df[["timestamp", nc, "状态值"]].rename(columns={nc: name_col}))
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values="状态值", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / task["output_file"])

    if task.get("mapping_file"):
        write_mapping_csv(out_dir, task["mapping_file"], map_name, "开停状态")
    logging.info("已输出: %s", out_dir)


def process_node_factor(input_dir: Path, task: dict) -> None:
    """节点分配因子：日期×节点 宽表"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    name_col = task["name_col"]
    value_col = task["value_col"]
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f)
            if name_col not in df.columns and df.shape[1] >= len(cols):
                df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
            df = df.dropna(subset=["日期", name_col])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            all_data.append(df[df["timestamp"] != ""][["timestamp", name_col, value_col]])
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values=value_col, aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_maintenance_plan(input_dir: Path, task: dict) -> None:
    """输变电设备检修计划：保留原始行结构"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[:df.shape[1]]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            for tc in ["开始时间", "结束时间"]:
                if tc in df.columns:
                    df[tc] = pd.to_datetime(df[tc], errors="coerce")
            df = df.dropna(subset=["日期", cols[2]])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.sort_values(["timestamp", cols[2]]).reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def _merge_date_period(date_val, period: int, offsets: dict) -> str:
    """业务日期 + 时段编号 -> timestamp（偏移量来自配置）"""
    try:
        dt = pd.to_datetime(date_val)
        date_str = dt.strftime("%Y-%m-%d")
        p = max(1, min(len(offsets), int(period) if pd.notna(period) else 1))
        h, m = offsets.get(str(p), offsets.get(p, [0, 0]))
        return f"{date_str} {h:02d}:{m:02d}:00.000"
    except Exception:
        return ""


def process_secondary_freq_clearing(input_dir: Path, task: dict) -> None:
    """二次调频出清结果：业务日期+时段 -> 各指标"""
    out_dir = ensure_output_dir(task["output_dir"])
    sheet = task.get("sheet_name", "REPORT0_C_C_C")
    date_col = task.get("date_col", "业务日期")
    period_col = task.get("period_col", "时段")
    out_cols = task.get("output_cols", [])
    offsets = _CFG.get("secondary_freq_period_offsets", {})
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name=sheet, header=0)
        except Exception:
            try:
                df = pd.read_excel(f, header=0)
            except Exception as e:
                logging.warning("跳过 %s: %s", f.name, e)
                continue
        try:
            if df.empty:
                continue
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col, period_col])
            df["timestamp"] = df.apply(
                lambda r: _merge_date_period(r[date_col], r[period_col], offsets), axis=1
            )
            df = df[df["timestamp"] != ""]
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    keep = [c for c in out_cols if c in merged.columns]
    merged = merged[keep].sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_coal_unit_capacity(input_dir: Path, task: dict) -> None:
    """省调煤电机组最大出力认定考核公示"""
    out_dir = ensure_output_dir(task["output_dir"])
    sheet = task.get("sheet_name", "REPORT0")
    name_col = task["name_col"]
    date_col = task["date_col"]
    ffill_cols = task.get("ffill_cols", [])
    outputs_cfg = task.get("outputs", [])
    test_col = task.get("test_result_col")
    map_name = task.get("value_map")
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name=sheet, header=0)
            if df.empty or name_col not in df.columns:
                continue
            for fc in ffill_cols:
                if fc in df.columns:
                    df[fc] = df[fc].ffill()
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col, name_col])
            df["timestamp"] = df[date_col].apply(extract_date_000)
            df = df[df["timestamp"] != ""]
            if test_col and test_col in df.columns and map_name:
                df[f"{test_col}_数值"] = df[test_col].apply(lambda v: apply_value_map(v, map_name))
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)

    for oc in outputs_cfg:
        col, fname = oc["col"], oc["file"]
        if col not in merged.columns:
            continue
        wide = merged.pivot_table(index="timestamp", columns=name_col, values=col, aggfunc="first")
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / fname)

    if test_col and map_name and task.get("mapping_file"):
        write_mapping_csv(out_dir, task["mapping_file"], map_name, test_col)
    logging.info("已输出: %s", out_dir)


def process_unit_generation_curve(input_dir: Path, task: dict) -> None:
    """机组实际发电曲线：timestamp×机组 宽表（96时点/天）"""
    out_dir = ensure_output_dir(task["output_dir"])
    sheet = task.get("sheet_name", "REPORT0")
    name_col = task["name_col"]
    date_col = task["date_col"]
    exclude = set(task.get("exclude_cols", ["序号", "日期", "机组名称"]))
    value_col = task.get("value_col", "发电量")
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name=sheet, header=0)
            if df.empty or name_col not in df.columns or date_col not in df.columns:
                continue
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col, name_col])
            time_cols = [c for c in df.columns if str(c).strip() and c not in exclude]
            if not time_cols:
                continue
            long_rows = []
            for _, row in df.iterrows():
                for tc in time_cols:
                    try:
                        tstr = str(tc).strip()
                        if tstr == "24:00":
                            ts = (pd.to_datetime(row[date_col]) + pd.Timedelta(days=1)).strftime(
                                "%Y-%m-%d 00:00:00.000"
                            )
                        else:
                            ts = merge_datetime(row[date_col], tc)
                        if ts:
                            long_rows.append({
                                "timestamp": ts,
                                name_col: row[name_col],
                                value_col: pd.to_numeric(row[tc], errors="coerce"),
                            })
                    except Exception:
                        continue
            if long_rows:
                all_data.append(pd.DataFrame(long_rows))
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values=value_col, aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_unit_date_mapped(input_dir: Path, task: dict) -> None:
    """日期制机组状态表：多个文本列分别映射为数值后 pivot 为宽表（无时点列）"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    date_col = task.get("date_col", "日期")
    name_col = task["name_col"]
    mapped_outputs = task.get("mapped_outputs", [])
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[: df.shape[1]]
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col, name_col])
            df["timestamp"] = df[date_col].apply(extract_date_000)
            df = df[df["timestamp"] != ""]
            for mo in mapped_outputs:
                src_col = mo["col"]
                map_name = mo["value_map"]
                if src_col in df.columns:
                    df[f"{src_col}_数值"] = df[src_col].apply(
                        lambda v, mn=map_name: apply_value_map(v, mn)
                    )
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)

    for mo in mapped_outputs:
        val_col = f"{mo['col']}_数值"
        if val_col not in merged.columns:
            continue
        wide = merged.pivot_table(
            index="timestamp", columns=name_col, values=val_col, aggfunc="first"
        )
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / mo["output_file"])
        if mo.get("mapping_file"):
            write_mapping_csv(out_dir, mo["mapping_file"], mo["value_map"], mo["col"])
    logging.info("已输出: %s", out_dir)


def process_timeseries_multicol(input_dir: Path, task: dict) -> None:
    """多列时间序列 Excel：timestamp + 多个数值列直出（不做 pivot）"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    date_col = task.get("date_col", "日期")
    time_col = task.get("time_col", "时点")
    value_col_names = task.get("value_col_names", [])
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[: df.shape[1]]
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            for vc in value_col_names:
                if vc in df.columns:
                    df[vc] = pd.to_numeric(df[vc], errors="coerce")
            df = df.dropna(subset=[date_col])
            df["timestamp"] = df.apply(
                lambda r: merge_datetime(r[date_col], r[time_col]), axis=1
            )
            keep = ["timestamp"] + [vc for vc in value_col_names if vc in df.columns]
            df = df[df["timestamp"] != ""][keep]
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_timeseries_csv(input_dir: Path, task: dict) -> None:
    """多列时间序列 CSV：timestamp + 多个数值列直出"""
    out_dir = ensure_output_dir(task["output_dir"])
    encoding = task.get("file_encoding", "utf-8")
    header_row = task.get("header_row", 0)
    date_col = task.get("date_col", "日期")
    time_col = task.get("time_col", "时点")
    value_col_names = task.get("value_col_names", [])
    all_data = []

    for f in sorted(input_dir.glob("*.csv")):
        try:
            df = pd.read_csv(f, encoding=encoding, header=header_row)
            if df.shape[0] < 1:
                continue
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            for vc in value_col_names:
                if vc in df.columns:
                    df[vc] = pd.to_numeric(df[vc], errors="coerce")
            df = df.dropna(subset=[date_col])
            df["timestamp"] = df.apply(
                lambda r: merge_datetime(r[date_col], r[time_col]), axis=1
            )
            keep = ["timestamp"] + [vc for vc in value_col_names if vc in df.columns]
            df = df[df["timestamp"] != ""][keep]
            all_data.append(df)
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


def process_tabular_date(input_dir: Path, task: dict) -> None:
    """简单日期表格：添加 timestamp 后按原始行结构直出（无时点列）"""
    out_dir = ensure_output_dir(task["output_dir"])
    cols = task["columns"]
    skip = task.get("skip_rows", 2)
    date_col = task.get("date_col", "日期")
    output_col_names = task.get("output_col_names", [])
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip:
                continue
            df = df.iloc[skip:].copy()
            df.columns = cols[: df.shape[1]]
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col])
            df["timestamp"] = df[date_col].apply(extract_date_000)
            df = df[df["timestamp"] != ""]
            keep = ["timestamp"] + [
                c for c in output_col_names if c in df.columns
            ]
            all_data.append(df[keep])
        except Exception as e:
            logging.warning("跳过 %s: %s", f.name, e)

    if not all_data:
        logging.warning("未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / task["output_file"])
    logging.info("已输出: %s", out_dir)


# ── 处理器注册表 ────────────────────────────────────────────────
PROCESSOR_REGISTRY: dict[str, Any] = {
    "section_constraints":      process_section_constraints,
    "reserve_capacity":         process_reserve_capacity,
    "clearing_quantity":        process_clearing_quantity,
    "channel_numeric":          process_channel_numeric,
    "channel_text":             process_channel_text,
    "node_lmp":                 process_node_lmp,
    "reservoir_level":          process_reservoir_level,
    "reserve_demand":           process_reserve_demand,
    "clearing_overview_excel":  process_clearing_overview_excel,
    "clearing_overview_csv":    process_clearing_overview_csv,
    "unit_commitment":          process_unit_commitment,
    "node_factor":              process_node_factor,
    "maintenance_plan":         process_maintenance_plan,
    "secondary_freq_clearing":  process_secondary_freq_clearing,
    "coal_unit_capacity":       process_coal_unit_capacity,
    "unit_generation_curve":    process_unit_generation_curve,
    "timeseries_multicol":     process_timeseries_multicol,
    "timeseries_csv":          process_timeseries_csv,
    "tabular_date":            process_tabular_date,
    "unit_date_mapped":        process_unit_date_mapped,
}


# ── 主流程 ──────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="批量数据处理脚本")
    parser.add_argument(
        "--config", "-c",
        default=str(Path(__file__).resolve().parent / "config.yaml"),
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "--task", "-t",
        nargs="*",
        help="仅处理指定任务名称（不指定则处理全部）",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        logging.error("配置文件不存在 %s", config_path)
        return 1

    cfg = load_config(config_path)
    base_dir = Path(__file__).resolve().parent
    _init_globals(cfg, base_dir)

    # 初始化日志
    log_dir = cfg.get("global", {}).get("log_dir", str(base_dir / "logs"))
    max_size_mb = cfg.get("global", {}).get("log_max_size_mb", _DEFAULT_MAX_MB)
    os.makedirs(log_dir, exist_ok=True)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _root = logging.getLogger()
    if not _root.handlers:
        _root.setLevel(logging.INFO)
        _ch = logging.StreamHandler()
        _ch.setFormatter(_fmt)
        _root.addHandler(_ch)
        _fh = _SizedTimedRotatingFileHandler(
            os.path.join(log_dir, "batch_process.log"),
            when="midnight", interval=1, backupCount=30, encoding="utf-8",
            max_bytes=max_size_mb * 1024 * 1024,
        )
        _fh.suffix = "%Y%m%d"
        _fh.setFormatter(_fmt)
        _root.addHandler(_fh)

    if not _DATA_ROOT.exists():
        logging.error("数据目录不存在 %s", _DATA_ROOT)
        return 1
    _OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    logging.info("配置文件: %s", config_path)
    logging.info("数据根目录: %s", _DATA_ROOT)
    logging.info("输出目录: %s", _OUTPUT_ROOT)

    filter_tasks = set(args.task) if args.task else None
    tasks = cfg.get("tasks", [])

    for task in tasks:
        name = task["name"]
        if filter_tasks and name not in filter_tasks:
            continue

        task_type = task.get("type")
        processor = PROCESSOR_REGISTRY.get(task_type)
        if not processor:
            logging.warning("[跳过] %s (未知处理类型: %s)", name, task_type)
            continue

        data_dir_name = task.get("data_dir", name)
        input_dir = _DATA_ROOT / data_dir_name
        if task.get("dir_prefix_match"):
            has_data = input_dir.exists() or any(
                d.is_dir() and d.name.startswith(data_dir_name) for d in _DATA_ROOT.iterdir()
            )
            if not has_data:
                logging.warning("[跳过] %s (目录不存在)", name)
                continue
        elif not input_dir.exists():
            logging.warning("[跳过] %s (目录不存在)", name)
            continue
        else:
            if not list(input_dir.glob("*.xlsx")) and not list(input_dir.glob("*.csv")):
                logging.warning("[跳过] %s (无 xlsx/csv 文件)", name)
                continue

        logging.info("[处理] %s ...", name)
        try:
            processor(input_dir, task)
        except Exception as e:
            logging.error("[%s] 处理失败: %s", name, e, exc_info=True)

    logging.info("全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
