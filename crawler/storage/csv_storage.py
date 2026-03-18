"""
CSV 数据存储模块
负责将爬取的数据保存为 CSV 文件
"""

import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Set

import pandas as pd

from utils.logger import get_logger

logger = get_logger()


class CsvStorage:
    """CSV 数据存储管理器"""

    def __init__(self, config: dict):
        storage_config = config.get("storage", {})
        self.output_dir = storage_config.get("output_dir", "./data")
        self.encoding = storage_config.get("encoding", "utf-8-sig")
        os.makedirs(self.output_dir, exist_ok=True)

    def _get_save_dir(self, category: str = "") -> str:
        """根据分类获取数据保存目录。"""
        if category:
            return os.path.join(self.output_dir, self._safe_name(category))
        return self.output_dir

    def save(self, data: List[Dict], task_name: str, date_str: str = "",
             extra_label: str = "", category: str = "") -> Optional[str]:
        """
        将数据保存为 CSV 文件

        命名规则：{数据类别}_{日期}_{其他筛选条件}.csv
        例：实时节点边际电价_2026-02-05_节点1206008004.csv

        Args:
            data: 数据行列表（字典列表）
            task_name: 任务/数据类别名称
            date_str: 日期字符串
            extra_label: 额外筛选条件标签（如节点名称、断面名称）
            category: 分类目录名（如：现货出清结果）

        Returns:
            保存的文件路径，失败返回 None
        """
        if not data:
            logger.warning("[%s] 无数据可保存", task_name)
            return None

        try:
            # 创建分类子目录
            save_dir = self._get_save_dir(category)
            os.makedirs(save_dir, exist_ok=True)

            # 构造文件名
            filename = self._build_filename(task_name, date_str, extra_label)
            filepath = os.path.join(save_dir, filename)

            # 转换为 DataFrame
            df = pd.DataFrame(data)

            # 针对带「序号」列的标准明细表做一次轻量清洗，
            # 过滤掉解析过程中混入的非数据行（如「最新更新日期」说明行）。
            if "序号" in df.columns:
                try:
                    seq = pd.to_numeric(df["序号"], errors="coerce")
                    # 仅保留序号为有效数字的行，其余视为噪声行丢弃
                    df = df.loc[seq.notna()].copy()
                    df["序号"] = seq.loc[seq.notna()].astype(int)
                except Exception:
                    # 清洗失败时保持原始数据，避免影响其它页面
                    pass

            # 丢弃完全为空的列（常见于表头/说明行被误解析出的临时列）
            df = df.dropna(axis=1, how="all")

            df.to_csv(filepath, index=False, encoding=self.encoding)

            logger.info("数据已保存: %s (%d 行, %d 列)",
                        filepath, len(df), len(df.columns))
            return filepath

        except Exception as e:
            logger.error("保存 CSV 失败 [%s]: %s", task_name, e)
            return None

    def append(self, data: List[Dict], filepath: str) -> Optional[str]:
        """
        追加数据到已有 CSV 文件

        Args:
            data: 新数据行
            filepath: 目标文件路径

        Returns:
            文件路径
        """
        if not data:
            return filepath

        try:
            df_new = pd.DataFrame(data)

            if os.path.exists(filepath):
                df_existing = pd.read_csv(filepath, encoding=self.encoding)
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                # 去重
                df_combined.drop_duplicates(inplace=True)
            else:
                df_combined = df_new

            df_combined.to_csv(filepath, index=False, encoding=self.encoding)
            logger.info("数据已追加: %s (现共 %d 行)", filepath, len(df_combined))
            return filepath

        except Exception as e:
            logger.error("追加 CSV 失败 [%s]: %s", filepath, e)
            return None

    def _build_filename(self, task_name: str, date_str: str = "",
                         extra_label: str = "") -> str:
        """
        构造 CSV 文件名

        Args:
            task_name: 任务名称
            date_str: 日期
            extra_label: 额外标签

        Returns:
            文件名
        """
        parts = [self._safe_name(task_name)]

        if date_str:
            parts.append(date_str)

        if extra_label:
            parts.append(self._safe_name(extra_label))

        return "_".join(parts) + ".csv"

    @staticmethod
    def _safe_name(name: str) -> str:
        """
        将名称转为安全的文件名（去除特殊字符）

        Args:
            name: 原始名称

        Returns:
            安全的文件名
        """
        # 保留中文、字母、数字、连字符、下划线
        safe = re.sub(r'[^\w\u4e00-\u9fff\uff08\uff09\-]', '_', name)
        # 合并连续下划线
        safe = re.sub(r'_+', '_', safe)
        return safe.strip("_")

    def get_existing_dates(self, task_name: str, category: str = "") -> List[str]:
        """
        获取指定任务已有数据的日期列表（用于增量更新）

        Args:
            task_name: 任务名称
            category: 分类目录

        Returns:
            已存在的日期列表
        """
        dates: Set[str] = set()
        save_dir = self._get_save_dir(category)

        if not os.path.exists(save_dir):
            return []

        safe_task = self._safe_name(task_name)
        for f in os.listdir(save_dir):
            if f.startswith(safe_task) and f.endswith(".csv"):
                # 从文件名中提取日期
                match = re.search(r"(\d{4}-\d{2}-\d{2})", f)
                if match:
                    dates.add(match.group(1))

        return sorted(dates)

    def get_existing_labels_for_date(self, task_name: str, date_str: str,
                                     category: str = "") -> List[str]:
        """
        获取指定任务某日期已存在的标签列表（CSV 存储目录）。
        返回值中的空字符串表示“无标签文件”（如不选/无下拉场景）。
        """
        labels: Set[str] = set()
        save_dir = self._get_save_dir(category)

        if not os.path.exists(save_dir):
            return []

        safe_task = self._safe_name(task_name)
        prefix = f"{safe_task}_{date_str}"
        prefix_with_label = f"{prefix}_"
        for filename in os.listdir(save_dir):
            if not filename.endswith(".csv"):
                continue
            if filename == f"{prefix}.csv":
                labels.add("")
                continue
            if not filename.startswith(prefix_with_label):
                continue

            stem = filename[:-4]  # 去掉 .csv
            label = stem[len(prefix_with_label):].strip()
            if label:
                labels.add(label)

        return sorted(labels)
