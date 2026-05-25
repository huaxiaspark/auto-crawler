"""
页面导航模块
处理左侧菜单、子菜单的导航和页面切换

侧边栏结构说明：
    - 侧边栏使用 Element UI 的 el-tree（树形控件），NOT el-menu
    - 容器层次：#leftMenuTwo > #guide-menu > .el-tree.filter-tree
    - 每个菜单项是一个 treeitem: <div role="treeitem" class="el-tree-node">
    - 文本在 <span title="菜单名">菜单名</span> 中，用 title 属性精确标识
    - 点击 .el-tree-node__content 展开/折叠节点
    - 展开状态：aria-expanded="true" + is-expanded class
    - 叶子节点的 expand-icon 有 is-leaf class

导航路径：
    1. 常规路径（3层树形菜单）：
       信息披露 > 分类(如现货实时数据) > 页面(如实时节点边际电价)
    2. 综合查询路径（侧边栏叶子 + 页面内导航）：
       信息披露 > 综合查询(叶子) > [页面内: 顶部导航栏 > 左侧面板 > 目标]
       例如：信息披露 > 综合查询 > 供需与约束 > 参数信息 > 节点分配因子
       注意：「综合查询」在侧边栏中是叶子节点（点击打开独立Web页面），
       不是可展开的分支。打开后的页面有自己的顶部导航栏和左侧面板。

关键注意事项：
    - 树由 Vue 动态渲染，首次加载需等待菜单出现
    - 点击可展开节点会 toggle，需检查 aria-expanded 避免误操作
    - 菜单展开有动画延迟，需等待子项可见后再继续
    - 侧边栏可能需要滚动才能看到目标菜单项
    - 综合查询页面内容可能渲染在 iframe 中，需在主页面和 iframe 中搜索元素
"""

import os
import time
from typing import Optional

from playwright.sync_api import Page, Locator, TimeoutError as PlaywrightTimeout

from utils.logger import get_logger
from utils.timing import sleep as ui_sleep

logger = get_logger()

# el-tree 侧边栏选择器
TREE_SELECTOR = "#guide-menu .el-tree"


