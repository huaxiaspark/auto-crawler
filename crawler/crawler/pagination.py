"""
分页处理模块
处理表格的分页控件和滚动加载

支持两种前端框架的分页：
- Element UI：使用 .el-pagination 组件
- FineReport 报表：使用内置的页面导航工具栏

注意：分页控件在 iframe 内部，需要通过 self.ctx 指向正确的 Frame 上下文。
"""

import re
import time
from typing import Optional, Union

from playwright.sync_api import Page, Frame, TimeoutError as PlaywrightTimeout

from utils.logger import get_logger

logger = get_logger()


class PaginationHandler:
    """分页处理器"""

    def __init__(self, page: Page, config: dict):
        self.page = page
        # ctx 指向实际操作 DOM 的上下文（Frame 或 Page）
        self.ctx: Union[Page, Frame] = page
        self.config = config
        self.page_interval = config.get("request", {}).get("page_interval", 2)

    def _is_finereport_page(self) -> bool:
        """检测当前上下文是否为 FineReport 报表页面"""
        try:
            fr_count = self.ctx.locator(
                ".fr-trigger-editor, .fr-form-imgboard, .para-container"
            ).count()
            return fr_count > 0
        except Exception:
            return False

    def get_total_pages(self) -> int:
        """
        获取总页数

        Returns:
            总页数，如果无法获取返回 1
        """
        try:
            # FineReport 页面：通过 JS API 获取总页数
            if self._is_finereport_page():
                return self._fr_get_total_pages()

            # Element UI 路径
            # 方式1：查找 "/N" 样式的总页数显示
            pager_selectors = [
                ".el-pagination__total",
                ".el-pager li:last-child",
                'text=/\\/\\d+/',
            ]

            for sel in pager_selectors:
                try:
                    el = self.ctx.locator(sel).first
                    if el.is_visible():
                        text = el.text_content().strip()
                        nums = re.findall(r"\d+", text)
                        if nums:
                            return int(nums[-1])
                except Exception:
                    continue

            # 方式2：查找页码输入框旁边的总页数
            try:
                total_text = self.ctx.locator('text=/\\/\\s*\\d+/').first
                if total_text.is_visible():
                    text = total_text.text_content()
                    match = re.search(r"/\s*(\d+)", text)
                    if match:
                        return int(match.group(1))
            except Exception:
                pass

            logger.debug("未找到总页数，默认为 1 页")
            return 1

        except Exception as e:
            logger.warning("获取总页数失败: %s", e)
            return 1

    def has_next_page(self) -> bool:
        """
        检查是否有下一页

        Returns:
            是否有下一页
        """
        try:
            # FineReport 页面
            if self._is_finereport_page():
                return self._fr_has_next_page()

            # Element UI 路径
            next_selectors = [
                'button:has-text("下一页")',
                ".el-pagination .btn-next",
                'text=下一页',
                "button.btn-next",
            ]

            for sel in next_selectors:
                try:
                    btn = self.ctx.locator(sel).first
                    if btn.is_visible():
                        # 检查按钮是否禁用
                        disabled = btn.get_attribute("disabled")
                        is_disabled = btn.locator(".is-disabled").count() > 0
                        has_disabled_class = "disabled" in (
                            btn.get_attribute("class") or ""
                        )
                        if disabled is not None or is_disabled or has_disabled_class:
                            return False
                        return True
                except Exception:
                    continue

            return False

        except Exception:
            return False

    def go_next_page(self) -> bool:
        """
        翻到下一页

        Returns:
            是否成功翻页
        """
        try:
            # FineReport 页面
            if self._is_finereport_page():
                return self._fr_go_next_page()

            # Element UI 路径
            next_selectors = [
                'button:has-text("下一页")',
                ".el-pagination .btn-next",
                'text=下一页',
                "button.btn-next",
            ]

            for sel in next_selectors:
                try:
                    btn = self.ctx.locator(sel).first
                    if btn.is_visible() and btn.is_enabled():
                        btn.click()
                        time.sleep(self.page_interval)
                        try:
                            self.ctx.wait_for_load_state(
                                "networkidle", timeout=10000
                            )
                        except Exception:
                            pass
                        logger.debug("已翻到下一页")
                        return True
                except Exception:
                    continue

            logger.debug("无法翻到下一页")
            return False

        except Exception as e:
            logger.warning("翻页失败: %s", e)
            return False

    def go_to_page(self, page_num: int) -> bool:
        """
        跳转到指定页码

        Args:
            page_num: 目标页码

        Returns:
            是否成功跳转
        """
        try:
            # FineReport 页面
            if self._is_finereport_page():
                return self._fr_go_to_page(page_num)

            # Element UI 路径
            page_input_selectors = [
                ".el-pagination .el-input__inner",
                'input[type="number"]',
                ".el-pager .number",
            ]

            for sel in page_input_selectors:
                try:
                    inp = self.ctx.locator(sel).first
                    if inp.is_visible():
                        inp.click()
                        inp.fill(str(page_num))
                        inp.press("Enter")
                        time.sleep(self.page_interval)
                        try:
                            self.ctx.wait_for_load_state(
                                "networkidle", timeout=10000
                            )
                        except Exception:
                            pass
                        logger.debug("已跳转到第 %d 页", page_num)
                        return True
                except Exception:
                    continue

            logger.warning("无法跳转到第 %d 页", page_num)
            return False

        except Exception as e:
            logger.warning("页码跳转失败: %s", e)
            return False

    def scroll_to_load_all(self, container_selector: Optional[str] = None):
        """
        滚动加载所有数据（针对无分页但有滚动条的表格）

        Args:
            container_selector: 滚动容器选择器
        """
        logger.info("开始滚动加载数据...")

        scroll_target = container_selector or "table"
        previous_height = 0
        max_attempts = 50
        attempts = 0

        while attempts < max_attempts:
            try:
                # 获取当前滚动高度（在 iframe 内执行）
                current_height = self.ctx.evaluate(f"""
                    () => {{
                        const el = document.querySelector('{scroll_target}');
                        if (el) {{
                            el.parentElement.scrollTop = el.parentElement.scrollHeight;
                            return el.parentElement.scrollHeight;
                        }}
                        return document.documentElement.scrollHeight;
                    }}
                """)

                if current_height == previous_height:
                    break

                previous_height = current_height
                time.sleep(1)
                attempts += 1

            except Exception as e:
                logger.warning("滚动加载出错: %s", e)
                break

        logger.info("滚动加载完成 (尝试 %d 次)", attempts)

    # ── FineReport 分页支持 ──────────────────────────────────────

    def _fr_get_total_pages(self) -> int:
        """
        获取 FineReport 报表的总页数。

        FineReport 的分页信息通过 JS API 或页面 DOM 中的 "/N" 格式文本获取。

        Returns:
            总页数
        """
        # 方法1：通过 FineReport JS API
        try:
            result = self.ctx.evaluate("""() => {
                try {
                    var form = _g();
                    if (form && form.currentPage !== undefined && form.totalPage !== undefined) {
                        return form.totalPage;
                    }
                    return 0;
                } catch(e) {
                    return 0;
                }
            }""")
            if result and result > 0:
                logger.debug("FineReport JS API 获取总页数: %d", result)
                return int(result)
        except Exception as e:
            logger.debug("FineReport JS API 获取总页数失败: %s", e)

        # 方法2：查找页面中的 "/N" 格式文本（FineReport 工具栏通常显示 "1/5" 格式）
        try:
            page_info_selectors = [
                'text=/\\d+\\s*\\/\\s*\\d+/',
                ".x-page-toolbar",
                ".fr-toolbar",
            ]
            for sel in page_info_selectors:
                try:
                    el = self.ctx.locator(sel).first
                    if el.is_visible():
                        text = el.text_content().strip()
                        match = re.search(r"/\s*(\d+)", text)
                        if match:
                            total = int(match.group(1))
                            logger.debug("FineReport DOM 获取总页数: %d", total)
                            return total
                except Exception:
                    continue
        except Exception:
            pass

        logger.debug("FineReport 未找到总页数，默认为 1 页")
        return 1

    def _fr_has_next_page(self) -> bool:
        """检查 FineReport 报表是否有下一页"""
        # 方法1：通过 JS API
        try:
            result = self.ctx.evaluate("""() => {
                try {
                    var form = _g();
                    if (form && form.currentPage !== undefined && form.totalPage !== undefined) {
                        return form.currentPage < form.totalPage;
                    }
                    return false;
                } catch(e) {
                    return false;
                }
            }""")
            return bool(result)
        except Exception:
            pass

        # 方法2：查找"下一页"按钮
        fr_next_selectors = [
            'text=下一页',
            'a:has-text("下一页")',
            'span:has-text("下一页")',
            ".x-page-next",
            ".fr-page-next",
        ]
        for sel in fr_next_selectors:
            try:
                btn = self.ctx.locator(sel).first
                if btn.is_visible():
                    # 检查是否禁用
                    cls = btn.get_attribute("class") or ""
                    if "disabled" in cls or "gray" in cls:
                        return False
                    return True
            except Exception:
                continue

        return False

    def _fr_go_next_page(self) -> bool:
        """FineReport 报表翻到下一页"""
        # 方法1：通过 JS API
        try:
            success = self.ctx.evaluate("""() => {
                try {
                    var form = _g();
                    if (form && typeof form.gotoPrevPage !== 'undefined') {
                        // FineReport 使用 gotoPage(n) 方法
                        var current = form.currentPage || 1;
                        var total = form.totalPage || 1;
                        if (current < total) {
                            form.gotoPage(current + 1);
                            return true;
                        }
                    }
                    return false;
                } catch(e) {
                    return false;
                }
            }""")
            if success:
                time.sleep(self.page_interval)
                logger.debug("通过 FineReport JS API 翻到下一页")
                return True
        except Exception:
            pass

        # 方法2：点击"下一页"按钮
        fr_next_selectors = [
            'text=下一页',
            'a:has-text("下一页")',
            'span:has-text("下一页")',
            ".x-page-next",
            ".fr-page-next",
        ]
        for sel in fr_next_selectors:
            try:
                btn = self.ctx.locator(sel).first
                if btn.is_visible():
                    btn.click()
                    time.sleep(self.page_interval)
                    logger.debug("通过点击 FineReport 下一页按钮翻页")
                    return True
            except Exception:
                continue

        logger.debug("FineReport 无法翻到下一页")
        return False

    def _fr_go_to_page(self, page_num: int) -> bool:
        """FineReport 报表跳转到指定页码"""
        # 方法1：通过 JS API
        try:
            success = self.ctx.evaluate("""(pageNum) => {
                try {
                    var form = _g();
                    if (form && typeof form.gotoPage === 'function') {
                        form.gotoPage(pageNum);
                        return true;
                    }
                    return false;
                } catch(e) {
                    return false;
                }
            }""", page_num)
            if success:
                time.sleep(self.page_interval)
                logger.debug("通过 FineReport JS API 跳转到第 %d 页", page_num)
                return True
        except Exception:
            pass

        # 方法2：查找页码输入框
        try:
            # FineReport 的页码输入框通常在工具栏中
            page_input = self.ctx.locator(
                ".x-page-toolbar input, .fr-toolbar input, "
                "input[type='text']"
            ).all()
            for inp in page_input:
                try:
                    val = inp.input_value().strip()
                    # 页码输入框通常包含当前页码数字
                    if val and val.isdigit():
                        inp.click()
                        inp.fill(str(page_num))
                        inp.press("Enter")
                        time.sleep(self.page_interval)
                        logger.debug("通过 FineReport 页码输入框跳转到第 %d 页", page_num)
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        logger.warning("FineReport 无法跳转到第 %d 页", page_num)
        return False
