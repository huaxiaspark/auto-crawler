"""
筛选条件处理模块
处理日期选择、下拉框筛选、每页条数等操作

注意：页面内容（日期输入框、下拉框、查询按钮等）通常在 iframe 内部，
需要通过 self.ctx 指向正确的 Frame 上下文才能找到元素。
self.page 保留对主页面 Page 的引用，用于 keyboard 等仅 Page 支持的操作。
"""

import time
from typing import List, Optional, Union

from playwright.sync_api import Page, Frame, TimeoutError as PlaywrightTimeout

from utils.logger import get_logger

logger = get_logger()


class FilterHandler:
    """筛选条件处理器"""

    def __init__(self, page: Page, config: dict):
        self.page = page
        # ctx 指向实际操作 DOM 的上下文（Frame 或 Page）
        # 默认为 page，在检测到 iframe 后会被替换为 iframe 的 Frame
        self.ctx: Union[Page, Frame] = page
        self.config = config

    def _wait_for_filters_ready(self):
        """
        等待筛选区域渲染完成。

        将 Element UI、FineReport、通用选择器合并为单次 wait_for_selector 调用，
        任意一个选择器匹配即返回。避免串行等待导致的累计超时（原来 3 × 10s = 30s）。

        部分页面（如实时节点边际电价）的 iframe 内容加载较慢，
        合并后可在元素出现的第一时间检测到，减少不必要的等待。

        处理两种异常：
        - PlaywrightTimeout: 超时但 Frame 仍有效，可以继续尝试
        - 其他异常（如 Frame was detached）: Frame 失效，需要抛出让上层重试
        """
        combined_selector = ", ".join([
            # Element UI
            ".el-form-item", ".el-date-editor", ".el-select", ".el-input",
            # FineReport
            ".fr-trigger-editor", ".fr-form-imgboard",
            "input.fr-trigger-texteditor", ".para-container",
            # 通用
            "input", "select", "button",
        ])
        try:
            self.ctx.wait_for_selector(combined_selector, timeout=15000)
            logger.debug("筛选区域已就绪")
        except PlaywrightTimeout:
            logger.warning("筛选区域未在 15 秒内出现，继续尝试设置筛选条件...")
        except Exception as e:
            err_msg = str(e)
            if "detached" in err_msg.lower():
                logger.error("iframe 已 detached，需要重新检测: %s", err_msg)
                raise
            logger.warning("等待筛选区域时出现异常: %s", e)

    def _find_form_item(self, label: str):
        """根据标签文本查找对应的表单项容器"""
        # 策略1：el-form-item 容器
        try:
            form_item = self.ctx.locator(".el-form-item").filter(
                has_text=label
            ).first
            if form_item and form_item.is_visible():
                return form_item
        except Exception:
            pass

        # 策略2：通过 label 文本向上查找 el-form-item 祖先
        try:
            label_el = self.ctx.locator(f"text={label}").first
            if label_el.is_visible():
                return label_el.locator(
                    "xpath=ancestor::div[contains(@class,'el-form-item')][1]"
                )
        except Exception:
            pass

        # 策略3：label 的直接父级（有些页面不使用 el-form-item）
        try:
            label_el = self.ctx.locator(f"text={label}").first
            if label_el.is_visible():
                return label_el.locator("..")
        except Exception:
            pass

        return None

    @staticmethod
    def _pick_visible_input(container, selectors: List[str]):
        """在容器内按顺序选择第一个可见输入框"""
        for sel in selectors:
            try:
                candidate = container.locator(sel).first
                if candidate and candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    # ── FineReport 页面检测 ──────────────────────────────────────

    def _is_finereport_page(self) -> bool:
        """
        检测当前上下文是否为 FineReport 报表页面。

        FineReport 页面特征：
        - 包含 .fr-trigger-editor（日期/下拉控件）
        - 包含 .fr-form-imgboard（按钮控件）
        - 包含 .para-container（参数面板容器）
        - 包含 [widgetname] 属性的元素

        Returns:
            True 如果是 FineReport 页面
        """
        try:
            fr_count = self.ctx.locator(
                ".fr-trigger-editor, .fr-form-imgboard, .para-container"
            ).count()
            return fr_count > 0
        except Exception:
            return False

    # ── FineReport 下拉框（combo）处理 ───────────────────────────

    def _fr_get_dropdown_options(self, dropdown_label: str) -> List[str]:
        """
        获取 FineReport combo 控件的下拉选项。

        FineReport combo 控件使用 widgetname 属性标识，选项可能来自服务器（remote 模式）。
        优先通过 FineReport JavaScript API 获取，回退到 DOM 操作方式。

        Args:
            dropdown_label: 下拉框标签/widgetname（如 "节点名称"、"断面名称"）

        Returns:
            选项文本列表
        """
        options = []

        # 方法1：通过 FineReport JS API 获取选项
        try:
            result = self.ctx.evaluate("""(widgetName) => {
                try {
                    // FineReport 提供 _g() 函数访问报表对象
                    var form = _g();
                    if (!form || !form.parameterEl) return [];
                    var widget = form.parameterEl.getWidgetByName(widgetName);
                    if (!widget) return [];
                    // combo 控件提供 getItems() 方法
                    if (typeof widget.getItems === 'function') {
                        var items = widget.getItems();
                        return items.map(function(item) {
                            return item.text || item.value || '';
                        }).filter(function(t) { return t; });
                    }
                    return [];
                } catch(e) {
                    return [];
                }
            }""", dropdown_label)
            if result and len(result) > 0:
                options = [str(item) for item in result if item]
                logger.info("通过 FineReport JS API 获取到 %d 个下拉选项", len(options))
                return options
        except Exception as e:
            logger.debug("FineReport JS API 获取下拉选项失败: %s", e)

        # 方法2：通过 DOM 操作（点击打开下拉列表，读取选项）
        try:
            # 找到 combo 控件
            combo = self.ctx.locator(f'div.fr-trigger-editor[widgetname="{dropdown_label}"]').first
            if not combo or not combo.is_visible():
                logger.warning("未找到 FineReport combo 控件: %s", dropdown_label)
                return options

            # 点击触发按钮打开下拉列表
            trigger_btn = combo.locator(".fr-trigger-btn-up, .fr-trigger-btn").first
            if trigger_btn and trigger_btn.is_visible():
                trigger_btn.click()
            else:
                combo.locator("input").first.click()
            time.sleep(1)

            # 等待下拉列表出现并收集选项
            # FineReport combo 下拉列表的常见选择器
            fr_dropdown_selectors = [
                ".fr-combo-list-item",
                ".fr-trigger-list .fr-trigger-item",
                ".fr-list-item",
                ".x-combo-list-item",
            ]

            for sel in fr_dropdown_selectors:
                try:
                    items = self.ctx.locator(sel).all()
                    if items and len(items) > 0:
                        for item in items:
                            text = item.text_content().strip()
                            if text and text not in options:
                                options.append(text)
                        if options:
                            logger.info("通过 DOM 获取到 %d 个 FineReport 下拉选项", len(options))
                            break
                except Exception:
                    continue

            # 关闭下拉列表
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(0.3)

        except Exception as e:
            logger.debug("FineReport DOM 获取下拉选项失败: %s", e)

        return options

    def _fr_select_dropdown_option(self, dropdown_label: str, option_text: str):
        """
        在 FineReport combo 控件中选择指定选项。

        优先通过 FineReport JavaScript API 设置值，回退到 DOM 操作。

        Args:
            dropdown_label: 下拉框标签/widgetname
            option_text: 要选择的选项文本
        """
        # 方法1：通过 FineReport JS API 设置值
        try:
            success = self.ctx.evaluate("""([widgetName, value]) => {
                try {
                    var form = _g();
                    if (!form || !form.parameterEl) return false;
                    var widget = form.parameterEl.getWidgetByName(widgetName);
                    if (!widget) return false;
                    widget.setValue(value);
                    return true;
                } catch(e) {
                    return false;
                }
            }""", [dropdown_label, option_text])
            if success:
                logger.info("通过 FineReport JS API 设置下拉值: %s = %s",
                            dropdown_label, option_text)
                time.sleep(0.5)
                return
        except Exception as e:
            logger.debug("FineReport JS API 设置下拉值失败: %s", e)

        # 方法2：通过 DOM 操作（在输入框中直接输入文本）
        try:
            combo_input = self.ctx.locator(
                f'div.fr-trigger-editor[widgetname="{dropdown_label}"] '
                f'input.fr-trigger-texteditor'
            ).first
            if combo_input and combo_input.is_visible():
                combo_input.click()
                time.sleep(0.3)
                combo_input.fill(option_text)
                time.sleep(0.5)
                # 按 Enter 确认选择
                combo_input.press("Enter")
                time.sleep(0.3)
                logger.info("通过 DOM 输入设置下拉值: %s = %s",
                            dropdown_label, option_text)
                return
        except Exception as e:
            logger.debug("FineReport DOM 输入设置下拉值失败: %s", e)

        # 方法3：通过点击下拉列表中的选项
        try:
            # 打开下拉列表
            combo = self.ctx.locator(f'div.fr-trigger-editor[widgetname="{dropdown_label}"]').first
            trigger_btn = combo.locator(".fr-trigger-btn-up, .fr-trigger-btn").first
            if trigger_btn and trigger_btn.is_visible():
                trigger_btn.click()
            else:
                combo.locator("input").first.click()
            time.sleep(1)

            # 在下拉列表中查找并点击目标选项
            fr_item_selectors = [
                ".fr-combo-list-item",
                ".fr-trigger-list .fr-trigger-item",
                ".fr-list-item",
                ".x-combo-list-item",
            ]

            for sel in fr_item_selectors:
                try:
                    items = self.ctx.locator(sel).all()
                    for item in items:
                        text = item.text_content().strip()
                        if text == option_text:
                            item.click()
                            time.sleep(0.5)
                            logger.info("通过点击 FineReport 下拉列表选项: %s = %s",
                                        dropdown_label, option_text)
                            return
                except Exception:
                    continue

            # 关闭下拉列表
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

        except Exception as e:
            logger.debug("FineReport DOM 点击选项失败: %s", e)

        raise RuntimeError(f"FineReport 下拉选项设置失败: {dropdown_label} = {option_text}")

    def _fr_set_page_size(self, size: int):
        """
        设置 FineReport 报表的每页条数。

        通过 PAGESIZE combo 控件设置，优先使用 JS API。

        Args:
            size: 每页条数（10/20/30/40/50）
        """
        size_str = str(size)

        # 方法1：通过 FineReport JS API
        try:
            success = self.ctx.evaluate("""(value) => {
                try {
                    var form = _g();
                    if (!form || !form.parameterEl) return false;
                    var widget = form.parameterEl.getWidgetByName('PAGESIZE');
                    if (!widget) return false;
                    widget.setValue(value);
                    return true;
                } catch(e) {
                    return false;
                }
            }""", size_str)
            if success:
                logger.info("通过 FineReport JS API 设置每页条数: %d", size)
                return
        except Exception as e:
            logger.debug("FineReport JS API 设置每页条数失败: %s", e)

        # 方法2：通过 DOM 直接修改 PAGESIZE 输入框
        try:
            pagesize_input = self.ctx.locator(
                'div.fr-trigger-editor[widgetname="PAGESIZE"] input.fr-trigger-texteditor, '
                'div[widgetname="PAGESIZE"] input'
            ).first
            if pagesize_input and pagesize_input.is_visible():
                pagesize_input.click()
                time.sleep(0.3)
                pagesize_input.fill(size_str)
                time.sleep(0.3)
                pagesize_input.press("Enter")
                time.sleep(0.5)
                logger.info("通过 DOM 设置 FineReport 每页条数: %d", size)
                return
        except Exception as e:
            logger.debug("FineReport DOM 设置每页条数失败: %s", e)

        logger.warning("FineReport 每页条数设置失败")

    # 日期输入框的所有可能选择器（按优先级排列）
    _DATE_INPUT_SELECTORS = [
        # FineReport - widgetname 精确定位
        'div.fr-trigger-editor[widgetname="日期"] input.fr-trigger-texteditor',
        'div[widgetname="日期"] input.fr-trigger-texteditor',
        'div[widgetname="日期"] input',
        "input.fr-trigger-texteditor",
        # Element UI - 日期选择器
        ".el-date-editor input",
        ".el-date-editor .el-input__inner",
        # 通用属性匹配
        'input[placeholder*="日期"]',
        'input[placeholder*="date"]',
        'input[type="date"]',
    ]

    def set_date(self, date_str: str, quick_mode: bool = False):
        """
        设置日期输入框（格式：YYYY-MM-DD）

        适配多种页面类型：
        - Element UI DatePicker 页面（如：实时节点边际电价等）
        - FineReport 报表页面（如：抽蓄电站水位、日前备用总量等）
        - 普通文本输入框页面

        Args:
            date_str: 目标日期（YYYY-MM-DD）
            quick_mode: 快速模式（用于 export_all 等仅需设置日期后直接导出的场景）
                - 跳过 _wait_for_filters_ready（这些页面总是超时，浪费 15 秒）
                - 跳过日期面板关闭操作（Tab/Escape/点击空白处），
                  因为后续的导出按钮点击会自动关闭任何打开的面板
        """
        import time as time_module
        start_time = time_module.time()
        logger.info("设置日期: %s", date_str)
        try:
            # ── 第一阶段：等待页面就绪 ──
            if not quick_mode:
                # 常规模式：等待筛选区域加载完成
                wait_start = time_module.time()
                self._wait_for_filters_ready()
                logger.debug("等待筛选区域就绪耗时: %.2f秒", time_module.time() - wait_start)

            # ── 第二阶段：定位日期输入框 ──
            # 优化策略：先尝试快速定位，如果找不到再等待
            locate_start = time_module.time()
            date_input = self._pick_visible_input(self.ctx, self._DATE_INPUT_SELECTORS)
            
            if date_input is not None:
                logger.debug("快速定位日期输入框成功，耗时: %.2f秒", time_module.time() - locate_start)
            else:
                # 快速定位失败，使用 wait_for_selector 等待元素出现
                # 优化：减少超时时间从30秒到5秒（quick_mode）或10秒（常规模式）
                # 因为如果元素存在，应该很快出现；如果不存在，等待30秒也意义不大
                wait_timeout = 5000 if quick_mode else 10000
                combined_date_selector = ", ".join(self._DATE_INPUT_SELECTORS)
                try:
                    wait_start = time_module.time()
                    self.ctx.wait_for_selector(
                        combined_date_selector, timeout=wait_timeout, state="visible",
                    )
                    logger.debug("等待日期输入框出现耗时: %.2f秒", time_module.time() - wait_start)
                    # 等待成功后，再次尝试定位
                    date_input = self._pick_visible_input(self.ctx, self._DATE_INPUT_SELECTORS)
                except PlaywrightTimeout:
                    logger.debug("日期输入框未在 %d 秒内通过 wait_for_selector 检测到，"
                                 "继续使用回退策略...", wait_timeout / 1000)
                except Exception as e:
                    if "detached" in str(e).lower():
                        raise
                    logger.debug("等待日期输入框时异常: %s", e)

            if date_input is not None:
                logger.debug("通过预定义选择器找到日期输入框")

            # ── 回退策略（仅在快速定位失败时执行） ──

            # 回退1：通过"日期"标签定位其旁边的日期输入框（Element UI）
            if date_input is None:
                form_item = self._find_form_item("日期")
                if form_item is not None:
                    date_input = self._pick_visible_input(
                        form_item,
                        [
                            ".el-date-editor input",
                            ".el-date-editor .el-input__inner",
                            'input[placeholder*="日期"]',
                            'input[placeholder*="date"]',
                            ".el-input__inner",
                            "input",
                        ],
                    )

            # 回退2：通过标签文本查找附近的 input（适配非 Element UI 页面）
            if date_input is None:
                for label_text in ["日期", "运行日期", "查询日期", "选择日期", "日"]:
                    try:
                        label_el = self.ctx.locator(f"text={label_text}").first
                        if label_el and label_el.is_visible():
                            for level in range(1, 6):
                                ancestor = label_el
                                for _ in range(level):
                                    ancestor = ancestor.locator("..")
                                try:
                                    inp = ancestor.locator("input").first
                                    if inp and inp.is_visible():
                                        date_input = inp
                                        logger.debug(
                                            "通过标签「%s」+ 父级(level=%d)找到日期输入框",
                                            label_text, level,
                                        )
                                        break
                                except Exception:
                                    continue
                            if date_input is not None:
                                break
                    except Exception:
                        continue

            # 回退3：查找值包含日期格式的 input（当前页面已有日期值）
            if date_input is None:
                try:
                    inputs = self.ctx.locator("input").all()
                    for inp in inputs:
                        try:
                            if inp.is_visible():
                                val = inp.input_value().strip()
                                if val and len(val) == 10 and val[4] == "-" and val[7] == "-":
                                    date_input = inp
                                    logger.debug("通过已有日期值找到日期输入框: %s", val)
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass

            if date_input is None:
                try:
                    input_count = self.ctx.locator("input").count()
                    fr_count = self.ctx.locator(".fr-trigger-editor").count()
                    el_count = self.ctx.locator(".el-date-editor").count()
                    logger.error(
                        "未找到日期输入框 - 页面控件统计: "
                        "input=%d, fr-trigger-editor=%d, el-date-editor=%d",
                        input_count, fr_count, el_count,
                    )
                except Exception:
                    pass
                raise RuntimeError("未找到日期输入框")

            # ── 第三阶段：填写日期值 ──
            fill_start = time_module.time()
            
            # 优化：quick_mode下减少sleep时间，因为不需要等待UI响应
            click_sleep = 0.2 if quick_mode else 0.5
            fill_sleep = 0.1 if quick_mode else 0.3
            
            date_input.click()
            time.sleep(click_sleep)

            date_input.press("Control+a")
            time.sleep(0.05)  # 减少全选后的等待时间
            date_input.fill(date_str)
            time.sleep(fill_sleep)

            # 验证输入值，如果 fill 不生效则用 JS 直接赋值
            try:
                current = date_input.input_value().strip()
                if current != date_str:
                    logger.debug("fill 输入未生效 (%s)，使用 JS 赋值", current)
                    self.ctx.evaluate(
                        """([el, val]) => {
                            el.value = val;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }""",
                        [date_input.element_handle(), date_str],
                    )
            except Exception:
                pass
            
            logger.debug("填写日期值耗时: %.2f秒", time_module.time() - fill_start)

            # ── 第四阶段：关闭日期面板（quick_mode 下跳过） ──
            # quick_mode 场景（export_all）中，后续点击导出按钮时会自动关闭面板，
            # 无需额外的 Tab/Escape/点击空白操作，节省 ~3 秒 + 多次浏览器通信。
            if not quick_mode:
                close_start = time_module.time()
                # 按 Tab 确认输入（FineReport 日期控件响应 Tab/Enter 确认值）
                try:
                    date_input.press("Tab")
                except Exception:
                    pass
                time.sleep(0.2)  # 减少等待时间

                # 按 Escape 关闭日期面板
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                time.sleep(0.3)  # 减少等待时间

                # 点击页面空白处确保日期面板关闭
                try:
                    self.ctx.locator("body").click(position={"x": 0, "y": 0})
                except Exception:
                    pass
                time.sleep(0.2)  # 减少等待时间
                logger.debug("关闭日期面板耗时: %.2f秒", time_module.time() - close_start)

            total_time = time_module.time() - start_time
            logger.info("日期已设置为: %s (总耗时: %.2f秒)", date_str, total_time)

        except Exception as e:
            logger.error("设置日期失败 [%s]: %s", date_str, e)
            raise

    def _find_active_dropdown_panel(self):
        """
        找到当前打开的（可见的）el-select 下拉面板。

        Element UI 的下拉面板 .el-select-dropdown.el-popper 挂载在 body 上，
        页面上可能存在多个面板（如节点名称下拉、分页条数下拉等），
        但同一时刻只有一个是可见的（正在展开的那个）。

        Returns:
            可见的下拉面板 Locator，未找到返回 None
        """
        try:
            panels = self.ctx.locator(".el-select-dropdown.el-popper").all()
            for panel in panels:
                try:
                    if panel.is_visible():
                        return panel
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _open_dropdown_panel(self, dropdown_label: str):
        """
        打开指定标签的下拉框面板，并等待面板出现。

        Element UI 的 el-select 下拉面板是独立 DOM 节点，
        挂载在 <body> 上（不在 el-select 内部），需要通过
        可见性检查来定位当前打开的面板。

        Args:
            dropdown_label: 下拉框标签

        Returns:
            下拉输入框 Locator

        Raises:
            RuntimeError: 未找到下拉框
        """
        dropdown = self._find_dropdown(dropdown_label)
        if dropdown is None:
            raise RuntimeError(f"未找到下拉框: {dropdown_label}")

        # 先关闭可能已打开的面板
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

        # 确保所有面板都已关闭
        for _ in range(5):
            if self._find_active_dropdown_panel() is None:
                break
            time.sleep(0.3)

        # 点击打开下拉面板
        dropdown.click()
        time.sleep(0.8)

        # 等待目标下拉面板出现（通过检测可见面板）
        panel = None
        for attempt in range(3):
            # 检查是否有面板变为可见
            panel = self._find_active_dropdown_panel()
            if panel:
                # 确认面板中有选项
                try:
                    item_count = panel.locator(".el-select-dropdown__item").count()
                    if item_count > 0:
                        logger.debug("下拉面板已出现，包含 %d 个选项", item_count)
                        break
                except Exception:
                    pass

            if attempt < 2:
                # 重试：再次点击下拉框
                logger.debug("下拉面板未出现，重试点击... (第%d次)", attempt + 2)
                try:
                    # 先关闭可能打开的其他面板
                    self.page.keyboard.press("Escape")
                    time.sleep(0.3)
                except Exception:
                    pass
                dropdown.click()
                time.sleep(1.5)

        if panel is None or not panel.is_visible():
            logger.warning("下拉面板仍未出现，尝试使用 JavaScript 触发")
            # 最后手段：通过 JS 点击 el-select 容器触发展开
            try:
                self.ctx.evaluate("""(inputEl) => {
                    const selectEl = inputEl.closest('.el-select');
                    if (selectEl) {
                        const input = selectEl.querySelector('.el-input__inner');
                        if (input) {
                            input.click();
                            input.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            input.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        }
                    }
                }""", dropdown.element_handle())
                time.sleep(1.5)
                panel = self._find_active_dropdown_panel()
            except Exception as e:
                logger.debug("JS 触发下拉面板失败: %s", e)

        if panel is None:
            logger.warning("下拉面板最终未能打开")

        return dropdown

    def _close_dropdown_panel(self):
        """关闭当前打开的下拉面板"""
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.3)

    def _collect_visible_dropdown_items(self) -> List[str]:
        """
        从当前打开的下拉面板中收集所有选项文本。

        关键：页面上可能存在多个下拉面板（如节点名称、分页条数等），
        必须只从当前打开（可见）的面板中收集选项，避免混入其他面板的内容。

        Element UI 的 el-select 下拉面板可能包含可滚动列表，
        但所有选项 DOM 节点都存在（非虚拟滚动），可以一次性获取。

        Returns:
            选项文本列表
        """
        options = []
        try:
            # 关键修复：仅从当前打开的可见面板中收集选项
            panel = self._find_active_dropdown_panel()
            if panel is None:
                logger.warning("未找到可见的下拉面板，无法收集选项")
                return options

            # 优先取 span 内文本（更精确），回退到 li 自身文本
            items = panel.locator(".el-select-dropdown__item").all()
            for item in items:
                try:
                    # 优先读 span 子元素的文本
                    span = item.locator("span").first
                    text = ""
                    try:
                        text = span.text_content().strip()
                    except Exception:
                        text = item.text_content().strip()
                    if text and text != "全部" and text not in options:
                        options.append(text)
                except Exception:
                    continue
        except Exception as e:
            logger.debug("收集下拉选项失败: %s", e)
        return options

    def get_dropdown_options(self, dropdown_label: str) -> List[str]:
        """
        获取指定下拉框的所有选项

        自动检测页面类型（FineReport / Element UI），使用对应的策略。

        Args:
            dropdown_label: 下拉框标签（如：节点名称、断面名称、机组名称等）

        Returns:
            选项文本列表
        """
        logger.info("获取下拉选项: %s", dropdown_label)
        options = []

        try:
            self._wait_for_filters_ready()

            # 检测页面类型，优先使用 FineReport 路径
            if self._is_finereport_page():
                logger.info("检测到 FineReport 页面，使用 FineReport 方式获取下拉选项")
                options = self._fr_get_dropdown_options(dropdown_label)
            else:
                # Element UI 路径
                # 打开下拉面板
                self._open_dropdown_panel(dropdown_label)

                # 收集选项
                options = self._collect_visible_dropdown_items()

                # 关闭面板
                self._close_dropdown_panel()

            logger.info("下拉选项 [%s]: 共 %d 个", dropdown_label, len(options))
            if options:
                logger.info("  前5个: %s%s",
                            options[:5],
                            " ..." if len(options) > 5 else "")

        except Exception as e:
            logger.error("获取下拉选项失败 [%s]: %s", dropdown_label, e)
            try:
                self._close_dropdown_panel()
            except Exception:
                pass

        return options

    def select_dropdown_option(self, dropdown_label: str, option_text: str):
        """
        选择下拉框中的指定选项。

        自动检测页面类型（FineReport / Element UI），使用对应的策略。

        Args:
            dropdown_label: 下拉框标签
            option_text: 要选择的选项文本
        """
        logger.info("选择下拉选项: %s = %s", dropdown_label, option_text)
        try:
            self._wait_for_filters_ready()

            # 检测页面类型
            if self._is_finereport_page():
                logger.info("检测到 FineReport 页面，使用 FineReport 方式选择下拉选项")
                self._fr_select_dropdown_option(dropdown_label, option_text)
                logger.info("已选择: %s = %s", dropdown_label, option_text)
                return

            # Element UI 路径
            self._el_select_dropdown_option(dropdown_label, option_text)

        except Exception as e:
            logger.error("选择下拉选项失败 [%s=%s]: %s", dropdown_label, option_text, e)
            try:
                self._close_dropdown_panel()
            except Exception:
                pass
            raise

    def _el_select_dropdown_option(self, dropdown_label: str, option_text: str):
        """
        Element UI 页面的下拉选项选择逻辑。

        流程：
        1. 打开下拉面板
        2. 在当前可见面板中找到目标选项
        3. 滚动到可见区域并点击
        4. 等待面板关闭

        关键：必须只在当前打开的面板中查找，避免误点击其他面板的选项。

        Args:
            dropdown_label: 下拉框标签
            option_text: 要选择的选项文本
        """
        # 打开下拉面板
        self._open_dropdown_panel(dropdown_label)
        time.sleep(0.3)

        # 关键：找到当前打开的面板，仅在其中查找选项
        panel = self._find_active_dropdown_panel()
        if panel is None:
            self._close_dropdown_panel()
            raise RuntimeError(
                f"下拉面板未打开，无法选择选项: {option_text}"
            )

        # 在面板中查找目标选项并点击
        option_found = False

        # 策略1：精确匹配 el-select-dropdown__item（限定在当前面板内）
        try:
            items = panel.locator(".el-select-dropdown__item").all()
            for item in items:
                try:
                    text = item.text_content().strip()
                    if text == option_text:
                        # 滚动到可见区域
                        item.scroll_into_view_if_needed()
                        time.sleep(0.2)
                        item.click()
                        option_found = True
                        logger.debug("通过精确匹配点击选项: %s", option_text)
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.debug("策略1查找选项失败: %s", e)

        # 策略2：通过 span 子元素的文本精确匹配（限定在当前面板内）
        if not option_found:
            try:
                items = panel.locator(
                    ".el-select-dropdown__item span"
                ).all()
                for item in items:
                    try:
                        text = item.text_content().strip()
                        if text == option_text:
                            parent = item.locator("..")
                            parent.scroll_into_view_if_needed()
                            time.sleep(0.2)
                            parent.click()
                            option_found = True
                            logger.debug("通过span子元素点击选项: %s", option_text)
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.debug("策略2查找选项失败: %s", e)

        # 策略3：使用 has-text 精确文本匹配（限定在当前面板内）
        if not option_found:
            try:
                target = panel.locator(
                    f'.el-select-dropdown__item:has-text("{option_text}")'
                ).first
                if target.is_visible():
                    target.scroll_into_view_if_needed()
                    time.sleep(0.2)
                    target.click()
                    option_found = True
                    logger.debug("通过has-text点击选项: %s", option_text)
            except Exception as e:
                logger.debug("策略3查找选项失败: %s", e)

        if not option_found:
            self._close_dropdown_panel()
            raise RuntimeError(f"未在下拉选项中找到: {option_text}")

        # 等待下拉面板自动关闭（el-select 选中后自动收起）
        time.sleep(0.5)

        logger.info("已选择: %s = %s", dropdown_label, option_text)

    # ── 下拉框清空（「不选」优化） ────────────────────────────────

    def clear_dropdown_input(self, dropdown_label: str):
        """
        清空下拉框的输入值/选中值。

        用于「不选」优化场景：当下拉选项中包含「不选」时，
        直接清空输入框，使查询不带筛选条件，返回全部数据。

        相比先选择「不选」再清空，直接清空更可靠：
        - 避免 widget 内部状态残留「不选」导致查询仍然带筛选
        - 空值在 FineReport / Element UI 中统一表示"无筛选"

        自动检测页面类型（FineReport / Element UI），使用对应的策略。

        Args:
            dropdown_label: 下拉框标签（如：断面名称、通道名称等）
        """
        logger.info("清空下拉框输入值（不选模式）: %s", dropdown_label)
        try:
            self._wait_for_filters_ready()

            if self._is_finereport_page():
                logger.info("检测到 FineReport 页面，使用 FineReport 方式清空下拉框")
                self._fr_clear_dropdown_input(dropdown_label)
            else:
                self._el_clear_dropdown_input(dropdown_label)
        except Exception as e:
            logger.warning("清空下拉框输入值失败 [%s]: %s", dropdown_label, e)

    def quick_clear_fr_dropdown(self, dropdown_label: str):
        """
        快速清空 FineReport 下拉框值（轻量版）。

        直接通过 FineReport JS API 设置空值，跳过 _wait_for_filters_ready
        和 _is_finereport_page 等检测步骤。

        适用场景：已确认为 FineReport 页面，且需要在每次日期切换后重新确保
        下拉框为空（因为 set_date 可能触发 FineReport 参数联动刷新下拉框）。

        如果快速清空失败，会回退到完整的 clear_dropdown_input 方法。

        Args:
            dropdown_label: 下拉框的 widgetname
        """
        try:
            success = self.ctx.evaluate("""(widgetName) => {
                try {
                    var form = _g();
                    if (!form || !form.parameterEl) return false;
                    var widget = form.parameterEl.getWidgetByName(widgetName);
                    if (!widget) return false;
                    widget.setValue('');
                    return true;
                } catch(e) {
                    return false;
                }
            }""", dropdown_label)
            if success:
                logger.debug("快速清空 FineReport 下拉框: %s", dropdown_label)
                return
        except Exception as e:
            logger.debug("快速清空失败: %s", e)

        # 快速清空失败，回退到完整清空流程
        logger.info("快速清空失败，回退到完整清空方法: %s", dropdown_label)
        self.clear_dropdown_input(dropdown_label)

    def _fr_clear_dropdown_input(self, dropdown_label: str):
        """
        清空 FineReport combo 控件的值。

        优先通过 FineReport JS API 设置空值 (widget.setValue(''))，
        回退到 DOM 操作直接清空输入框。

        Args:
            dropdown_label: 下拉框的 widgetname
        """
        # 方法1：FineReport JS API 设置空值
        try:
            success = self.ctx.evaluate("""(widgetName) => {
                try {
                    var form = _g();
                    if (!form || !form.parameterEl) return false;
                    var widget = form.parameterEl.getWidgetByName(widgetName);
                    if (!widget) return false;
                    widget.setValue('');
                    return true;
                } catch(e) {
                    return false;
                }
            }""", dropdown_label)
            if success:
                logger.info("已通过 FineReport JS API 清空下拉框: %s", dropdown_label)
                time.sleep(0.3)
                return
        except Exception as e:
            logger.debug("FineReport JS API 清空下拉值失败: %s", e)

        # 方法2：DOM 直接清空输入框
        try:
            combo_input = self.ctx.locator(
                f'div.fr-trigger-editor[widgetname="{dropdown_label}"] '
                f'input.fr-trigger-texteditor'
            ).first
            if combo_input and combo_input.is_visible():
                combo_input.click()
                time.sleep(0.2)
                combo_input.fill("")
                time.sleep(0.2)
                combo_input.press("Enter")
                time.sleep(0.3)
                logger.info("已通过 DOM 清空 FineReport 下拉框: %s", dropdown_label)
                return
        except Exception as e:
            logger.debug("FineReport DOM 清空下拉值失败: %s", e)

        logger.warning("FineReport 下拉框清空失败: %s", dropdown_label)

    def _el_clear_dropdown_input(self, dropdown_label: str):
        """
        清空 Element UI el-select 的选中值。

        策略：
        1. 通过 JS 清空 el-select 组件的值并触发事件
        2. 回退到 hover 触发清空按钮（x 图标）并点击

        Args:
            dropdown_label: 下拉框标签
        """
        dropdown = self._find_dropdown(dropdown_label)
        if dropdown is None:
            logger.warning("未找到下拉框，无法清空: %s", dropdown_label)
            return

        # 策略1：通过 JS 清空 input 值并触发事件
        try:
            self.ctx.evaluate("""(inputEl) => {
                inputEl.value = '';
                inputEl.dispatchEvent(new Event('input', { bubbles: true }));
                inputEl.dispatchEvent(new Event('change', { bubbles: true }));
                // 尝试清空 Vue 组件内部状态
                var selectEl = inputEl.closest('.el-select');
                if (selectEl && selectEl.__vue__) {
                    selectEl.__vue__.value = '';
                    selectEl.__vue__.$emit('input', '');
                    selectEl.__vue__.$emit('change', '');
                }
            }""", dropdown.element_handle())
            time.sleep(0.3)
            logger.info("已通过 JS 清空 Element UI 下拉框: %s", dropdown_label)
            return
        except Exception as e:
            logger.debug("Element UI JS 清空失败: %s", e)

        # 策略2：hover 触发清空按钮
        try:
            select_container = dropdown.locator(
                "xpath=ancestor::div[contains(@class,'el-select')][1]"
            )
            select_container.hover()
            time.sleep(0.3)
            clear_btn = select_container.locator(
                ".el-input__clear, .el-icon-circle-close"
            ).first
            if clear_btn and clear_btn.is_visible():
                clear_btn.click()
                time.sleep(0.3)
                logger.info("已通过清空按钮清空 Element UI 下拉框: %s", dropdown_label)
                return
        except Exception as e:
            logger.debug("Element UI 清空按钮点击失败: %s", e)

        logger.warning("Element UI 下拉框清空失败: %s", dropdown_label)

    def set_page_size(self, size: int = 50):
        """
        设置每页显示条数

        自动检测页面类型（FineReport / Element UI），使用对应的策略。

        Args:
            size: 每页条数（10/20/30/40/50）
        """
        logger.info("设置每页条数: %d", size)
        try:
            # 优先检查是否为 FineReport 页面
            if self._is_finereport_page():
                logger.info("检测到 FineReport 页面，使用 FineReport 方式设置每页条数")
                self._fr_set_page_size(size)
                return

            # Element UI 路径
            page_size_selectors = [
                "text=每页条数",
                "text=每页展示",
                ".el-pagination .el-select",
            ]

            for sel in page_size_selectors:
                try:
                    element = self.ctx.locator(sel).first
                    if element.is_visible():
                        # 找到后点击旁边的下拉框
                        dropdown = element.locator(".. >> select, .. >> .el-input__inner").first
                        dropdown.click()
                        time.sleep(0.5)

                        # 选择目标条数
                        self.ctx.locator(f"text={size}").first.click()
                        time.sleep(1)
                        logger.info("已设置每页 %d 条", size)
                        return
                except Exception:
                    continue

            # 回退方案：直接查找带条数的 select
            selects = self.ctx.locator("select").all()
            for sel in selects:
                opts = sel.locator("option").all()
                for opt in opts:
                    if str(size) in opt.text_content():
                        sel.select_option(str(size))
                        time.sleep(1)
                        logger.info("已设置每页 %d 条", size)
                        return

            logger.warning("未找到每页条数选择器")

        except Exception as e:
            logger.error("设置每页条数失败: %s", e)

    def click_query_button(self):
        """
        点击「查询」按钮

        适配多种页面类型：
        - Element UI 页面：<button> 元素
        - FineReport 报表：<div widgetname="SEARCH"> 元素，内含 <span>查询</span>
        """
        logger.info("点击「查询」按钮")
        try:
            query_selectors = [
                # FineReport 查询按钮
                # widgetname 属性以 "SEARCH" 开头（实际有 SEARCH, SEARCH_C, SEARCH_C_C 等变体）
                'div[widgetname^="SEARCH"]',
                'div.fr-form-imgboard:has-text("查询")',
                'div.fr-form-imgboard:has-text("查 询")',
                # Element UI 查询按钮（部分页面按钮文字含空格，如"查 询"）
                'button:has-text("查询")',
                'button:has-text("查 询")',
                # 通用匹配（含空格变体）
                "text=查询",
                "text=查 询",
                ".query-btn",
                'button[type="primary"]',
            ]

            for sel in query_selectors:
                try:
                    btn = self.ctx.locator(sel).first
                    if btn.is_visible():
                        btn.click()
                        time.sleep(2)
                        try:
                            self.ctx.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass
                        logger.info("查询已执行（选择器: %s）", sel)
                        return
                except Exception:
                    continue

            logger.warning("未找到查询按钮")

        except Exception as e:
            logger.error("点击查询按钮失败: %s", e)
            raise

    def _find_dropdown(self, label: str):
        """
        根据标签文本查找对应的下拉框输入元素。

        Element UI el-select 的典型 DOM 结构：
          <div class="el-form-item">
            <label>节点名称</label>
            <div class="el-form-item__content">
              <div class="el-select">
                <div class="el-input">
                  <input class="el-input__inner" placeholder="请选择">
                  <span class="el-input__suffix">
                    <i class="el-select__caret el-input__icon el-icon-arrow-up"></i>
                  </span>
                </div>
              </div>
            </div>
          </div>

        点击 .el-input__inner 或 .el-select 容器都可以打开下拉面板。

        Args:
            label: 标签文本

        Returns:
            下拉框输入元素（Locator）或 None
        """
        # 策略1：通过表单项容器查找
        try:
            form_item = self._find_form_item(label)
            if form_item is not None:
                # 优先找 el-select 的 input
                dropdown = self._pick_visible_input(
                    form_item,
                    [
                        ".el-select .el-input__inner",
                        ".el-select input",
                        "input[role='combobox']",
                        "select",
                        ".el-input__inner",
                    ],
                )
                if dropdown is not None:
                    logger.debug("通过表单项容器找到下拉框: %s", label)
                    return dropdown
        except Exception:
            pass

        # 策略2：通过标签文本 + 紧邻的 el-select 查找
        try:
            label_el = self.ctx.locator(f"text={label}").first
            if label_el.is_visible():
                # 向上找父级容器，在其中寻找 el-select
                for level in range(1, 5):
                    ancestor = label_el
                    for _ in range(level):
                        ancestor = ancestor.locator("..")
                    try:
                        select_input = ancestor.locator(
                            ".el-select .el-input__inner"
                        ).first
                        if select_input.is_visible():
                            logger.debug(
                                "通过标签祖先(level=%d)找到下拉框: %s",
                                level, label,
                            )
                            return select_input
                    except Exception:
                        continue
        except Exception:
            pass

        # 策略3：直接查找 placeholder / aria-label 匹配的输入框
        try:
            selectors = [
                f'[aria-label*="{label}"]',
                f'[placeholder*="{label}"]',
                f'select[name*="{label}"]',
            ]
            for sel in selectors:
                el = self.ctx.locator(sel).first
                if el.is_visible():
                    logger.debug("通过属性选择器找到下拉框: %s", label)
                    return el
        except Exception:
            pass

        # 策略4：通过标签文本找到相邻的 input
        try:
            label_el = self.ctx.locator(f"text={label}").first
            if label_el.is_visible():
                parent = label_el.locator("..")
                dropdown = parent.locator(
                    "select, .el-select .el-input__inner, .el-input__inner"
                ).first
                if dropdown.is_visible():
                    logger.debug("通过标签直接父级找到下拉框: %s", label)
                    return dropdown
        except Exception:
            pass

        logger.warning("所有策略均未找到下拉框: %s", label)
        return None
