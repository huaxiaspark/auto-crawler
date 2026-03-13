"""
数据质量校验模块
提供数据完整性和质量检查功能
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils.logger import get_logger

logger = get_logger()


class DataValidator:
    """数据质量校验器"""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def reset(self):
        """重置错误和警告列表"""
        self.errors = []
        self.warnings = []

    def validate_not_empty(self, data: List[Dict], task_name: str) -> bool:
        """检查数据是否为空"""
        if not data:
            self.errors.append(f"[{task_name}] 数据为空")
            return False
        return True

    def validate_required_fields(self, data: List[Dict], required_fields: List[str],
                                  task_name: str) -> bool:
        """检查必填字段是否存在"""
        if not data:
            return False

        missing = []
        headers = set(data[0].keys())
        for field in required_fields:
            if field not in headers:
                missing.append(field)

        if missing:
            self.errors.append(
                f"[{task_name}] 缺少必填字段: {', '.join(missing)}"
            )
            return False
        return True

    def validate_numeric_range(self, data: List[Dict], field: str,
                                min_val: Optional[float] = None,
                                max_val: Optional[float] = None,
                                task_name: str = "") -> bool:
        """检查数值字段是否在合理范围内"""
        valid = True
        for i, row in enumerate(data):
            val = row.get(field)
            if val is None or val == "":
                continue
            try:
                num = float(str(val).replace(",", ""))
                if min_val is not None and num < min_val:
                    self.warnings.append(
                        f"[{task_name}] 第{i+1}行 {field}={num} 低于最小值 {min_val}"
                    )
                    valid = False
                if max_val is not None and num > max_val:
                    self.warnings.append(
                        f"[{task_name}] 第{i+1}行 {field}={num} 超过最大值 {max_val}"
                    )
                    valid = False
            except (ValueError, TypeError):
                self.warnings.append(
                    f"[{task_name}] 第{i+1}行 {field}={val} 无法转为数值"
                )
                valid = False
        return valid

    def validate_date_continuity(self, dates: List[str], task_name: str = "",
                                  date_format: str = "%Y-%m-%d") -> bool:
        """
        检查日期连续性

        Args:
            dates: 日期字符串列表
            task_name: 任务名称
            date_format: 日期格式

        Returns:
            是否连续
        """
        if len(dates) < 2:
            return True

        parsed_dates = []
        for d in dates:
            try:
                parsed_dates.append(datetime.strptime(d, date_format))
            except ValueError:
                continue

        parsed_dates = sorted(set(parsed_dates))
        missing = []
        for i in range(1, len(parsed_dates)):
            expected = parsed_dates[i - 1] + timedelta(days=1)
            if parsed_dates[i] != expected:
                gap_start = expected.strftime(date_format)
                gap_end = (parsed_dates[i] - timedelta(days=1)).strftime(date_format)
                missing.append(f"{gap_start} ~ {gap_end}")

        if missing:
            self.warnings.append(
                f"[{task_name}] 日期不连续，缺失区间: {'; '.join(missing)}"
            )
            return False
        return True

    def validate_row_count(self, data: List[Dict], expected_min: int,
                            task_name: str = "") -> bool:
        """检查数据行数是否达到预期最小值"""
        if len(data) < expected_min:
            self.warnings.append(
                f"[{task_name}] 数据行数 {len(data)} 少于预期最小值 {expected_min}"
            )
            return False
        return True

    def get_report(self) -> str:
        """生成校验报告"""
        lines = ["=" * 50, "数据质量校验报告", "=" * 50]

        if self.errors:
            lines.append(f"\n错误 ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  [ERROR] {e}")

        if self.warnings:
            lines.append(f"\n警告 ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  [WARN]  {w}")

        if not self.errors and not self.warnings:
            lines.append("\n所有检查通过，数据质量良好。")

        lines.append("=" * 50)
        return "\n".join(lines)


def validate_csv_file(filepath: str) -> Tuple[bool, str]:
    """
    校验 CSV 文件基本质量

    Args:
        filepath: CSV 文件路径

    Returns:
        (是否通过, 报告文本)
    """
    if not os.path.exists(filepath):
        return False, f"文件不存在: {filepath}"

    try:
        df = pd.read_csv(filepath, encoding="utf-8-sig")
    except Exception as e:
        return False, f"读取文件失败: {e}"

    issues = []

    if df.empty:
        issues.append("文件为空")

    if df.isnull().all(axis=1).any():
        null_rows = df.isnull().all(axis=1).sum()
        issues.append(f"存在 {null_rows} 行全空数据")

    if df.duplicated().any():
        dup_count = df.duplicated().sum()
        issues.append(f"存在 {dup_count} 行重复数据")

    if issues:
        return False, "; ".join(issues)

    return True, f"通过 (共 {len(df)} 行, {len(df.columns)} 列)"
