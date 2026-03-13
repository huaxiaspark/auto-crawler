"""
浏览器管理模块

支持两种工作模式：
  - connect（默认）：通过 CDP 连接到已打开且已登录的 Chrome 浏览器
    * 适用于服务器部署环境：Chrome 已在桌面打开并完成登录
    * 需要 Chrome 启动时带 --remote-debugging-port 参数
    * 脚本结束后只断开连接，不关闭浏览器和页面
  - launch：启动全新的 Chromium 浏览器实例
    * 适用于本地调试或无需登录的场景
    * 脚本结束后关闭浏览器
"""

import os
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from utils.logger import get_logger

logger = get_logger()


class BrowserManager:
    """浏览器生命周期管理器"""

    def __init__(self, config: dict):
        self.full_config = config
        self.config = config.get("browser", {})
        self.mode = self.config.get("mode", "connect")
        self.cdp_url = self.config.get("cdp_url", "http://localhost:9222")
        self.headless = self.config.get("headless", False)
        self.slow_mo = self.config.get("slow_mo", 300)
        self.timeout = self.config.get("timeout", 30000)
        self.download_dir = os.path.abspath(
            self.config.get("download_dir", "./data/exports")
        )
        self.viewport = self.config.get("viewport", {"width": 1920, "height": 1080})
        # 用于匹配目标页面的 URL 关键词
        self.target_url_pattern = self.config.get(
            "target_url_pattern", "pmos.sx.sgcc.com.cn"
        )

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def start(self) -> Page:
        """根据配置模式启动或连接浏览器"""
        os.makedirs(self.download_dir, exist_ok=True)

        if self.mode == "connect":
            return self._connect_existing()
        else:
            return self._launch_new()

    def _connect_existing(self) -> Page:
        """
        通过 CDP 连接到已打开的 Chrome 浏览器

        前提条件：
        - Chrome 已启动，且带 --remote-debugging-port 参数
        - 用户已在 Chrome 中完成登录并停留在目标页面

        连接后会查找包含目标 URL 的标签页。
        """
        logger.info("正在通过 CDP 连接到已有 Chrome (%s)...", self.cdp_url)

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as e:
            logger.error(
                "无法连接到 Chrome，请确认：\n"
                "  1. Chrome 已启动，且带 --remote-debugging-port 参数\n"
                "     启动命令示例: google-chrome --remote-debugging-port=9222\n"
                "  2. CDP 地址正确: %s\n"
                "  错误: %s",
                self.cdp_url, e,
            )
            self._playwright.stop()
            raise

        # 在已有的 contexts 中查找目标页面
        page = self._find_target_page()
        if page is None:
            logger.error(
                "已连接到 Chrome，但未找到包含「%s」的标签页。\n"
                "请确认 Chrome 中已打开目标网站并完成登录。",
                self.target_url_pattern,
            )
            raise RuntimeError(
                f"未找到包含「{self.target_url_pattern}」的标签页"
            )

        self._page = page
        # 设置默认超时
        self._page.set_default_timeout(self.timeout)

        logger.info("已连接到现有页面: %s", self._page.url)
        return self._page

    def _find_target_page(self) -> Optional[Page]:
        """
        在所有已打开的标签页中查找目标页面

        Returns:
            匹配的 Page 对象，未找到返回 None
        """
        contexts = self._browser.contexts
        logger.debug("浏览器有 %d 个 context", len(contexts))

        for ctx_idx, ctx in enumerate(contexts):
            pages = ctx.pages
            logger.debug("  Context %d: %d 个页面", ctx_idx, len(pages))
            for page in pages:
                logger.debug("    标签页 URL: %s", page.url)
                if self.target_url_pattern in page.url:
                    self._context = ctx
                    return page

        # 未找到匹配页面，列出所有标签页帮助诊断
        logger.warning("未找到目标页面，当前所有标签页:")
        for ctx in contexts:
            for page in ctx.pages:
                logger.warning("  - %s", page.url)

        return None

    def _launch_new(self) -> Page:
        """启动全新的 Chromium 浏览器实例（原有逻辑）"""
        # 无图形环境（如 SSH 无 DISPLAY）时强制使用无头模式
        if not self.headless and not os.environ.get("DISPLAY"):
            self.headless = True
            logger.info("检测到无 DISPLAY 环境，已自动切换为无头模式 (headless=True)")

        logger.info("正在启动 Chromium 浏览器 (headless=%s)...", self.headless)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )

        self._context = self._browser.new_context(
            viewport=self.viewport,
            accept_downloads=True,
        )
        self._context.set_default_timeout(self.timeout)

        self._page = self._context.new_page()
        logger.info("浏览器启动成功")
        return self._page

    @property
    def page(self) -> Page:
        """获取当前页面"""
        if self._page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """获取浏览器上下文"""
        if self._context is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._context

    def navigate(self, url: str):
        """导航到指定 URL"""
        logger.info("正在导航到: %s", url)
        self.page.goto(url, wait_until="networkidle")
        logger.info("页面加载完成")

    def wait_for_load(self, timeout: Optional[int] = None):
        """等待页面加载完成"""
        t = timeout or self.timeout
        self.page.wait_for_load_state("networkidle", timeout=t)

    def screenshot(self, path: str):
        """截屏保存"""
        self.page.screenshot(path=path)
        logger.debug("截屏已保存: %s", path)

    def close(self):
        """
        释放浏览器资源

        connect 模式：只断开 CDP 连接，不关闭浏览器和页面
        launch 模式：关闭浏览器及所有页面
        """
        if self.mode == "connect":
            logger.info("正在断开浏览器连接（保持 Chrome 运行）...")
            try:
                # 注意：connect 模式下不能 close page/context，否则会关闭用户的标签页
                if self._browser:
                    self._browser.close()   # CDP 模式下仅断开连接
                if self._playwright:
                    self._playwright.stop()
            except Exception as e:
                logger.warning("断开连接时出错: %s", e)
            finally:
                self._page = None
                self._context = None
                self._browser = None
                self._playwright = None
                logger.info("已断开浏览器连接")
        else:
            logger.info("正在关闭浏览器...")
            try:
                if self._page:
                    self._page.close()
                if self._context:
                    self._context.close()
                if self._browser:
                    self._browser.close()
                if self._playwright:
                    self._playwright.stop()
            except Exception as e:
                logger.warning("关闭浏览器时出错: %s", e)
            finally:
                self._page = None
                self._context = None
                self._browser = None
                self._playwright = None
                logger.info("浏览器已关闭")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
