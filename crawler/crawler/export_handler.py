"""
导出处理模块
处理「原样导出」和「导出」按钮的点击和文件下载

注意：导出按钮在 iframe 内，需要通过 self.ctx 操作。
下载事件仍然需要通过 self.page（主页面）来监听。
"""

import os
import re
import time
import glob as glob_module
from typing import List, Optional, Set, Union

from playwright.sync_api import Page, Frame, TimeoutError as PlaywrightTimeout

from utils.logger import get_logger
from utils.timing import sleep_seconds

logger = get_logger()


class ExportHandler:
    """导出功能处理器"""

    def __init__(self, page: Page, config: dict):
        self.page = page
        # ctx 指向实际操作 DOM 的上下文（Frame 或 Page）
        self.ctx: Union[Page, Frame] = page
        self.config = config
        self.download_dir = os.path.abspath(
            config.get("browser", {}).get("download_dir", "./data/exports")
        )
        self.export_timeout = config.get("request", {}).get("export_timeout", 60) * 1000  # 转为毫秒
        os.makedirs(self.download_dir, exist_ok=True)

    @staticmethod
    def _safe_export_name(name: str) -> str:
        """将任务名/标签转为导出文件名中的安全片段。"""
        return (name or "").replace("/", "_").replace("\\", "_").strip()

    def _build_export_stem(self, task_name: str, date_str: str,
                           extra_label: str = "") -> str:
        """
        构造导出文件名（不含扩展名）的稳定前缀。
        """
        safe_task = self._safe_export_name(task_name)
        safe_extra = self._safe_export_name(extra_label)

        parts = [safe_task]
        if date_str:
            parts.append(date_str)
        if safe_extra:
            parts.append(safe_extra)
        return "_".join(parts)

    def has_existing_export(self, task_name: str, date_str: str,
                            extra_label: str = "") -> bool:
        """
        判断指定任务/日期/标签的导出文件是否已存在。
        """
        stem = self._build_export_stem(task_name, date_str, extra_label)
        pattern = os.path.join(self.download_dir, f"{stem}.*")
        for filepath in glob_module.glob(pattern):
            if os.path.isfile(filepath) and os.path.getsize(filepath) > 0:
                return True
        return False

    def get_existing_export_dates(self, task_name: str) -> List[str]:
        """
        获取指定任务在导出目录中已存在的日期列表（用于增量跳过）。
        """
        safe_task = self._safe_export_name(task_name)
        dates: Set[str] = set()
        if not os.path.exists(self.download_dir):
            return []

        pattern = re.compile(
            rf"^{re.escape(safe_task)}_(\d{{4}}-\d{{2}}-\d{{2}})(?:_|$)"
        )
        for filename in os.listdir(self.download_dir):
            fullpath = os.path.join(self.download_dir, filename)
            if not os.path.isfile(fullpath):
                continue
            stem = os.path.splitext(filename)[0]
            match = pattern.match(stem)
            if match:
                dates.add(match.group(1))
        return sorted(dates)

    def get_existing_export_labels_for_date(self, task_name: str,
                                            date_str: str) -> List[str]:
        """
        获取指定任务某日期在导出目录中已完成的标签列表。
        返回值中的空字符串表示“无标签导出”（如不选/无下拉场景）。
        """
        labels: Set[str] = set()
        if not os.path.exists(self.download_dir):
            return []

        prefix = self._build_export_stem(task_name, date_str)
        plain_name = f"{prefix}."
        with_label_name = f"{prefix}_"
        for filename in os.listdir(self.download_dir):
            fullpath = os.path.join(self.download_dir, filename)
            if not os.path.isfile(fullpath):
                continue
            if filename.startswith(plain_name):
                labels.add("")
                continue
            if not filename.startswith(with_label_name):
                continue

            stem = os.path.splitext(filename)[0]
            label = stem[len(with_label_name):].strip()
            if label:
                labels.add(label)

        return sorted(labels)

    def try_export(self, export_type: str = "原样导出",
                    task_name: str = "", date_str: str = "",
                    extra_label: str = "") -> Optional[str]:
        """
        尝试点击导出按钮并下载文件（带重试机制）

        Args:
            export_type: 导出按钮文本（"原样导出" 或 "导出"）
            task_name: 任务名称（用于文件命名）
            date_str: 日期字符串
            extra_label: 额外标签（如节点名称等）

        Returns:
            下载文件路径，失败返回 None
        """
        logger.info("尝试导出: %s [%s]", export_type, task_name)

        max_retries = 3
        retry_interval = 5  # 秒

        for attempt in range(1, max_retries + 1):
            try:
                # 查找导出按钮（在 iframe 内）
                export_btn = self._find_export_button(export_type)
                if export_btn is None:
                    if attempt < max_retries:
                        logger.warning("未找到「%s」按钮，%d秒后重试 (%d/%d)", 
                                     export_type, retry_interval, attempt, max_retries)
                        sleep_seconds(retry_interval)
                        continue
                    else:
                        logger.warning("未找到「%s」按钮，已重试%d次", export_type, max_retries)
                        return None

                # 使用 Playwright 的下载事件处理（download 事件在主 Page 上）
                with self.page.expect_download(timeout=self.export_timeout) as download_info:
                    export_btn.click()

                download = download_info.value

                # 构造目标文件名
                suffix = os.path.splitext(download.suggested_filename)[1] or ".csv"
                stem = self._build_export_stem(task_name, date_str, extra_label)
                filename = f"{stem}{suffix}"

                filepath = os.path.join(self.download_dir, filename)

                # 保存文件
                download.save_as(filepath)
                logger.info("导出文件已保存: %s", filepath)
                return filepath

            except PlaywrightTimeout:
                if attempt < max_retries:
                    logger.warning("导出超时，%d秒后重试 (%d/%d) [%s]", 
                                 retry_interval, attempt, max_retries, task_name)
                    sleep_seconds(retry_interval)
                    continue
                else:
                    logger.warning("导出超时，可能按钮不可用或无数据，已重试%d次 [%s]", 
                                 max_retries, task_name)
                    return None
            except Exception as e:
                if attempt < max_retries:
                    logger.warning("导出失败，%d秒后重试 (%d/%d) [%s]: %s", 
                                 retry_interval, attempt, max_retries, task_name, e)
                    sleep_seconds(retry_interval)
                    continue
                else:
                    logger.error("导出失败，已重试%d次 [%s]: %s", max_retries, task_name, e)
                    return None

        return None

    def _find_export_button(self, export_type: str):
        """
        查找导出按钮（在 iframe 内查找）

        适配多种页面类型：
        - Element UI 页面：标准 <button> 或 <a> 元素
        - FineReport 报表：<button class="fr-btn-text x-emb-excel"> 元素

        注意：部分页面按钮文字中包含空格（如"导 出"而非"导出"），
        本方法会同时匹配有空格和无空格两种变体。

        Args:
            export_type: 按钮文本（如 "原样导出"、"导出"、"导 出"）

        Returns:
            按钮元素或 None
        """
        # 生成无空格和有空格的变体，确保两种都能匹配
        export_type_no_space = export_type.replace(" ", "")
        variants = list(dict.fromkeys([export_type, export_type_no_space]))

        # 按优先级尝试多种选择器
        selectors = [
            # FineReport 导出按钮
            'button.x-emb-excel',
            'button.fr-btn-text.x-emb-excel',
        ]
        for variant in variants:
            selectors.extend([
                f'button.x-emb-excel:has-text("{variant}")',
                f'button:has-text("{variant}")',
                f'a:has-text("{variant}")',
                f'span:has-text("{variant}")',
                f'text={variant}',
            ])

        for sel in selectors:
            try:
                btn = self.ctx.locator(sel).first
                if btn.is_visible():
                    logger.debug("找到导出按钮（选择器: %s）", sel)
                    return btn
            except Exception:
                continue

        # 回退：查找包含"导出"文字的按钮（去除空格后比较，兼容"导 出"等变体）
        try:
            btns = self.ctx.locator("button").all()
            for btn in btns:
                text = btn.text_content().strip()
                text_no_space = text.replace(" ", "")
                if (export_type_no_space in text_no_space
                        or "导出" in text_no_space):
                    return btn
        except Exception:
            pass

        return None

    def is_export_available(self, export_type: str = "原样导出") -> bool:
        """
        检查导出按钮是否可用

        Args:
            export_type: 按钮文本

        Returns:
            是否可用
        """
        btn = self._find_export_button(export_type)
        if btn is None:
            return False
        try:
            return btn.is_visible() and btn.is_enabled()
        except Exception:
            return False
