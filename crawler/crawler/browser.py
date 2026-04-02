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
import urllib.request
import json
from typing import Optional
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from utils.logger import get_logger

logger = get_logger()
KEEPALIVE_PAGE_MARKER = "AUTO_CRAWLER_KEEPALIVE"


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
            # Chrome 以 --remote-debugging-address=0.0.0.0 启动时，
            # /json/version 返回的 webSocketDebuggerUrl 主机为 0.0.0.0，
            # 容器内无法连接。需手动获取并替换为实际可达的主机。
            cdp_host = urlparse(self.cdp_url).netloc  # e.g. host.docker.internal:9222
            version_url = f"{self.cdp_url.rstrip('/')}/json/version"
            with urllib.request.urlopen(version_url, timeout=10) as resp:
                info = json.loads(resp.read())
            ws_url = info.get("webSocketDebuggerUrl", "")
            if ws_url:
                parsed = urlparse(ws_url)
                ws_url = urlunparse(parsed._replace(netloc=cdp_host))
                logger.debug("使用修正后的 WebSocket URL: %s", ws_url)
                self._browser = self._playwright.chromium.connect_over_cdp(ws_url)
            else:
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

    def _is_keepalive_page(self, page: Page) -> bool:
        """判断标签页是否为保活专用页面。"""
        try:
            return page.evaluate("() => window.name") == KEEPALIVE_PAGE_MARKER
        except Exception:
            return False

    def _mark_page_as_keepalive(self, page: Page):
        """给标签页打上保活标记，便于爬虫连接时跳过。"""
        page.evaluate(f"() => {{ window.name = '{KEEPALIVE_PAGE_MARKER}'; }}")

    def _find_target_page(self) -> Optional[Page]:
        """
        在所有已打开的标签页中查找目标页面

        Returns:
            匹配的 Page 对象，未找到返回 None
        """
        contexts = self._browser.contexts
        logger.debug("浏览器有 %d 个 context", len(contexts))

        keepalive_candidate = None

        for ctx_idx, ctx in enumerate(contexts):
            pages = ctx.pages
            logger.debug("  Context %d: %d 个页面", ctx_idx, len(pages))
            for page in pages:
                logger.debug("    标签页 URL: %s", page.url)
                if self.target_url_pattern in page.url:
                    if self._is_keepalive_page(page):
                        keepalive_candidate = keepalive_candidate or (ctx, page)
                        logger.debug("    跳过保活专用标签页: %s", page.url)
                        continue
                    self._context = ctx
                    return page

        if keepalive_candidate is not None:
            self._context, page = keepalive_candidate
            logger.warning("仅找到保活专用标签页，将复用该标签页执行爬取")
            return page

        # 未找到匹配页面，列出所有标签页帮助诊断
        logger.warning("未找到目标页面，当前所有标签页:")
        for ctx in contexts:
            for page in ctx.pages:
                logger.warning("  - %s", page.url)

        return None

    def get_or_create_keepalive_page(self, target_url: str) -> Page:
        """
        获取或创建保活专用标签页。

        connect 模式下优先复用当前 context 中已存在的保活标签页；
        若不存在，则在当前登录 context 中新建一个标签页，并写入 window.name 标记。
        """
        if self._browser is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")

        for ctx in self._browser.contexts:
            for page in ctx.pages:
                if self._is_keepalive_page(page):
                    self._context = ctx
                    page.set_default_timeout(self.timeout)
                    return page

        ctx = self._context
        if ctx is None:
            contexts = self._browser.contexts
            if not contexts:
                raise RuntimeError("当前浏览器中没有可用 context，无法创建保活标签页")
            ctx = contexts[0]
            self._context = ctx

        page = ctx.new_page()
        page.set_default_timeout(self.timeout)
        self._mark_page_as_keepalive(page)
        page.goto(target_url, wait_until="domcontentloaded")
        logger.info("已创建保活专用标签页: %s", target_url)
        return page

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
