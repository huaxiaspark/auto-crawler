#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量处理 data 目录下各子目录的 Excel/CSV 数据，生成规范的宽表 CSV 文件。

基于示例脚本的逻辑，针对实际数据结构进行适配。
时间格式统一为 YYYY-MM-DD HH:MM:SS.000
"""

from __future__ import annotations

import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

TIME_FMT = "%Y-%m-%d %H:%M:%S.000"
DATA_ROOT = Path(__file__).resolve().parent / "data"
OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def ensure_output_dir(subdir: str) -> Path:
    out = OUTPUT_ROOT / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_csv_with_split(df: pd.DataFrame, base_path: Path, **to_csv_kwargs) -> None:
    """保存 DataFrame 为 CSV。若单文件超过 5MB，则拆分为多个文件。"""
    base_path = Path(base_path)
    if base_path.suffix != ".csv":
        base_path = base_path.with_suffix(".csv")
    opts = {"index": False, "encoding": "utf-8", **to_csv_kwargs}
    df.to_csv(base_path, **opts)
    size = base_path.stat().st_size
    if size <= MAX_FILE_SIZE:
        return
    base_path.unlink()
    n = len(df)
    n_parts = max(2, (size + MAX_FILE_SIZE - 1) // MAX_FILE_SIZE)
    chunk_size = (n + n_parts - 1) // n_parts
    stem, parent = base_path.stem, base_path.parent
    for i in range(n_parts):
        start = i * chunk_size
        end = min((i + 1) * chunk_size, n)
        part = df.iloc[start:end]
        path = parent / f"{stem}_part{i + 1}.csv"
        part.to_csv(path, **opts)
    print(f"    (已拆分为 {n_parts} 个文件)")


def extract_date_000(val) -> str:
    """日期转为当日 00:00:00.000"""
    try:
        if pd.isna(val):
            return ""
        dt = pd.to_datetime(val)
        return dt.strftime("%Y-%m-%d 00:00:00.000")
    except Exception:
        return ""


def merge_datetime(date_val, time_val) -> str:
    """合并日期和时点为标准 timestamp"""
    try:
        date_str = str(date_val).split()[0][:10]
        time_str = str(time_val).strip().zfill(5)  # 00:15
        if len(time_str) == 4:  # 0:15
            time_str = "0" + time_str
        dt = datetime.strptime(f"{date_str} {time_str}:00", "%Y-%m-%d %H:%M:%S")
        return dt.strftime(TIME_FMT)
    except Exception:
        return ""


# ========== 1. 断面约束 ==========
def process_section_constraints(input_dir: Path) -> None:
    out_dir = ensure_output_dir("断面约束")
    all_data = []
    section_info = {}

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name="REPORT0", header=None)
            if df.shape[0] <= 2:
                continue
            df = df.iloc[2:].copy()
            df.columns = ["序号", "日期", "断面名称", "断面描述", "正向传输极限", "反向传输极限"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df["正向传输极限"] = pd.to_numeric(df["正向传输极限"], errors="coerce")
            df["反向传输极限"] = pd.to_numeric(df["反向传输极限"], errors="coerce")
            df = df.dropna(subset=["日期", "断面名称"])
            all_data.append(df)
            for _, row in df.iterrows():
                sn = str(row["断面名称"]).strip()
                if sn and sn != "nan":
                    section_info[sn] = str(row["断面描述"]).strip()
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)

    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged["timestamp"] = merged["日期"].apply(extract_date_000)
    merged = merged[merged["timestamp"] != ""]

    for col_type, name in [("正向传输极限", "断面约束_正向传输极限"), ("反向传输极限", "断面约束_反向传输极限")]:
        wide = merged.pivot_table(index="timestamp", columns="断面名称", values=col_type, aggfunc="first")
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / name)

    info_df = pd.DataFrame([{"断面名称": k, "断面描述": v} for k, v in sorted(section_info.items())])
    info_df.to_csv(out_dir / "断面信息映射.csv", index=False, encoding="utf-8")
    print(f"  已输出: {out_dir}")


# ========== 2. 实时/日前 备用总量 ==========
def process_reserve_capacity(input_dir: Path, output_name: str) -> None:
    out_dir = ensure_output_dir(output_name)
    rows = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] < 3:
                continue
            row = df.iloc[2]
            date_val = row.iloc[1]
            if pd.isna(date_val):
                continue
            dt = pd.to_datetime(date_val)
            ts = dt.strftime(TIME_FMT)
            rows.append({
                "timestamp": ts,
                "上旋最小值": float(row.iloc[2]) if pd.notna(row.iloc[2]) else None,
                "下旋最小值": float(row.iloc[3]) if pd.notna(row.iloc[3]) else None,
            })
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not rows:
        print(f"  未读取到有效数据")
        return
    out_df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(out_df, out_dir / output_name)
    print(f"  已输出: {out_dir}")


# ========== 3. 实时各时段出清现货电量 ==========
def process_clearing_quantity(input_dir: Path) -> None:
    out_dir = ensure_output_dir("实时各时段出清现货电量")
    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= 3:
                continue
            df = df.iloc[3:].copy()
            df.columns = ["序号", "日期", "时点", "出清现货电量"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df["出清现货电量"] = pd.to_numeric(df["出清现货电量"], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged["timestamp"] = merged.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
    merged = merged[merged["timestamp"] != ""][["timestamp", "出清现货电量"]]
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / "实时各时段出清现货电量")
    print(f"  已输出: {out_dir}")


# ========== 4. 通道/断面类宽表（输电通道可用容量、日前联络线计划、重要通道、断面约束及影子价格、实时输电断面约束及阻塞）==========
def process_channel_style(
    input_dir: Path,
    output_name: str,
    name_col: str = "名称",
    value_col: str = "数值",
    skip_rows: int = 2,
    col_names: list | None = None,
) -> None:
    out_dir = ensure_output_dir(output_name)

    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip_rows:
                continue
            df = df.iloc[skip_rows:].copy()
            if col_names:
                df.columns = col_names[:df.shape[1]]
                val_col = col_names[4] if len(col_names) > 4 else "数值"
            else:
                df.columns = ["序号", name_col, "日期", "时点", value_col]
                val_col = value_col
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            if val_col in df.columns:
                df["数值"] = pd.to_numeric(df[val_col], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            df["timestamp"] = df.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
            df = df[df["timestamp"] != ""][["timestamp", name_col if name_col in df.columns else df.columns[1], "数值"]]
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values="数值", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / output_name)
    print(f"  已输出: {out_dir}")


# 是否越限映射：否->0, 是->1, 其他依次映射
LIMIT_EXCEED_MAP = {"否": 0, "是": 1}


def _map_limit_exceed(val) -> int:
    v = str(val).strip()
    return LIMIT_EXCEED_MAP.get(v, len(LIMIT_EXCEED_MAP))


def process_channel_style_text(
    input_dir: Path,
    output_name: str,
    name_col: str = "断面名称",
    value_col: str = "是否越限",
    skip_rows: int = 2,
) -> None:
    """处理值为文本的宽表（如是否越限），映射为数值：否->0, 是->1，并输出映射关系CSV"""
    out_dir = ensure_output_dir(output_name)

    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= skip_rows:
                continue
            df = df.iloc[skip_rows:].copy()
            df.columns = ["序号", name_col, "日期", "时点", value_col]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            df["timestamp"] = df.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
            df = df[df["timestamp"] != ""][["timestamp", name_col, value_col]].copy()
            df["数值"] = df[value_col].apply(_map_limit_exceed)
            df = df[["timestamp", name_col, "数值"]]
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns=name_col, values="数值", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / output_name)

    # 输出是否越限映射关系
    map_df = pd.DataFrame([{"是否越限": k, "映射值": v} for k, v in sorted(LIMIT_EXCEED_MAP.items())])
    map_df = pd.concat([map_df, pd.DataFrame([{"是否越限": "其他", "映射值": 2}])], ignore_index=True)
    map_df.to_csv(out_dir / "是否越限映射.csv", index=False, encoding="utf-8")
    print(f"  已输出: {out_dir} (含是否越限映射.csv)")


def _find_lmp_col(df: pd.DataFrame, keyword: str, fallback_idx: int) -> str:
    """从 DataFrame 列名中查找包含 keyword 的列"""
    for c in df.columns:
        if keyword in str(c):
            return c
    return df.columns[fallback_idx] if len(df.columns) > fallback_idx else df.columns[-1]


def _process_node_lmp_impl(input_dir: Path, output_prefix: str) -> None:
    """节点边际电价通用处理，输出电能量价格、阻塞价格、节点电价三个宽表"""
    out_dir = ensure_output_dir(output_prefix)
    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
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
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    for col_keyword, out_suffix in [
        ("电能量价格", "电能量价格"),
        ("阻塞价格", "阻塞价格"),
        ("节点电价", "节点电价"),
    ]:
        col = _find_lmp_col(merged, col_keyword, {"电能量价格": 3, "阻塞价格": 4, "节点电价": 5}[col_keyword])
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
        sub = merged[["timestamp", "节点名称", col]].copy()
        wide = sub.pivot_table(index="timestamp", columns="节点名称", values=col, aggfunc="first")
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / f"{output_prefix}_{out_suffix}")
    print(f"  已输出: {out_dir}")


# ========== 5. 实时节点边际电价 ==========
def process_node_lmp(input_dir: Path) -> None:
    _process_node_lmp_impl(input_dir, "实时节点边际电价")


# ========== 5b. 日前节点边际电价 ==========
def process_dayahead_node_lmp(input_dir: Path) -> None:
    """日前节点边际电价：支持 data/日前节点边际电价 或 data/日前节点边际电价_* 子目录"""
    if input_dir.exists():
        _process_node_lmp_impl(input_dir, "日前节点边际电价")
        return
    parent = input_dir.parent
    subdirs = sorted([d for d in parent.iterdir() if d.is_dir() and d.name.startswith("日前节点边际电价")])
    if not subdirs:
        print(f"  未找到 日前节点边际电价 数据目录")
        return
    all_data = []
    out_dir = ensure_output_dir("日前节点边际电价")
    for subdir in subdirs:
        for f in sorted(subdir.glob("*.xlsx")):
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
                print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    for col_keyword, out_suffix in [("电能量价格", "电能量价格"), ("阻塞价格", "阻塞价格"), ("节点电价", "节点电价")]:
        col = _find_lmp_col(merged, col_keyword, {"电能量价格": 3, "阻塞价格": 4, "节点电价": 5}[col_keyword])
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
        sub = merged[["timestamp", "节点名称", col]].copy()
        wide = sub.pivot_table(index="timestamp", columns="节点名称", values=col, aggfunc="first")
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / f"日前节点边际电价_{out_suffix}")
    print(f"  已输出: {out_dir}")


# ========== 6. 抽蓄电站水位 ==========
def process_reservoir_level(input_dir: Path) -> None:
    out_dir = ensure_output_dir("抽蓄电站水位")
    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= 2:
                continue
            df = df.iloc[2:].copy()
            df.columns = ["序号", "日期", "描述", "数值"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df["数值"] = pd.to_numeric(df["数值"], errors="coerce")
            df = df.dropna(subset=["日期", "描述"])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            df = df[df["timestamp"] != ""][["timestamp", "描述", "数值"]]
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns="描述", values="数值", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / "抽蓄电站水位")
    print(f"  已输出: {out_dir}")


# ========== 7. 日前正负备用需求 ==========
def process_reserve_demand(input_dir: Path) -> None:
    out_dir = ensure_output_dir("日前正负备用需求")
    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= 2:
                continue
            df = df.iloc[2:].copy()
            df.columns = ["序号", "日期", "备用类型", "备用负荷"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df["备用负荷"] = pd.to_numeric(df["备用负荷"], errors="coerce")
            df = df.dropna(subset=["日期", "备用类型"])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            df = df[df["timestamp"] != ""][["timestamp", "备用类型", "备用负荷"]]
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns="备用类型", values="备用负荷", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / "日前正负备用需求")
    print(f"  已输出: {out_dir}")


# ========== 8. 日前/实时 市场出清概况（Excel）==========
def process_clearing_overview_excel(input_dir: Path, output_name: str) -> None:
    out_dir = ensure_output_dir(output_name)
    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= 2:
                continue
            df = df.iloc[2:].copy()
            df.columns = ["序号", "日期", "出清概况"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期"])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            df = df[df["timestamp"] != ""][["timestamp", "出清概况"]]
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / output_name)
    print(f"  已输出: {out_dir}")


# ========== 9. 实时市场出清概况（CSV）==========
def process_clearing_overview_csv(input_dir: Path) -> None:
    out_dir = ensure_output_dir("实时市场出清概况")
    all_data = []
    for f in sorted(input_dir.glob("*.csv")):
        try:
            df = pd.read_csv(f, encoding="utf-8", header=1)
            if df.shape[0] < 1:
                continue
            if "日期" not in df.columns and df.shape[1] >= 2:
                df.columns = ["日期", "出清概况"] + list(df.columns[2:])
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期"])
            df = df[["日期", "出清概况"]].copy()
            df["timestamp"] = df["日期"].apply(extract_date_000)
            df = df[df["timestamp"] != ""][["timestamp", "出清概况"]]
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / "实时市场出清概况")
    print(f"  已输出: {out_dir}")


# 开停状态映射：停机->0, 开机->1, 其他依次映射
UNIT_STATUS_MAP = {"停机": 0, "开机": 1}


def _map_unit_status(val) -> int:
    v = str(val).strip()
    return UNIT_STATUS_MAP.get(v, 2)  # 其他状态映射为 2


# ========== 10. 日前机组开机安排 ==========
def process_unit_commitment(input_dir: Path) -> None:
    out_dir = ensure_output_dir("日前机组开机安排")

    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f)
            if "机组名称" not in df.columns and df.shape[1] >= 4:
                df.columns = ["机组名称", "日期", "时点", "开停状态"]
            status_col = [c for c in df.columns if "开停" in str(c) or "状态" in str(c)]
            if not status_col:
                status_col = [df.columns[3]] if len(df.columns) > 3 else []
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期", "时点"])
            df["timestamp"] = df.apply(lambda r: merge_datetime(r["日期"], r["时点"]), axis=1)
            df = df[df["timestamp"] != ""].copy()
            if status_col:
                df["状态值"] = df[status_col[0]].apply(_map_unit_status)
            name_col = "机组名称" if "机组名称" in df.columns else df.columns[0]
            all_data.append(df[["timestamp", name_col, "状态值"]].rename(columns={name_col: "机组名称"}))
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns="机组名称", values="状态值", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / "日前机组开机安排")

    # 输出开停状态映射关系
    map_df = pd.DataFrame([{"开停状态": k, "映射值": v} for k, v in sorted(UNIT_STATUS_MAP.items())])
    map_df = pd.concat([map_df, pd.DataFrame([{"开停状态": "其他", "映射值": 2}])], ignore_index=True)
    map_df.to_csv(out_dir / "开停状态映射.csv", index=False, encoding="utf-8")
    print(f"  已输出: {out_dir} (含开停状态映射.csv)")


# ========== 11. 节点分配因子 ==========
def process_node_factor(input_dir: Path) -> None:
    out_dir = ensure_output_dir("节点分配因子")
    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f)
            if "节点名称" not in df.columns and df.shape[1] >= 5:
                df.columns = ["序号", "日期", "时段", "节点名称", "分配因子"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df["分配因子"] = pd.to_numeric(df["分配因子"], errors="coerce")
            df = df.dropna(subset=["日期", "节点名称"])
            # 时段 24:00 视为当日 00:00 或次日 00:00，这里用日期+00:00:00
            df["timestamp"] = df["日期"].apply(extract_date_000)
            df = df[df["timestamp"] != ""][["timestamp", "节点名称", "分配因子"]]
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns="节点名称", values="分配因子", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / "节点分配因子")
    print(f"  已输出: {out_dir}")


# ========== 12. 输变电设备检修计划 ==========
def process_maintenance_plan(input_dir: Path) -> None:
    out_dir = ensure_output_dir("输变电设备检修计划")
    all_data = []
    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, header=None)
            if df.shape[0] <= 2:
                continue
            df = df.iloc[2:].copy()
            df.columns = ["序号", "日期", "设备名称", "设备类型", "开始时间", "结束时间"]
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df["开始时间"] = pd.to_datetime(df["开始时间"], errors="coerce")
            df["结束时间"] = pd.to_datetime(df["结束时间"], errors="coerce")
            df = df.dropna(subset=["日期", "设备名称"])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    merged = merged.sort_values(["timestamp", "设备名称"]).reset_index(drop=True)
    save_csv_with_split(merged, out_dir / "输变电设备检修计划")
    print(f"  已输出: {out_dir}")


# ========== 13. 二次调频出清结果 ==========
def _merge_date_period(date_val, period: int) -> str:
    """业务日期 + 时段(1-5) -> timestamp。5 时段/天：1=00:00, 2=04:48, 3=09:36, 4=14:24, 5=19:12"""
    try:
        dt = pd.to_datetime(date_val)
        date_str = dt.strftime("%Y-%m-%d")
        period = int(period) if pd.notna(period) else 1
        period = max(1, min(5, period))
        offsets = {1: (0, 0), 2: (4, 48), 3: (9, 36), 4: (14, 24), 5: (19, 12)}
        h, m = offsets.get(period, (0, 0))
        return f"{date_str} {h:02d}:{m:02d}:00.000"
    except Exception:
        return ""


def process_secondary_freq_clearing(input_dir: Path) -> None:
    """二次调频出清结果：业务日期+时段 -> 各指标"""
    out_dir = ensure_output_dir("二次调频出清结果")
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name="REPORT0_C_C_C", header=0)
            if df.empty:
                continue
            df["业务日期"] = pd.to_datetime(df["业务日期"], errors="coerce")
            df = df.dropna(subset=["业务日期", "时段"])
            df["timestamp"] = df.apply(lambda r: _merge_date_period(r["业务日期"], r["时段"]), axis=1)
            df = df[df["timestamp"] != ""]
            all_data.append(df)
        except Exception as e:
            try:
                df = pd.read_excel(f, header=0)
                if "业务日期" in df.columns and "时段" in df.columns:
                    df["业务日期"] = pd.to_datetime(df["业务日期"], errors="coerce")
                    df = df.dropna(subset=["业务日期", "时段"])
                    df["timestamp"] = df.apply(lambda r: _merge_date_period(r["业务日期"], r["时段"]), axis=1)
                    df = df[df["timestamp"] != ""]
                    all_data.append(df)
            except Exception:
                print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
                continue

    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    out_cols = ["timestamp", "时段", "调频需求容量", "调频主体边际排序价格", "市场出清价格（中标均价）", "市场供给容量", "市场限价", "最大kp值"]
    out_cols = [c for c in out_cols if c in merged.columns]
    merged = merged[out_cols].sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(merged, out_dir / "二次调频出清结果")
    print(f"  已输出: {out_dir}")


# 当日测试结果映射：不合格->0, 合格->1, 不确定->2, 其他->3
TEST_RESULT_MAP = {"不合格": 0, "合格": 1, "不确定": 2}


def _map_test_result(val) -> int:
    v = str(val).strip()
    return TEST_RESULT_MAP.get(v, 3)


# ========== 14. 省调煤电机组最大出力认定考核公示 ==========
def process_coal_unit_capacity(input_dir: Path) -> None:
    """省调煤电机组最大出力认定考核公示：日期×机组 -> 当日认定最大出力等，当日测试结果做数值映射"""
    out_dir = ensure_output_dir("省调煤电机组最大出力认定考核公示")
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name="REPORT0", header=0)
            if df.empty or "机组名称" not in df.columns:
                continue
            df["机组名称"] = df["机组名称"].ffill()
            df["装机容量（MW）"] = df["装机容量（MW）"].ffill()
            df["月前申报最大出力（MW）"] = df["月前申报最大出力（MW）"].ffill()
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期", "机组名称"])
            df["timestamp"] = df["日期"].apply(extract_date_000)
            df = df[df["timestamp"] != ""]
            if "当日测试结果" in df.columns:
                df["当日测试结果_数值"] = df["当日测试结果"].apply(_map_test_result)
            all_data.append(df)
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
            continue

    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)

    for col_name, out_name in [
        ("当日认定最大出力（MW）", "当日认定最大出力"),
        ("装机容量（MW）", "装机容量"),
        ("月前申报最大出力（MW）", "月前申报最大出力"),
        ("当日测试结果_数值", "当日测试结果"),
    ]:
        if col_name not in merged.columns:
            continue
        wide = merged.pivot_table(index="timestamp", columns="机组名称", values=col_name, aggfunc="first")
        wide.reset_index(inplace=True)
        wide = wide.sort_values("timestamp").reset_index(drop=True)
        save_csv_with_split(wide, out_dir / out_name)

    if "当日测试结果" in merged.columns:
        map_df = pd.DataFrame([{"当日测试结果": k, "映射值": v} for k, v in sorted(TEST_RESULT_MAP.items())])
        map_df = pd.concat([map_df, pd.DataFrame([{"当日测试结果": "其他", "映射值": 3}])], ignore_index=True)
        map_df.to_csv(out_dir / "当日测试结果映射.csv", index=False, encoding="utf-8")

    print(f"  已输出: {out_dir}")


# ========== 15. 机组实际发电曲线 ==========
def process_unit_generation_curve(input_dir: Path) -> None:
    """机组实际发电曲线：日期+96时点，机组为行转为机组为列的宽表，timestamp×机组→发电量"""
    out_dir = ensure_output_dir("机组实际发电曲线")
    all_data = []

    for f in sorted(input_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(f, sheet_name="REPORT0", header=0)
            if df.empty or "机组名称" not in df.columns or "日期" not in df.columns:
                continue
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.dropna(subset=["日期", "机组名称"])
            time_cols = [c for c in df.columns if c not in ("序号", "日期", "机组名称") and str(c).strip()]
            if not time_cols:
                continue
            long_rows = []
            for _, row in df.iterrows():
                date_val = row["日期"]
                unit = row["机组名称"]
                for tc in time_cols:
                    try:
                        tstr = str(tc).strip()
                        if tstr == "24:00":
                            dt = pd.to_datetime(date_val) + pd.Timedelta(days=1)
                            ts = dt.strftime("%Y-%m-%d 00:00:00.000")
                        else:
                            ts = merge_datetime(date_val, tc)
                        if ts:
                            val = pd.to_numeric(row[tc], errors="coerce")
                            long_rows.append({"timestamp": ts, "机组名称": unit, "发电量": val})
                    except Exception:
                        continue
            if long_rows:
                all_data.append(pd.DataFrame(long_rows))
        except Exception as e:
            print(f"  警告: 跳过 {f.name}: {e}", file=sys.stderr)
            continue

    if not all_data:
        print(f"  未读取到有效数据")
        return
    merged = pd.concat(all_data, ignore_index=True)
    wide = merged.pivot_table(index="timestamp", columns="机组名称", values="发电量", aggfunc="first")
    wide.reset_index(inplace=True)
    wide = wide.sort_values("timestamp").reset_index(drop=True)
    save_csv_with_split(wide, out_dir / "机组实际发电曲线")
    print(f"  已输出: {out_dir}")


# ========== 主流程 ==========
HANDLERS = [
    ("断面约束", process_section_constraints),
    ("实时备用总量", lambda d: process_reserve_capacity(d, "实时备用总量")),
    ("日前备用总量", lambda d: process_reserve_capacity(d, "日前备用总量")),
    ("实时各时段出清现货电量", process_clearing_quantity),
    ("输电通道可用容量", lambda d: process_channel_style(d, "输电通道可用容量", "通道名称", "可用容量(MW)", 2, ["序号", "通道名称", "日期", "时点", "可用容量"])),
    ("日前联络线计划", lambda d: process_channel_style(d, "日前联络线计划", "通道", "电力值(MW)", 2, ["序号", "通道", "日期", "时点", "数值"])),
    ("重要通道实际输电情况", lambda d: process_channel_style(d, "重要通道实际输电情况", "断面名称", "潮流(MW)", 2, ["序号", "断面名称", "日期", "时点", "数值"])),
    ("断面约束情况及影子价格", lambda d: process_channel_style(d, "断面约束情况及影子价格", "断面名称", "阻塞价格(元/MWh)", 2, ["序号", "断面名称", "日期", "时点", "数值"])),
    ("实时输电断面约束及阻塞", lambda d: process_channel_style_text(d, "实时输电断面约束及阻塞")),
    ("实时节点边际电价", process_node_lmp),
    ("日前节点边际电价", process_dayahead_node_lmp),
    ("抽蓄电站水位", process_reservoir_level),
    ("日前正负备用需求", process_reserve_demand),
    ("日前市场出清概况", lambda d: process_clearing_overview_excel(d, "日前市场出清概况")),
    ("实时市场出清概况", process_clearing_overview_csv),
    ("日前机组开机安排", process_unit_commitment),
    ("节点分配因子", process_node_factor),
    ("输变电设备检修计划", process_maintenance_plan),
    ("二次调频出清结果", process_secondary_freq_clearing),
    ("省调煤电机组最大出力认定考核公示", process_coal_unit_capacity),
    ("机组实际发电曲线", process_unit_generation_curve),
]


def main() -> int:
    data_root = DATA_ROOT
    if not data_root.exists():
        print(f"错误: 数据目录不存在 {data_root}")
        return 1
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"数据根目录: {data_root}")
    print(f"输出目录: {OUTPUT_ROOT}\n")

    for subdir_name, handler in HANDLERS:
        subdir = data_root / subdir_name
        if subdir_name == "日前节点边际电价":
            has_data = subdir.exists() or any(
                d.is_dir() and d.name.startswith("日前节点边际电价") for d in data_root.iterdir()
            )
            if not has_data:
                print(f"[跳过] {subdir_name} (目录不存在)")
                continue
        elif not subdir.exists():
            print(f"[跳过] {subdir_name} (目录不存在)")
            continue
        else:
            has_xlsx = list(subdir.glob("*.xlsx"))
            has_csv = list(subdir.glob("*.csv"))
            if not has_xlsx and not has_csv:
                print(f"[跳过] {subdir_name} (无 xlsx/csv 文件)")
                continue
        print(f"[处理] {subdir_name} ...")
        try:
            handler(subdir)
        except Exception as e:
            print(f"  错误: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    print("\n全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
