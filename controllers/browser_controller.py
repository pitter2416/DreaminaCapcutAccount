import os
import threading
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class BrowserConfig:
    headless: bool


class BrowserController:
    """
    负责：
    - 固定 PLAYWRIGHT_BROWSERS_PATH 到项目目录
    - 每线程复用 browser 实例（thread-local）
    - 任务完成后关闭 context/page（避免泄漏）
    """

    def __init__(self, cfg: BrowserConfig, browser_root: str):
        self.cfg = cfg
        self.browser_root = browser_root
        # 强制使用项目内浏览器目录，避免被外部环境变量污染。
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_root

        self._thread_local = threading.local()
        self._resources_lock = threading.Lock()
        self._resources: list[Tuple[object, object]] = []

    def _ensure_browser(self) -> object:
        if hasattr(self._thread_local, "browser"):
            return self._thread_local.browser

        from playwright.sync_api import sync_playwright  # type: ignore

        p = sync_playwright().start()
        b = p.chromium.launch(
            headless=self.cfg.headless,
            args=["--lang=zh-CN"],
        )

        self._thread_local.playwright = p
        self._thread_local.browser = b
        with self._resources_lock:
            self._resources.append((p, b))
        return b

    def new_page(self) -> object:
        browser = self._ensure_browser()
        context = browser.new_context()
        return context.new_page()

    def close_page(self, page: Optional[object]) -> None:
        if not page:
            return
        try:
            context = page.context
            context.close()
        except Exception:
            pass

    def close_all(self) -> None:
        with self._resources_lock:
            resources = list(self._resources)
            self._resources.clear()
        for p, b in resources:
            try:
                b.close()
            except Exception:
                pass
            try:
                p.stop()
            except Exception:
                pass