class Navigator:
    """页面导航器 - 处理左侧栏树形菜单和内容区 tab 切换"""

    def __init__(self, page: Page, config: dict):
        self.page = page
        self.config = config
        self.query_interval = config.get("request", {}).get("query_interval", 3)
        # 状态追踪：避免重复点击已展开的折叠菜单（再次点击会收起）
        self._info_disclosure_expanded = False
        self._current_category: Optional[str] = None
        # 诊断截图保存目录
        self._debug_dir = os.path.abspath(
            config.get("logging", {}).get("log_dir", "./logs")
        )
        os.makedirs(self._debug_dir, exist_ok=True)

    # ── 诊断工具 ──────────────────────────────────────────────

    def _save_debug_screenshot(self, name: str):
        """保存诊断截图（用于调试页面状态）"""
        try:
            path = os.path.join(self._debug_dir, f"debug_{name}.png")
            self.page.screenshot(path=path)
            logger.info("诊断截图已保存: %s", path)
        except Exception as e:
            logger.warning("保存诊断截图失败: %s", e)

    def _log_page_info(self):
        """记录当前页面信息（用于诊断）"""
        try:
            url = self.page.url
            title = self.page.title()
            logger.info("当前页面 URL: %s, 标题: %s", url, title)
        except Exception:
            pass

    # ── el-tree 操作 ──────────────────────────────────────────

    def _get_tree(self) -> Locator:
        """获取侧边栏的 el-tree 控件"""
        return self.page.locator(TREE_SELECTOR).first

    def _find_tree_node_content(self, text: str, timeout: int = 15000) -> Locator:
        """
        在 el-tree 中查找指定文本的节点内容区域

        使用 span[title="..."] 属性选择器精确匹配菜单项文本，
        然后定位到其所在的 .el-tree-node__content 容器。
        这样点击会触发 el-tree 的 node-click 和展开行为。

        Args:
            text: 菜单项文本（与 span 的 title 属性一致）
            timeout: 等待超时（毫秒）

        Returns:
            .el-tree-node__content 的 Locator
        """
        tree = self._get_tree()
        # 使用 :has() 找到包含目标 span 的 .el-tree-node__content
        node_content = tree.locator(
            f'.el-tree-node__content:has(span[title="{text}"])'
        ).first
        node_content.wait_for(state="visible", timeout=timeout)
        return node_content

    def _is_tree_node_expanded(self, text: str) -> bool:
        """
        检查树节点是否已展开

        通过检查包含目标文本的 treeitem 元素的 aria-expanded 属性判断。

        Args:
            text: 菜单项文本

        Returns:
            True 如果节点已展开
        """
        tree = self._get_tree()
        try:
            # 找到直接包含目标文本的 treeitem 元素
            # :has(> .el-tree-node__content ...) 确保只匹配直接父级 treeitem
            treeitem = tree.locator(
                f'div[role="treeitem"]:has(> .el-tree-node__content span[title="{text}"])'
            ).first
            aria = treeitem.get_attribute("aria-expanded", timeout=3000)
            return aria == "true"
        except Exception:
            return False

    def _expand_tree_node(self, text: str, timeout: int = 15000):
        """
        展开树节点（如果未展开）

        el-tree 的展开逻辑：
        1. 先检查 aria-expanded 判断当前状态
        2. 如果已展开，跳过（避免 toggle 收起）
        3. 如果未展开，点击 .el-tree-node__content 触发展开
        4. 如果点击 content 仍未展开，则直接点击 expand-icon

        Args:
            text: 菜单项文本
            timeout: 等待超时（毫秒）
        """
        if self._is_tree_node_expanded(text):
            logger.debug("树节点「%s」已展开，跳过", text)
            return

        logger.info("正在展开树节点「%s」...", text)

        # 第一次尝试：点击 .el-tree-node__content
        node_content = self._find_tree_node_content(text, timeout=timeout)
        node_content.scroll_into_view_if_needed()
        ui_sleep("short")
        node_content.click()
        ui_sleep("long")  # 等待展开动画

        if self._is_tree_node_expanded(text):
            logger.info("已展开「%s」", text)
            return

        # 第二次尝试：直接点击 expand-icon
        logger.debug("点击 content 未展开「%s」，尝试点击展开图标", text)
        try:
            node_content = self._find_tree_node_content(text, timeout=5000)
            expand_icon = node_content.locator(".el-tree-node__expand-icon").first
            expand_icon.click()
            ui_sleep("long")
        except PlaywrightTimeout:
            pass

        if self._is_tree_node_expanded(text):
            logger.info("已展开「%s」（通过展开图标）", text)
            return

        # 仍未展开：保存截图并报错
        self._save_debug_screenshot(f"expand_failed_{text}")
        raise PlaywrightTimeout(f"无法展开树节点「{text}」")

    def _click_tree_leaf(self, text: str, timeout: int = 15000):
        """
        点击叶子节点（触发页面导航）

        叶子节点不需要展开，直接点击即可跳转到对应页面。

        Args:
            text: 菜单项文本
            timeout: 等待超时（毫秒）
        """
        logger.info("正在点击菜单项「%s」...", text)
        node_content = self._find_tree_node_content(text, timeout=timeout)
        node_content.scroll_into_view_if_needed()
        ui_sleep("short")
        node_content.click()

    # ── 等待与就绪检查 ────────────────────────────────────────

    def wait_for_sidebar_ready(self, timeout: int = 30000):
        """
        等待侧边栏 el-tree 菜单渲染完成

        通过等待 #guide-menu 容器内的 el-tree 出现，
        以及已知的顶层菜单项（如「信息披露」）出现来判断就绪。

        Args:
            timeout: 超时时间（毫秒）
        """
        logger.info("等待侧边栏菜单加载...")

        # 先等待页面网络空闲（API 数据加载完成）
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            logger.warning("等待 networkidle 超时，继续")

        # 等待 el-tree 容器出现
        try:
            self._get_tree().wait_for(state="attached", timeout=10000)
            logger.debug("el-tree 容器已加载")
        except PlaywrightTimeout:
            logger.warning("未找到 el-tree 容器")
            self._save_debug_screenshot("no_el_tree")
            self._log_page_info()
            return

        # 等待「信息披露」菜单项出现（树数据已加载的标志）
        try:
            tree = self._get_tree()
            tree.locator('span[title="信息披露"]').first.wait_for(
                state="visible", timeout=timeout
            )
            logger.info("侧边栏菜单已就绪")
        except PlaywrightTimeout:
            logger.warning("等待侧边栏菜单超时 (%dms)", timeout)
            self._save_debug_screenshot("sidebar_not_ready")
            self._log_page_info()

    # ── 导航流程 ──────────────────────────────────────────────

    def navigate_to_info_disclosure(self):
        """
        展开左侧栏「信息披露」树节点

        信息披露是所有数据页面的顶层入口，必须先展开才能看到子分类。
        使用 aria-expanded 属性检查避免重复点击导致折叠。
        """
        if self._info_disclosure_expanded and self._is_tree_node_expanded("信息披露"):
            logger.debug("「信息披露」已展开，跳过")
            return

        try:
            self._expand_tree_node("信息披露", timeout=15000)
            self._info_disclosure_expanded = True
        except PlaywrightTimeout:
            logger.error("无法展开「信息披露」菜单")
            self._save_debug_screenshot("info_disclosure_failed")
            self._log_page_info()
            raise

    def navigate_to_category(self, category: str):
        """
        展开二级分类树节点
        （如：现货出清结果、现货实时数据、现货日前信息、综合查询）

        这些分类是「信息披露」的子节点，需先展开「信息披露」。

        Args:
            category: 分类名称
        """
        if (self._current_category == category
                and self._is_tree_node_expanded(category)):
            logger.debug("分类「%s」已展开，跳过", category)
            return

        logger.info("正在展开分类「%s」...", category)
        try:
            self._expand_tree_node(category, timeout=15000)
            self._current_category = category
        except PlaywrightTimeout:
            logger.error("未找到或无法展开分类「%s」", category)
            self._save_debug_screenshot(f"category_{category}_failed")
            raise

    def navigate_to_subcategory(self, subcategory: str):
        """
        点击三级叶子菜单（如：实时节点边际电价、日前备用总量等）

        叶子节点点击后触发页面导航，需等待页面加载完成。
        若找不到子菜单，大概率是页面被自动刷新回首页，
        此时重置导航状态并重新从「信息披露」展开后重试一次。

        Args:
            subcategory: 子分类名称
        """
        try:
            self._click_tree_leaf(subcategory, timeout=15000)
        except PlaywrightTimeout:
            logger.error("未找到子菜单「%s」或页面加载超时", subcategory)
            self._save_debug_screenshot(f"subcategory_{subcategory}_failed")

            # 页面极大可能已被自动刷新回首页，重置状态后重试
            logger.warning("疑似页面已刷新回首页，重置导航状态后重试「%s」...", subcategory)
            self._info_disclosure_expanded = False
            self._current_category = None
            try:
                self.wait_for_sidebar_ready()
                self.navigate_to_info_disclosure()
            except Exception as e:
                logger.error("重置导航状态失败: %s", e)
                raise PlaywrightTimeout(f"未找到子菜单「{subcategory}」且重置导航失败")
            raise
        ui_sleep("xlong")
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            logger.warning("「%s」页面 networkidle 超时，继续执行", subcategory)
        logger.info("已导航到「%s」页面", subcategory)

    def navigate_to_page(self, category: str, page_name: str,
                          subcategory_path: Optional[str] = None):
        """
        完整导航到目标页面

        常规路径（3层树形菜单）：
            信息披露 > 分类 > 页面
            例如：信息披露 > 现货实时数据 > 实时节点边际电价

        综合查询路径（侧边栏叶子 + 页面内导航）：
            信息披露 > 综合查询(叶子) > [页面内: 顶部导航 > 左侧面板 > 目标]
            例如：信息披露 > 综合查询 > 供需与约束 > 参数信息 > 节点分配因子

        Args:
            category: 二级分类（现货出清结果/现货实时数据/现货日前信息/综合查询）
            page_name: 页面名称
            subcategory_path: 页面内子分类路径（如 "供需与约束 > 参数信息"），
                              仅综合查询等特殊页面使用
        """
        logger.info("=" * 60)
        logger.info("导航到: 信息披露 > %s > %s", category, page_name)

        # 第一步：展开「信息披露」
        self.navigate_to_info_disclosure()

        if subcategory_path:
            # ── 综合查询等特殊页面 ──
            # 「综合查询」在侧边栏中是叶子节点（点击后打开独立 Web 页面），
            # 而非可展开的分支。打开后的页面有自己独立的导航结构：
            #   - 顶部导航栏（水平标签页）
            #   - 左侧面板（手风琴菜单）
            logger.info("点击侧边栏「%s」打开页面...", category)
            self._click_tree_leaf(category, timeout=15000)
            ui_sleep("xlong")
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                logger.debug("等待 networkidle 超时，继续")

            # 在打开的综合查询页面内完成后续导航
            self._navigate_comprehensive_query(page_name, subcategory_path, category)
        else:
            # ── 常规路径：展开二级分类 → 点击目标页面 ──
            self.navigate_to_category(category)
            self.navigate_to_subcategory(page_name)

        time.sleep(self.query_interval)

    def _find_clickable_text(self, text: str, timeout: int = 10000):
        """
        在主页面或 iframe 中查找包含指定文本的可见元素。

        综合查询页面的内容通常渲染在 iframe 中，因此需要同时在
        主页面和所有 iframe 中搜索目标元素。
        优先使用精确匹配，找不到时回退到包含匹配。

        Args:
            text: 要查找的文本
            timeout: 总超时时间（毫秒）

        Returns:
            匹配的 Locator
        """
        per_try_timeout = min(timeout // 4, 3000)

        # 策略1：精确匹配 - 主页面
        try:
            loc = self.page.get_by_text(text, exact=True).first
            loc.wait_for(state="visible", timeout=per_try_timeout)
            logger.debug("在主页面中找到「%s」（精确匹配）", text)
            return loc
        except Exception:
            pass

        # 策略2：精确匹配 - 各 iframe
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                loc = frame.get_by_text(text, exact=True).first
                loc.wait_for(state="visible", timeout=per_try_timeout)
                logger.debug("在 iframe 中找到「%s」（精确匹配, URL: %s）",
                             text, frame.url[:50] if frame.url else "N/A")
                return loc
            except Exception:
                continue

        # 策略3：包含匹配 - 主页面（处理文本含空格/图标等情况）
        try:
            loc = self.page.get_by_text(text).first
            loc.wait_for(state="visible", timeout=per_try_timeout)
            logger.debug("在主页面中找到「%s」（包含匹配）", text)
            return loc
        except Exception:
            pass

        # 策略4：包含匹配 - 各 iframe
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                loc = frame.get_by_text(text).first
                loc.wait_for(state="visible", timeout=per_try_timeout)
                logger.debug("在 iframe 中找到「%s」（包含匹配）", text)
                return loc
            except Exception:
                continue

        raise PlaywrightTimeout(
            f"在主页面和所有 iframe 中均未找到包含「{text}」的可见元素"
        )

    def _navigate_comprehensive_query(self, page_name: str, subcategory_path: str,
                                       category: Optional[str] = None):
        """
        综合查询页面内导航。

        综合查询在侧边栏中是叶子节点，点击后打开一个独立的 Web 页面。
        该页面有自己的导航结构（与侧边栏的 el-tree 无关）：
        - 顶部导航栏：水平可滚动标签页（供需与约束、电网运行、市场运营 等）
        - 左侧面板：手风琴/可展开菜单（约束信息、参数信息 等）
          - 叶子项：点击后加载数据页面

        subcategory_path 格式："顶部标签 > 左侧分类"
        例如："供需与约束 > 参数信息"

        完整流程：
        1. 在顶部导航栏点击「供需与约束」标签
        2. 在左侧面板展开「参数信息」分类
        3. 点击目标叶子项「节点分配因子」

        Args:
            page_name: 最终目标页面名称（如 "节点分配因子"）
            subcategory_path: 页面内导航路径（如 "供需与约束 > 参数信息"）
            category: 侧边栏叶子节点名称（如 "综合查询"），用于顶部导航失败时重新打开页面
        """
        parts = [p.strip() for p in subcategory_path.split(">")]
        top_nav_name = parts[0]        # 顶部导航标签，如 "供需与约束"
        left_panel_path = parts[1:]    # 左侧面板路径，如 ["参数信息"]

        logger.info("综合查询页面导航: %s > %s > %s",
                    top_nav_name, " > ".join(left_panel_path), page_name)

        # ── 第1步：点击顶部导航栏标签 ──
        logger.info("点击顶部导航「%s」...", top_nav_name)
        try:
            tab = self._find_clickable_text(top_nav_name)
            tab.scroll_into_view_if_needed()
            ui_sleep("short")
            tab.click()
            ui_sleep("xlong")
            logger.info("已点击顶部导航「%s」", top_nav_name)
        except Exception as e:
            logger.error("点击顶部导航「%s」失败: %s", top_nav_name, e)
            self._save_debug_screenshot(f"comp_top_nav_{top_nav_name}_failed")

            # 页面可能已被系统刷新回首页，尝试重新点击侧边栏叶子节点打开综合查询页面
            if category:
                logger.warning(
                    "疑似页面已刷新回首页，重新点击侧边栏「%s」后重试顶部导航...",
                    category,
                )
                try:
                    # 页面刷新后侧边栏状态已重置，需重新展开「信息披露」
                    self._info_disclosure_expanded = False
                    self._current_category = None
                    self.navigate_to_info_disclosure()
                    self._click_tree_leaf(category, timeout=15000)
                    ui_sleep("xlong")
                    try:
                        self.page.wait_for_load_state("networkidle", timeout=15000)
                    except PlaywrightTimeout:
                        pass
                    tab = self._find_clickable_text(top_nav_name)
                    tab.scroll_into_view_if_needed()
                    ui_sleep("short")
                    tab.click()
                    ui_sleep("xlong")
                    logger.info("重试成功：已点击顶部导航「%s」", top_nav_name)
                except Exception as retry_e:
                    logger.error("重试点击顶部导航「%s」仍失败: %s", top_nav_name, retry_e)
                    self._save_debug_screenshot(f"comp_top_nav_{top_nav_name}_retry_failed")
                    raise PlaywrightTimeout(
                        f"无法点击综合查询顶部导航「{top_nav_name}」"
                    )
            else:
                raise PlaywrightTimeout(
                    f"无法点击综合查询顶部导航「{top_nav_name}」"
                )

        # 等待页面内容更新
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout:
            pass
        ui_sleep("long")

        # ── 第2步：在左侧面板中展开中间分类 ──
        for cat in left_panel_path:
            logger.info("展开左侧面板「%s」...", cat)
            try:
                cat_el = self._find_clickable_text(cat)
                cat_el.scroll_into_view_if_needed()
                ui_sleep("short")
                cat_el.click()
                ui_sleep("long")
                logger.info("已展开左侧面板「%s」", cat)
            except Exception as e:
                logger.warning("展开左侧面板「%s」失败: %s", cat, e)
                self._save_debug_screenshot(f"comp_left_{cat}_failed")

        # ── 第3步：点击最终目标页面 ──
        logger.info("点击目标「%s」...", page_name)
        try:
            target = self._find_clickable_text(page_name)
            target.scroll_into_view_if_needed()
            ui_sleep("short")
            target.click()
            ui_sleep("xlong")
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass
            logger.info("已导航到综合查询「%s」", page_name)
        except Exception as e:
            logger.error("点击综合查询目标「%s」失败: %s", page_name, e)
            self._save_debug_screenshot(f"comp_target_{page_name}_failed")
            raise PlaywrightTimeout(
                f"无法点击综合查询目标页「{page_name}」"
            )

    # ── 内容区操作 ────────────────────────────────────────────

    def click_tab(self, tab_name: str):
        """
        点击内容区顶部的 tab 标签

        Args:
            tab_name: tab 名称
        """
        logger.info("点击 Tab: %s", tab_name)
        try:
            tab = self.page.locator(f"text={tab_name}").first
            tab.click()
            ui_sleep("long")
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout:
            logger.warning("未找到 Tab「%s」", tab_name)

    def wait_for_table(self, timeout: int = 15000):
        """等待表格加载完成"""
        try:
            self.page.wait_for_selector("table", timeout=timeout)
            ui_sleep("long")
            logger.debug("表格已加载")
        except PlaywrightTimeout:
            logger.warning("等待表格超时 (%dms)", timeout)
