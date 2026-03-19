"""
页面爬取模块
针对不同页面类型的具体爬取逻辑

关键设计：
    该平台的页面内容（日期输入框、下拉框、查询/导出按钮、数据表格等）
    都渲染在 iframe 内部，而不是主页面中。
    导航后需检测 iframe 并切换到其 Frame 上下文，否则无法找到任何控件。
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from playwright.sync_api import Page, Frame

from crawler.navigator import Navigator
from crawler.filter_handler import FilterHandler
from crawler.export_handler import ExportHandler
from crawler.pagination import PaginationHandler
from crawler.data_extractor import DataExtractor
from storage.csv_storage import CsvStorage
from utils.parser import parse_clearing_summary_batch
from utils.logger import get_logger

logger = get_logger()


class PageCrawler:
    """
    通用页面爬取器
    根据任务配置自动选择爬取策略（导出 / 表格解析 / 分页 等）
    """

    def __init__(self, page: Page, config: dict):
        self.page = page
        self.config = config
        self.navigator = Navigator(page, config)
        self.filter_handler = FilterHandler(page, config)
        self.export_handler = ExportHandler(page, config)
        self.pagination = PaginationHandler(page, config)
        self.extractor = DataExtractor(page)
        self.storage = CsvStorage(config)

        self.date_interval = config.get("request", {}).get("date_interval", 2)
        self.retry_times = config.get("request", {}).get("retry_times", 3)
        self.retry_interval = config.get("request", {}).get("retry_interval", 5)

        # 记住当前任务使用的 iframe ID，用于重新检测时优先匹配
        self._current_iframe_id: Optional[str] = None
        # 记录当前任务首次导航时的 iframe ID，用于检测页面是否被刷新回首页
        self._task_iframe_id: Optional[str] = None
        # 「不选」下拉清空状态：清空过一次后，后续日期迭代无需重复清空
        self._dropdown_cleared_for_none: bool = False

    # ── iframe 上下文切换 ────────────────────────────────────────

    def _drill_into_nested_iframe(self, frame: Frame) -> Optional[Frame]:
        """
        检查 Frame 内是否包含嵌套的 iframe，如果有则进入最内层。

        该平台部分页面使用 3 层 iframe 嵌套：
        - 主页面 → pxf-settlement-outnetpub iframe → 内层 id="iframe"（FineReport 报表）
        - 内层 iframe 包含实际的表单控件（日期输入框、查询按钮、数据表格）

        FineReport iframe 特征：
        - id="iframe"
        - 包含 .fr-trigger-editor（日期控件）、.fr-form-imgboard（按钮）
        - 或包含 input, button, table 等通用控件

        Args:
            frame: 上一层 iframe 的 Frame 对象

        Returns:
            内层 iframe 的 Frame 对象，如果没有嵌套则返回 None
        """
        try:
            inner_iframes = frame.query_selector_all("iframe")
            for inner_el in inner_iframes:
                try:
                    inner_frame = inner_el.content_frame()
                    if inner_frame:
                        # 检查内层 iframe 是否有实际的表单控件
                        count = inner_frame.locator(
                            "input, button, table, "
                            ".fr-trigger-editor, .fr-form-imgboard, "
                            ".el-date-editor, .el-select, .el-input"
                        ).count()
                        if count > 0:
                            inner_id = inner_el.get_attribute("id") or "unknown"
                            logger.info(
                                "发现嵌套内层 iframe: %s (包含 %d 个表单控件, URL: %s)",
                                inner_id, count,
                                inner_frame.url[:80] if inner_frame.url else "N/A",
                            )
                            return inner_frame
                except Exception:
                    continue
        except Exception as e:
            logger.debug("检查嵌套 iframe 失败: %s", e)
        return None

    def _get_content_frame(self) -> Optional[Frame]:
        """
        检测当前页面中可见的内容 iframe 并返回其 Frame 对象。

        该平台使用 iframe 加载各个功能页面内容：
        - 主页面（self.page）包含侧边栏菜单和 tab 切换
        - 功能页面（日期筛选、表格、导出按钮等）在 iframe 内

        平台存在两种 iframe 结构：
        1. 二层结构：主页面 → 内容 iframe（Element UI 页面，如实时节点边际电价）
        2. 三层结构：主页面 → 中间 iframe → 内层 iframe（FineReport 报表页面）
           中间 iframe 通常 id="pxf-settlement-outnetpub"
           内层 iframe 通常 id="iframe"，包含 FineReport 表单控件

        本方法会自动检测并穿透嵌套结构，返回最内层包含实际控件的 Frame。

        Returns:
            最内层内容 iframe 的 Frame 对象，未找到返回 None
        """
        # 方法0：如果记录了 iframe ID，优先按 ID 查找
        if self._current_iframe_id:
            try:
                target = self.page.query_selector(
                    f'iframe#{self._current_iframe_id}'
                )
                if target and target.is_visible():
                    frame = target.content_frame()
                    if frame:
                        logger.info(
                            "通过已记录ID找到内容区 iframe: %s (URL: %s)",
                            self._current_iframe_id,
                            frame.url[:80] if frame.url else "N/A",
                        )
                        # ★ 检查是否有嵌套的内层 iframe
                        inner = self._drill_into_nested_iframe(frame)
                        if inner:
                            return inner
                        return frame
            except Exception as e:
                logger.debug("通过ID查找iframe失败: %s", e)

        # 方法1：通过 query_selector 找到可见的 iframe 元素
        try:
            iframes = self.page.query_selector_all("iframe")
            for iframe_el in iframes:
                try:
                    if iframe_el.is_visible():
                        frame = iframe_el.content_frame()
                        if frame:
                            iframe_id = iframe_el.get_attribute("id") or "unknown"
                            logger.info(
                                "找到内容区 iframe: %s (URL: %s)",
                                iframe_id,
                                frame.url[:80] if frame.url else "N/A",
                            )
                            # 记住这个 iframe 的 ID
                            self._current_iframe_id = iframe_id

                            # ★ 检查是否有嵌套的内层 iframe（FineReport 三层结构）
                            inner = self._drill_into_nested_iframe(frame)
                            if inner:
                                return inner
                            return frame
                except Exception:
                    continue
        except Exception as e:
            logger.debug("方法1查找iframe失败: %s", e)

        # 方法2：遍历所有 frames，找到有实际内容的非主 frame
        try:
            for frame in self.page.frames:
                if frame == self.page.main_frame:
                    continue
                try:
                    # 检查 frame 内是否有表单控件或按钮
                    # 同时支持 Element UI 和 FineReport 控件
                    count = frame.locator(
                        "button, input, table, "
                        ".el-date-editor, .el-select, .el-input, "
                        ".fr-trigger-editor, .fr-form-imgboard"
                    ).count()
                    if count > 0:
                        logger.info(
                            "找到内容区 frame (方法2): %s",
                            frame.url[:80] if frame.url else "N/A",
                        )
                        return frame
                except Exception:
                    continue
        except Exception as e:
            logger.debug("方法2查找iframe失败: %s", e)

        logger.warning("未检测到内容区 iframe，将使用主页面上下文")
        return None

    def _switch_to_content_frame(self):
        """
        检测 iframe 并将所有 handler 的操作上下文切换到 iframe 内。

        包含重试机制：如果首次只找到外层 iframe（内层尚未加载），
        会等待并重新尝试穿透嵌套 iframe。

        如果未检测到 iframe，handler 将继续使用主页面上下文。
        """
        frame = self._get_content_frame()
        if frame:
            self.filter_handler.ctx = frame
            self.export_handler.ctx = frame
            self.extractor.ctx = frame
            self.pagination.ctx = frame
            logger.info("已将操作上下文切换到 iframe")

            # 检查是否可能还有未加载的内层 iframe
            # 如果当前 frame 没有 FineReport/ElementUI 控件，
            # 但有一个 iframe 子元素，说明内层可能还在加载
            try:
                control_count = frame.locator(
                    "input, .fr-trigger-editor, .el-date-editor"
                ).count()
                inner_iframe_count = len(frame.query_selector_all("iframe"))
                if control_count == 0 and inner_iframe_count > 0:
                    logger.info("外层 iframe 中发现内层 iframe 但控件未加载，等待加载...")
                    for retry in range(5):
                        time.sleep(2)
                        inner = self._drill_into_nested_iframe(frame)
                        if inner:
                            self.filter_handler.ctx = inner
                            self.export_handler.ctx = inner
                            self.extractor.ctx = inner
                            self.pagination.ctx = inner
                            logger.info("内层 iframe 加载完成 (第%d次尝试)", retry + 1)
                            return
                    logger.warning("内层 iframe 未能在预期时间内加载完成")
            except Exception as e:
                logger.debug("检查内层 iframe 加载状态失败: %s", e)
        else:
            # 回退到主页面
            self.filter_handler.ctx = self.page
            self.export_handler.ctx = self.page
            self.extractor.ctx = self.page
            self.pagination.ctx = self.page

    def _is_frame_valid(self) -> bool:
        """
        检查当前 iframe 上下文是否仍然有效（未被 detach）。

        iframe 可能因页面重新渲染、Vue 路由切换等原因被替换，
        此时旧的 Frame 引用变为 detached 状态，所有操作都会失败。

        Returns:
            True 表示 Frame 仍有效，False 表示已 detached
        """
        ctx = self.filter_handler.ctx
        # 如果 ctx 就是 page 本身，不需要检查
        if ctx == self.page:
            return True
        try:
            # 尝试一个轻量操作来验证 Frame 是否仍然有效
            ctx.evaluate("() => document.readyState")
            return True
        except Exception:
            return False

    def _ensure_content_frame(self):
        """
        确保 iframe 上下文有效。如果 Frame 已 detached，则重新检测 iframe。

        该平台的 Vue.js 应用在页面切换或异步加载时，可能会替换 iframe 元素，
        导致之前获取的 Frame 引用失效。此方法在关键操作前调用，
        确保操作上下文始终指向有效的 iframe。

        重新检测到 iframe 后，会校验其 ID 是否与任务 iframe 一致。
        若不一致（如检测到首页的 pxf-common-portal 而非任务的 iframe），
        说明页面已被刷新回首页，抛出异常让上层触发恢复导航。
        """
        if self._is_frame_valid():
            return

        logger.warning("检测到 iframe 已 detached，正在重新检测...")

        # 等待一小段时间让页面稳定
        time.sleep(1)

        # 重试多次，因为新的 iframe 可能还在加载中
        for attempt in range(5):
            frame = self._get_content_frame()
            if frame:
                # ★ 校验：重新检测到的 iframe 是否与任务 iframe 一致
                # 若不一致，说明页面已被刷新回首页（如检测到 pxf-common-portal）
                if (self._task_iframe_id
                        and self._current_iframe_id != self._task_iframe_id):
                    logger.warning(
                        "重新检测到的 iframe '%s' 与任务 iframe '%s' 不一致，"
                        "页面可能已被刷新回首页",
                        self._current_iframe_id, self._task_iframe_id,
                    )
                    raise RuntimeError(
                        f"iframe 已变更（期望 '{self._task_iframe_id}'，"
                        f"实际 '{self._current_iframe_id}'），页面可能已被刷新回首页"
                    )

                self.filter_handler.ctx = frame
                self.export_handler.ctx = frame
                self.extractor.ctx = frame
                self.pagination.ctx = frame
                logger.info("已重新检测到 iframe 并切换上下文 (第%d次尝试)", attempt + 1)
                return
            logger.debug("第%d次重新检测 iframe 未找到，等待后重试...", attempt + 1)
            time.sleep(2)

        # 最终回退到主页面
        logger.warning("多次重试仍未检测到 iframe，回退到主页面上下文")
        self.filter_handler.ctx = self.page
        self.export_handler.ctx = self.page
        self.extractor.ctx = self.page
        self.pagination.ctx = self.page

    # ── 页面自动刷新恢复 ──────────────────────────────────────────

    def _is_on_task_page(self) -> bool:
        """
        主动检测当前页面是否仍在任务页面（而非被刷新回首页）。

        检测逻辑（按优先级）：
        1. 如果记录了任务 iframe ID，检查该 iframe 是否仍存在且可见且 Frame 有效；
           若 _task_iframe_id 存在但 iframe 不可用，直接返回 False。
        2. 若未记录 iframe ID，检查当前 ctx 是否有任务页面特有的控件
           （日期选择器、下拉框等，排除通用 input/button 以避免首页误匹配）。
        3. 检测侧边栏「信息披露」节点是否仍处于展开状态；
           若已收起，说明页面可能已被刷新回首页。

        Returns:
            True 表示仍在任务页面，False 表示已被跳转（需要恢复导航）
        """
        # 检测1：任务 iframe 是否仍存在且可见
        if self._task_iframe_id:
            try:
                target = self.page.query_selector(f'iframe#{self._task_iframe_id}')
                if target and target.is_visible():
                    frame = target.content_frame()
                    if frame:
                        frame.evaluate("() => document.readyState")
                        return True
            except Exception:
                pass
            # 任务 iframe 不再可用
            logger.debug("任务 iframe '%s' 已不可用", self._task_iframe_id)
            return False

        # 检测2：当前 ctx 是否有任务页面特有的控件
        # 使用日期选择器等高区分度选择器，避免首页 iframe 中的通用 input/button 误匹配
        try:
            ctx = self.filter_handler.ctx
            if ctx != self.page:
                count = ctx.locator(
                    ".el-date-editor, .el-select, "
                    ".fr-trigger-editor, .fr-form-imgboard"
                ).count()
                if count > 0:
                    return True
        except Exception:
            pass

        # 检测3：侧边栏「信息披露」节点是否仍处于展开状态
        # 如果页面被刷新回首页，侧边栏会重新加载，「信息披露」展开状态会丢失
        try:
            sidebar_ready = self.navigator._is_tree_node_expanded("信息披露")
            if not sidebar_ready:
                # 侧边栏已收起说明页面可能已刷新
                logger.debug("侧边栏「信息披露」节点未展开，疑似已回首页")
                return False
        except Exception:
            pass

        return True

    def _recover_navigation(self, category: str, task_name: str,
                             subcategory: Optional[str] = None):
        """
        检测页面是否被自动刷新回首页，如果是则重新导航到目标页面。

        该平台可能在长时间操作（如导出等待）期间自动刷新页面或会话超时，
        导致浏览器回到首页。此时 iframe 会变为首页的 iframe（如 pxf-common-portal），
        与目标页面的 iframe（如 pxf-phbsx-other-outer）不同，
        所有后续操作（查找按钮、设置日期等）都会失败。

        检测由 _is_on_task_page() 统一完成（iframe 可用性 → 表单控件 → 侧边栏状态）。

        Args:
            category: 分类目录（如 "现货实时数据"）
            task_name: 任务名称（如 "实时节点边际电价"）
            subcategory: 子分类路径（可选）

        Returns:
            True 如果执行了恢复导航，False 如果页面正常无需恢复
        """
        # _is_on_task_page() 内部已覆盖 iframe 可用性、表单控件、侧边栏状态三级检测
        if self._is_on_task_page():
            return False

        # ★ 确认需要恢复：重新导航到目标页面
        logger.warning(
            "确认需要恢复导航（任务 iframe '%s'），正在重新导航到「%s」...",
            self._task_iframe_id, task_name,
        )

        try:
            # 重置导航状态，确保侧边栏重新展开
            self.navigator._info_disclosure_expanded = False
            self.navigator._current_category = None

            self.navigator.navigate_to_page(category, task_name, subcategory)

            # 重置 iframe 记录并重新检测
            self._current_iframe_id = None
            self._dropdown_cleared_for_none = False  # 页面恢复后需重新清空下拉框
            self._switch_to_content_frame()
            time.sleep(3)
            self._ensure_content_frame()

            # 更新任务 iframe ID（重新导航后可能略有不同）
            self._task_iframe_id = self._current_iframe_id

            logger.info("页面恢复导航完成，已重新切换到「%s」", task_name)
            return True
        except Exception as e:
            logger.error("页面恢复导航失败: %s", e)
            raise

    # ── 动态下拉列表支持 ──────────────────────────────────────────

    def _wait_for_dropdown_refresh(self, dropdown_label: str, max_wait: float = 5.0):
        """
        等待下拉列表在日期切换后刷新完成。

        日前联络线计划、实时联络线出力、实时市场机组出力及电价三个页面，
        设置日期后会自动触发下拉列表数据刷新。需要等待刷新完成后再获取选项。

        Args:
            dropdown_label: 下拉框标签（用于日志）
            max_wait: 最大等待秒数
        """
        try:
            self.filter_handler.ctx.wait_for_load_state(
                "networkidle", timeout=int(max_wait * 1000)
            )
        except Exception:
            pass
        time.sleep(1)
        logger.debug("下拉列表「%s」刷新等待完成", dropdown_label)

    def _get_channels_meta_path(self) -> str:
        """获取通道元数据文件路径"""
        download_dir = os.path.abspath(
            self.config.get("browser", {}).get("download_dir", "./data/exports")
        )
        return os.path.join(download_dir, "_channels_meta.json")

    def _save_channels_meta(self, task_name: str, date_str: str,
                            channels: List[str]):
        """
        将某任务某日期实际获取到的下拉选项列表写入元数据文件。

        元数据文件供 data-verify 校验侧使用，替代硬编码的 standard_channels。

        Args:
            task_name: 任务名称
            date_str: 日期字符串
            channels: 该日期下实际的下拉选项列表
        """
        meta_path = self._get_channels_meta_path()
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)

        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("元数据文件读取失败，将重新创建: %s", meta_path)

        if task_name not in meta:
            meta[task_name] = {}
        meta[task_name][date_str] = channels

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        logger.debug("已保存通道元数据: %s / %s → %d 个选项",
                     task_name, date_str, len(channels))

    # ── 主流程 ────────────────────────────────────────────────────

    @staticmethod
    def _normalize_progress_label(label: str) -> str:
        """
        将下拉标签标准化，用于跨目录（导出目录/CSV目录）匹配已完成状态。
        """
        normalized = (label or "").strip()
        normalized = normalized.replace("/", "_").replace("\\", "_")
        normalized = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", normalized)
        normalized = re.sub(r"_+", "_", normalized)
        return normalized.strip("_")

    def _build_option_progress_key(self, option_text: str) -> str:
        """
        将下拉选项文本映射到进度键。
        「不选」与空选项统一为无标签键（空字符串）。
        """
        normalized_option = (option_text or "").strip()
        if not normalized_option or normalized_option == "不选":
            return ""
        return self._normalize_progress_label(normalized_option)

    def _get_completed_option_keys_for_date(self, task_name: str,
                                            date_str: str,
                                            category: str,
                                            has_export: bool) -> Set[str]:
        """
        获取某任务某日期下已完成的下拉选项键（来自 CSV 与导出文件）。
        """
        completed: Set[str] = set()

        stored_labels = self.storage.get_existing_labels_for_date(
            task_name=task_name,
            date_str=date_str,
            category=category,
        )
        for label in stored_labels:
            completed.add(self._normalize_progress_label(label))

        if has_export:
            exported_labels = self.export_handler.get_existing_export_labels_for_date(
                task_name=task_name,
                date_str=date_str,
            )
            for label in exported_labels:
                completed.add(self._normalize_progress_label(label))

        return completed

    def crawl_task(self, task_name: str, task_config: dict,
                    start_date: str, end_date: str,
                    batch_queries: Optional[List[Tuple[str, str, str]]] = None,
                    date_list: Optional[List[str]] = None):
        """
        执行单个爬取任务

        Args:
            task_name: 任务名称（如：实时节点边际电价）
            task_config: 任务配置字典
            start_date: 起始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            batch_queries: 仅对「日前联络线计划」有效；若提供，则为 [(start, end, 联络线名称), ...]，按此列表执行指定日期+筛选条件，不遍历全部下拉选项。
            date_list: 若提供，则仅爬取该列表中的日期，忽略 start_date/end_date 范围（用于补充缺失数据）。
        """
        if not task_config.get("enabled", True):
            logger.info("任务「%s」已禁用，跳过", task_name)
            return

        logger.info("=" * 70)
        logger.info("开始爬取任务: %s", task_name)
        logger.info("日期范围: %s ~ %s", start_date, end_date)
        logger.info("=" * 70)

        category = task_config.get("category", "")
        subcategory = task_config.get("subcategory", None)
        has_dropdown = task_config.get("has_dropdown", False)
        dropdown_label = task_config.get("dropdown_label", "")
        has_export = task_config.get("has_export", False)
        export_type = task_config.get("export_type", "原样导出")
        has_pagination = task_config.get("has_pagination", False)
        has_page_size = task_config.get("has_page_size", False)
        is_clearing_summary = "出清概况" in task_name
        export_all = task_config.get("export_all", False)
        dropdown_skip_none = task_config.get("dropdown_skip_none", False)
        dropdown_refresh_on_date = task_config.get("dropdown_refresh_on_date", False)

        # 获取已爬取的日期（增量更新）
        # 含下拉且非 export_all 的任务，按“日期+下拉选项”粒度跳过，避免误判。
        use_date_level_skip = not (has_dropdown and not export_all)
        existing_dates: Set[str] = set()
        if use_date_level_skip:
            existing_dates.update(self.storage.get_existing_dates(task_name, category))
            if has_export:
                existing_dates.update(self.export_handler.get_existing_export_dates(task_name))
            logger.info("已有数据日期: %d 天", len(existing_dates))
        else:
            logger.info("任务含下拉选项，启用「日期+下拉」粒度断点续跑")

        # 导航到目标页面
        try:
            self.navigator.navigate_to_page(category, task_name, subcategory)
        except Exception as e:
            logger.error("导航到「%s」失败: %s", task_name, e)
            return

        # ★ 重置 iframe ID 记录（新任务可能使用不同的 iframe）
        self._current_iframe_id = None
        self._task_iframe_id = None
        self._dropdown_cleared_for_none = False

        # ★ 关键步骤：导航完成后，检测 iframe 并切换上下文
        self._switch_to_content_frame()

        # 等待内容区完全加载（iframe 内容可能需要较长时间）
        time.sleep(3)

        # ★ 二次确认：iframe 可能在加载过程中被替换，需要重新检测
        self._ensure_content_frame()

        # ★ 记录任务的 iframe ID，用于后续检测页面是否被刷新回首页
        self._task_iframe_id = self._current_iframe_id
        if self._task_iframe_id:
            logger.debug("记录任务 iframe ID: %s", self._task_iframe_id)

        # 设置每页条数（如果支持）
        if has_page_size:
            try:
                self.filter_handler.set_page_size(50)
            except Exception:
                logger.warning("设置每页条数失败，使用默认值")

        # 含下拉筛选且配置了 dropdown_skip_none 的任务：批量查询模式（日期+筛选条件由文件指定，不遍历全部下拉选项）
        if dropdown_skip_none and batch_queries:
            self._crawl_task_tieline_batch(
                task_name=task_name,
                task_config=task_config,
                batch_queries=batch_queries,
                category=category,
                dropdown_label=dropdown_label,
                has_export=has_export,
                export_type=export_type,
                has_pagination=has_pagination,
                is_clearing_summary=is_clearing_summary,
            )
            logger.info("任务「%s」完成（批量模式）", task_name)
            return

        # 获取下拉选项（先确保 iframe 上下文有效）
        # 如果 export_all 为 True，则跳过下拉选项获取，导出按钮会一次导出全部数据
        # 如果 dropdown_refresh_on_date 为 True，则延迟到每个日期设置后再获取
        dropdown_options = []
        dropdown_select_none = task_config.get("dropdown_select_none", False)
        if has_dropdown and not export_all and not dropdown_refresh_on_date:
            self._ensure_content_frame()
            dropdown_options = self.filter_handler.get_dropdown_options(dropdown_label)
            if not dropdown_options:
                # 获取下拉选项失败，可能页面已被刷新，尝试恢复导航后重新获取
                logger.warning("未获取到「%s」的下拉选项，尝试恢复导航后重新获取",
                               dropdown_label)
                try:
                    recovered = self._recover_navigation(
                        category, task_name, subcategory)
                    if recovered:
                        self._ensure_content_frame()
                        dropdown_options = self.filter_handler.get_dropdown_options(
                            dropdown_label)
                except Exception as e:
                    logger.error("恢复导航后重新获取下拉选项失败: %s", e)

            if not dropdown_options:
                logger.warning("未获取到「%s」的下拉选项，尝试不选择直接查询",
                               dropdown_label)
                dropdown_options = [""]  # 空字符串表示不选择

            # ── 「不选」优化 ──
            # 当配置启用 dropdown_select_none 且下拉选项中包含「不选」时，
            # 仅选择「不选」即可获取全部数据，无需逐一遍历每个选项，
            # 大幅提高爬取效率（从 N 次查询/导出缩减为 1 次）。
            elif dropdown_select_none:
                if "不选" in dropdown_options:
                    logger.info(
                        "下拉选项中包含「不选」且任务启用了 dropdown_select_none，"
                        "仅选择「不选」以获取全部数据（原 %d 个选项 → 1 个）",
                        len(dropdown_options),
                    )
                    dropdown_options = ["不选"]
                else:
                    logger.warning(
                        "任务启用了 dropdown_select_none，但下拉选项中未找到「不选」，"
                        "将使用全部 %d 个选项逐一爬取",
                        len(dropdown_options),
                    )
        elif export_all:
            logger.info("任务「%s」启用了 export_all 模式，跳过下拉选项获取，"
                        "将通过导出按钮一次性导出所有数据", task_name)
        elif dropdown_refresh_on_date:
            logger.info("任务「%s」启用了 dropdown_refresh_on_date，"
                        "将在每个日期设置后重新获取下拉选项", task_name)

        # 日期迭代
        if date_list is None:
            date_list = self._generate_date_list(start_date, end_date)
        else:
            logger.info("使用指定日期列表（共 %d 天），跳过增量检查", len(date_list))
        total_dates = len(date_list)

        for date_idx, date_str in enumerate(date_list):
            # 增量更新检查
            if use_date_level_skip and date_str in existing_dates:
                logger.info("[%d/%d] 跳过已有数据: %s", date_idx + 1, total_dates, date_str)
                continue

            completed_option_keys: Set[str] = set()
            if has_dropdown and dropdown_options and not export_all:
                completed_option_keys = self._get_completed_option_keys_for_date(
                    task_name=task_name,
                    date_str=date_str,
                    category=category,
                    has_export=has_export,
                )

                # 如果当前日期下所有应执行的选项都已完成，则整日跳过。
                pending_exists = False
                for option in dropdown_options:
                    normalized_option = (option or "").strip()
                    if dropdown_skip_none and normalized_option == "不选":
                        continue
                    if self._build_option_progress_key(option) not in completed_option_keys:
                        pending_exists = True
                        break

                if not pending_exists:
                    logger.info("[%d/%d] 跳过已完成日期: %s（下拉选项均已存在）",
                                date_idx + 1, total_dates, date_str)
                    continue

            logger.info("[%d/%d] 处理日期: %s", date_idx + 1, total_dates, date_str)

            # ★ 先设置日期（export_all 或 无下拉选项但有导出按钮的任务使用 quick_mode）
            # quick_mode 跳过 _wait_for_filters_ready 和日期面板关闭操作（Tab/Escape/点击空白），
            # 因为后续直接点击导出按钮，导出操作会自动关闭任何打开的面板，节省约 3 秒。
            use_quick_mode = export_all or (has_export and not has_dropdown)
            try:
                self._ensure_content_frame()
                self.filter_handler.set_date(date_str, quick_mode=use_quick_mode)
                if not use_quick_mode:
                    time.sleep(0.5)
            except Exception as e:
                logger.error("[%d/%d] 设置日期失败 [%s]: %s",
                             date_idx + 1, total_dates, date_str, e)
                # ★ 检测是否页面被刷新回首页，若是则恢复导航
                try:
                    recovered = self._recover_navigation(
                        category, task_name, subcategory)
                    if recovered:
                        logger.info("页面已恢复，重新设置日期: %s", date_str)
                        self.filter_handler.set_date(
                            date_str, quick_mode=use_quick_mode)
                        if not use_quick_mode:
                            time.sleep(0.5)
                    else:
                        # 页面正常但设置日期仍失败，跳过此日期
                        time.sleep(self.date_interval)
                        continue
                except Exception as recover_err:
                    logger.error("恢复导航后重新设置日期仍失败: %s", recover_err)
                    time.sleep(self.date_interval)
                    continue

            # ★ 日期设置成功后，根据 export_all 配置决定后续操作
            if export_all:
                # 导出全部数据模式：直接导出，无需逐一选择下拉选项
                self._crawl_export_all(
                    task_name=task_name,
                    task_config=task_config,
                    date_str=date_str,
                    category=category,
                    export_type=export_type,
                )
            elif has_dropdown and dropdown_refresh_on_date:
                # ★ 动态下拉列表模式：每个日期设置后重新获取下拉选项
                self._ensure_content_frame()
                self._wait_for_dropdown_refresh(dropdown_label)
                dropdown_options = self.filter_handler.get_dropdown_options(dropdown_label)
                if not dropdown_options:
                    # 获取下拉选项失败，可能页面已被刷新，尝试恢复导航后重新获取
                    logger.warning("[%d/%d] 日期 %s 未获取到「%s」的下拉选项，尝试恢复导航",
                                   date_idx + 1, total_dates, date_str, dropdown_label)
                    try:
                        recovered = self._recover_navigation(
                            category, task_name, subcategory)
                        if recovered:
                            self._ensure_content_frame()
                            self.filter_handler.set_date(date_str, quick_mode=False)
                            time.sleep(0.5)
                            self._wait_for_dropdown_refresh(dropdown_label)
                            dropdown_options = self.filter_handler.get_dropdown_options(
                                dropdown_label)
                    except Exception as e:
                        logger.error("恢复导航后重新获取下拉选项失败: %s", e)

                if not dropdown_options:
                    logger.warning("[%d/%d] 日期 %s 未获取到「%s」的下拉选项，跳过",
                                   date_idx + 1, total_dates, date_str, dropdown_label)
                    time.sleep(self.date_interval)
                    continue

                # 过滤掉「不选」
                effective_options = [
                    opt for opt in dropdown_options
                    if not (dropdown_skip_none and (opt or "").strip() == "不选")
                ]

                logger.info("[%d/%d] 日期 %s 下拉选项: %d 个（有效 %d 个）",
                            date_idx + 1, total_dates, date_str,
                            len(dropdown_options), len(effective_options))

                # 保存当前日期的通道元数据（供 data-verify 使用）
                self._save_channels_meta(task_name, date_str, effective_options)

                # 获取已完成的选项键
                completed_option_keys = self._get_completed_option_keys_for_date(
                    task_name=task_name,
                    date_str=date_str,
                    category=category,
                    has_export=has_export,
                )

                # 检查是否所有选项都已完成
                pending_options = [
                    opt for opt in effective_options
                    if self._build_option_progress_key(opt) not in completed_option_keys
                ]
                if not pending_options:
                    logger.info("[%d/%d] 跳过已完成日期: %s（下拉选项均已存在）",
                                date_idx + 1, total_dates, date_str)
                    time.sleep(self.date_interval)
                    continue

                # 对每个下拉选项迭代
                for opt_idx, option in enumerate(effective_options):
                    option_key = self._build_option_progress_key(option)
                    if option_key in completed_option_keys:
                        logger.info(
                            "  下拉选项 [%d/%d]: %s，已完成，跳过",
                            opt_idx + 1,
                            len(effective_options),
                            option or "(默认)",
                        )
                        continue

                    logger.info("  下拉选项 [%d/%d]: %s",
                                opt_idx + 1, len(effective_options), option or "(默认)")
                    success = self._crawl_single(
                        task_name=task_name,
                        task_config=task_config,
                        date_str=date_str,
                        category=category,
                        dropdown_label=dropdown_label,
                        dropdown_value=option,
                        has_export=has_export,
                        export_type=export_type,
                        has_pagination=has_pagination,
                        is_clearing_summary=is_clearing_summary,
                        date_already_set=True,
                    )
                    if success:
                        completed_option_keys.add(option_key)
            elif has_dropdown and dropdown_options:
                # 对每个下拉选项迭代
                for opt_idx, option in enumerate(dropdown_options):
                    normalized_option = (option or "").strip()
                    # 若配置了 dropdown_skip_none，跳过「不选」选项
                    if dropdown_skip_none and normalized_option == "不选":
                        logger.info(
                            "  下拉选项 [%d/%d]: %s，按任务规则跳过",
                            opt_idx + 1,
                            len(dropdown_options),
                            normalized_option,
                        )
                        continue

                    option_key = self._build_option_progress_key(option)
                    if option_key in completed_option_keys:
                        logger.info(
                            "  下拉选项 [%d/%d]: %s，已完成，跳过",
                            opt_idx + 1,
                            len(dropdown_options),
                            option or "(默认)",
                        )
                        continue

                    logger.info("  下拉选项 [%d/%d]: %s",
                                opt_idx + 1, len(dropdown_options), option or "(默认)")
                    success = self._crawl_single(
                        task_name=task_name,
                        task_config=task_config,
                        date_str=date_str,
                        category=category,
                        dropdown_label=dropdown_label,
                        dropdown_value=option,
                        has_export=has_export,
                        export_type=export_type,
                        has_pagination=has_pagination,
                        is_clearing_summary=is_clearing_summary,
                        date_already_set=True,
                    )
                    if success:
                        completed_option_keys.add(option_key)
            else:
                self._crawl_single(
                    task_name=task_name,
                    task_config=task_config,
                    date_str=date_str,
                    category=category,
                    dropdown_label="",
                    dropdown_value="",
                    has_export=has_export,
                    export_type=export_type,
                    has_pagination=has_pagination,
                    is_clearing_summary=is_clearing_summary,
                    date_already_set=True,
                )

            time.sleep(self.date_interval)

        logger.info("任务「%s」完成", task_name)

    def _crawl_task_tieline_batch(
        self,
        task_name: str,
        task_config: dict,
        batch_queries: List[Tuple[str, str, str]],
        category: str,
        dropdown_label: str,
        has_export: bool,
        export_type: str,
        has_pagination: bool,
        is_clearing_summary: bool,
    ):
        """
        含下拉筛选且配置了 dropdown_skip_none 的任务专用：
        按「日期+筛选条件」列表批量执行，不获取页面全部下拉选项。
        batch_queries: [(start_date, end_date, 筛选选项名称), ...]
        """
        batch_pairs: List[Tuple[str, str]] = []
        for start_date, end_date, option in batch_queries:
            for date_str in self._generate_date_list(start_date, end_date):
                batch_pairs.append((date_str, option))
        total = len(batch_pairs)
        logger.info("批量模式：共 %d 个「日期+%s」组合", total, dropdown_label)
        for idx, (date_str, option) in enumerate(batch_pairs):
            logger.info("[%d/%d] 日期: %s, %s: %s", idx + 1, total, date_str, dropdown_label, option)
            try:
                self._ensure_content_frame()
                self.filter_handler.set_date(date_str, quick_mode=False)
                time.sleep(0.5)
            except Exception as e:
                logger.error("设置日期失败 [%s]: %s", date_str, e)
                try:
                    recovered = self._recover_navigation(
                        category, task_name, task_config.get("subcategory")
                    )
                    if recovered:
                        self.filter_handler.set_date(date_str, quick_mode=False)
                        time.sleep(0.5)
                    else:
                        time.sleep(self.date_interval)
                        continue
                except Exception as recover_err:
                    logger.error("恢复导航后设置日期仍失败: %s", recover_err)
                    time.sleep(self.date_interval)
                    continue
            success = self._crawl_single(
                task_name=task_name,
                task_config=task_config,
                date_str=date_str,
                category=category,
                dropdown_label=dropdown_label,
                dropdown_value=option,
                has_export=has_export,
                export_type=export_type,
                has_pagination=has_pagination,
                is_clearing_summary=is_clearing_summary,
                date_already_set=True,
            )
            if not success:
                logger.warning("跳过失败组合: %s / %s", date_str, option)
            time.sleep(self.date_interval)

    def _crawl_single(self, task_name: str, task_config: dict,
                       date_str: str, category: str,
                       dropdown_label: str, dropdown_value: str,
                       has_export: bool, export_type: str,
                       has_pagination: bool,
                       is_clearing_summary: bool,
                       date_already_set: bool = False) -> bool:
        """
        执行单次爬取（一个日期 + 一个下拉选项组合）

        支持自动重试。每次重试前会主动检测页面是否被刷新回首页：
        - 通过 _is_on_task_page() 主动检测任务 iframe 是否仍然有效
        - 若已跳回首页，则重新导航到目标页面并恢复 iframe 上下文，再重新执行本次任务
        - 重试时始终重新设置日期（页面刷新后日期状态会丢失）

        Args:
            date_already_set: 日期已在主流程中设置，首次尝试时跳过重复设置；
                              重试时始终重新设置日期（页面刷新后日期会丢失）
        """
        subcategory = task_config.get("subcategory", None)

        for attempt in range(1, self.retry_times + 1):
            try:
                # ★ 重试时（attempt > 1）：优先检测并恢复页面导航。
                # 必须在 _ensure_content_frame 之前执行，因为页面刷新后
                # _ensure_content_frame 可能会切换到首页的 iframe 而非任务页面的 iframe。
                if attempt > 1:
                    try:
                        self._recover_navigation(category, task_name, subcategory)
                    except Exception as nav_err:
                        logger.error("恢复导航失败: %s", nav_err)
                        # 恢复导航失败，页面状态不确定，跳过本次尝试
                        if attempt < self.retry_times:
                            time.sleep(self.retry_interval)
                            continue
                        else:
                            logger.error("已达最大重试次数，跳过此记录")
                            return False

                # 确保 iframe 上下文有效
                self._ensure_content_frame()

                self._do_crawl_single(
                    task_name, task_config, date_str, category,
                    dropdown_label, dropdown_value,
                    has_export, export_type, has_pagination,
                    is_clearing_summary,
                    # 首次尝试且主流程已设置日期时，跳过重复设置；
                    # 重试时始终重新设置日期（页面刷新后日期会丢失）
                    skip_date_set=(date_already_set and attempt == 1),
                )
                return True  # 成功则退出
            except Exception as e:
                logger.error("爬取失败 [%s][%s][%s] 第%d次: %s",
                             task_name, date_str, dropdown_value, attempt, e)
                if attempt < self.retry_times:
                    logger.info("等待 %d 秒后重试...", self.retry_interval)
                    time.sleep(self.retry_interval)
                else:
                    logger.error("已达最大重试次数，跳过此记录")
        return False

    # ── 导出全部数据模式 ──────────────────────────────────────────

    def _crawl_export_all(self, task_name: str, task_config: dict,
                           date_str: str, category: str, export_type: str):
        """
        导出全部数据模式（含重试）：日期已在主流程中设置，直接执行导出。

        适用于导出按钮可一次导出所有选项数据的页面（如实时节点边际电价）。
        这类页面虽然有下拉筛选控件，但点击导出按钮时会忽略下拉选择，
        将该日期下所有选项（如所有节点）的数据一并导出到一个文件中。

        注意：首次调用时日期已由主流程设置完毕，重试时会重新设置日期
        （因为 iframe 可能在失败过程中被刷新）。

        当检测到页面被自动刷新回首页时，会重新导航到目标页面后再重试。

        Args:
            task_name: 任务名称
            task_config: 任务配置
            date_str: 日期字符串
            category: 分类目录
            export_type: 导出按钮文本
        """
        subcategory = task_config.get("subcategory", None)

        for attempt in range(1, self.retry_times + 1):
            try:
                # ★ 重试时（attempt > 1）：优先检测并恢复页面导航，
                # 必须在 _ensure_content_frame 之前执行。
                if attempt > 1:
                    try:
                        self._recover_navigation(category, task_name, subcategory)
                    except Exception as nav_err:
                        logger.error("恢复导航失败: %s", nav_err)
                        # 恢复导航失败，页面状态不确定，跳过本次尝试
                        if attempt < self.retry_times:
                            time.sleep(self.retry_interval)
                            continue
                        else:
                            logger.error("已达最大重试次数，跳过 [%s][%s]",
                                         task_name, date_str)
                            return

                self._ensure_content_frame()
                # 重试时需要重新设置日期（页面刷新后日期会丢失）
                if attempt > 1:
                    self.filter_handler.set_date(date_str, quick_mode=True)
                    time.sleep(0.5)
                self._do_crawl_export_all(
                    task_name, task_config, date_str, category, export_type,
                )
                return
            except Exception as e:
                logger.error("导出全部数据失败 [%s][%s] 第%d次: %s",
                             task_name, date_str, attempt, e)
                if attempt < self.retry_times:
                    logger.info("等待 %d 秒后重试...", self.retry_interval)
                    time.sleep(self.retry_interval)
                else:
                    logger.error("已达最大重试次数，跳过 [%s][%s]", task_name, date_str)

    def _do_crawl_export_all(self, task_name: str, task_config: dict,
                              date_str: str, category: str, export_type: str):
        """
        实际执行全部数据导出的核心逻辑。

        日期已在主流程中设置完毕，本方法直接执行导出操作。
        恢复导航统一由外层 _crawl_export_all 的重试循环负责，
        本方法仅在当前页面上尝试导出，失败则抛异常交给外层重试。

        流程：
        1. 直接点击导出按钮
        2. 若未成功，尝试先点击查询再导出
        3. 仍失败则抛异常，由外层重试（含恢复导航）
        """

        def do_export():
            return self.export_handler.try_export(
                export_type=export_type,
                task_name=task_name,
                date_str=date_str,
                extra_label="",
            )

        # 1. 直接点击导出按钮（日期已设置）
        filepath = do_export()
        if filepath:
            logger.info("导出全部数据成功: %s", filepath)
            return

        # 2. 直接导出未成功，尝试先点击查询再导出
        logger.info("直接导出未成功，尝试先点击查询再导出...")
        self._ensure_content_frame()
        self.filter_handler.click_query_button()
        time.sleep(1)

        filepath = do_export()
        if filepath:
            logger.info("查询后导出全部数据成功: %s", filepath)
            return

        # 3. 仍失败，抛异常交给外层重试（外层会负责恢复导航）
        raise RuntimeError(f"导出全部数据失败: {task_name} {date_str}")

    # ── 单次爬取（日期+下拉组合） ────────────────────────────────

    def _do_crawl_single(self, task_name: str, task_config: dict,
                          date_str: str, category: str,
                          dropdown_label: str, dropdown_value: str,
                          has_export: bool, export_type: str,
                          has_pagination: bool,
                          is_clearing_summary: bool,
                          skip_date_set: bool = False):
        """实际执行单次爬取的核心逻辑

        Args:
            skip_date_set: 为 True 时跳过日期设置（主流程已设置过，避免重复）
        """

        # 「不选」模式下，文件命名不带筛选标签（因为返回的是全部数据）
        extra_label = "" if dropdown_value == "不选" else dropdown_value

        # 1. 设置日期（如果主流程已设置过则跳过，节省 ~2-7 秒）
        if not skip_date_set:
            self.filter_handler.set_date(date_str)
            time.sleep(0.5)

        # 2. 设置下拉选项（先选下拉，再点查询）
        if dropdown_label and dropdown_value:
            if dropdown_value == "不选":
                # 「不选」优化：清空下拉框输入值，使查询不带筛选条件，返回全部数据
                if not self._dropdown_cleared_for_none:
                    # 首次清空：使用完整方法（含页面类型检测和等待）
                    self.filter_handler.clear_dropdown_input(dropdown_label)
                    self._dropdown_cleared_for_none = True
                    time.sleep(0.3)
                else:
                    # ★ 后续清空：使用快速方法（直接通过 FineReport JS API 调用，~0.1s）
                    # 注意：此方法仅适用于 FineReport 页面；若快速清空失败，
                    # 内部会自动回退到完整的 clear_dropdown_input 方法。
                    # set_date 可能触发 FineReport 参数联动刷新下拉框，
                    # 因此仍需确保下拉框为空。
                    self.filter_handler.quick_clear_fr_dropdown(dropdown_label)
            else:
                self.filter_handler.select_dropdown_option(dropdown_label, dropdown_value)
                time.sleep(0.5)

        # 3. 点击查询
        self.filter_handler.click_query_button()
        time.sleep(1)

        # 4. 尝试导出（优先使用导出）
        if has_export:
            filepath = self.export_handler.try_export(
                export_type=export_type,
                task_name=task_name,
                date_str=date_str,
                extra_label=extra_label,
            )
            if filepath:
                logger.info("通过导出获取数据成功: %s", filepath)
                return

            # 导出失败时，始终触发重新导航重试
            # （大多数情况是页面自动刷新回首页导致，无需回退到表格解析）
            raise RuntimeError(
                f"导出失败，触发重新导航重试 [{task_name}][{date_str}]"
            )

        # 5. 从表格提取数据
        # 进入表格提取前，先确认仍在任务页面（防止页面已刷新回首页）
        if not self._is_on_task_page():
            raise RuntimeError(
                f"进入表格提取前检测到页面已回到首页，触发重新导航 [{task_name}][{date_str}]"
            )

        all_data = []

        if has_pagination:
            # 带分页的提取
            all_data = self._extract_with_pagination(task_name)
        else:
            # 无分页，滚动加载后提取
            self.pagination.scroll_to_load_all()
            headers, rows = self.extractor.extract_table()
            all_data = rows

        if not all_data:
            # 提取到 0 行时，检测是否已回到首页（首页表格也会返回 0 行）
            if not self._is_on_task_page():
                raise RuntimeError(
                    f"表格提取结果为空且检测到页面已回到首页，触发重新导航 [{task_name}][{date_str}]"
                )
            logger.warning("未提取到数据 [%s][%s][%s]", task_name, date_str,
                           dropdown_value or "(不选)")
            return

        # 6. 特殊处理：出清概况文本解析
        if is_clearing_summary:
            all_data = parse_clearing_summary_batch(all_data)

        # 7. 数据清洗
        all_data = self._clean_data(all_data)

        # 8. 添加更新时间--（如果不需要，删除即可）
        update_time = self.extractor.extract_update_time()
        if update_time:
            for row in all_data:
                row["最新更新日期"] = update_time

        # 9. 保存 CSV
        self.storage.save(
            data=all_data,
            task_name=task_name,
            date_str=date_str,
            extra_label=extra_label,
            category=category,
        )

    def _extract_with_pagination(self, task_name: str) -> List[Dict]:
        """
        带分页的数据提取

        Args:
            task_name: 任务名称

        Returns:
            所有页的数据
        """
        all_data = []
        total_pages = self.pagination.get_total_pages()
        logger.info("共 %d 页数据", total_pages)

        current_page = 1
        while True:
            logger.info("正在提取第 %d/%d 页...", current_page, total_pages)

            headers, rows = self.extractor.extract_table()
            all_data.extend(rows)

            if current_page >= total_pages:
                break

            if not self.pagination.has_next_page():
                break

            if not self.pagination.go_next_page():
                break

            current_page += 1

        logger.info("分页提取完成: 共 %d 行数据", len(all_data))
        return all_data

    def _clean_data(self, data: List[Dict]) -> List[Dict]:
        """
        基础数据清洗

        - 去除前后空白
        - 转换数字类型
        - 处理缺失值

        Args:
            data: 原始数据

        Returns:
            清洗后的数据
        """
        cleaned = []
        for row in data:
            new_row = {}
            for key, value in row.items():
                if isinstance(value, str):
                    value = value.strip()
                    # 跳过序号列的转换
                    if key == "序号":
                        new_row[key] = value
                        continue
                    # 尝试数值转换
                    if value.replace(".", "", 1).replace("-", "", 1).isdigit():
                        try:
                            if "." in value:
                                value = float(value)
                            else:
                                value = int(value)
                        except (ValueError, TypeError):
                            pass
                new_row[key] = value if value != "" else None
            cleaned.append(new_row)
        return cleaned

    @staticmethod
    def _generate_date_list(start_date: str, end_date: str) -> List[str]:
        """
        生成日期列表（含首尾）

        Args:
            start_date: 起始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）

        Returns:
            日期字符串列表
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return dates
