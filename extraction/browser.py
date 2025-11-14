"""Thin wrapper around Playwright to keep browser concerns isolated."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)


@dataclass(slots=True)
class BrowserConfig:
    headless: bool = True
    slow_mo: float = 0
    navigation_timeout_ms: int = 45000
    viewport_width: int = 1280
    viewport_height: int = 720


class BrowserSession:
    """Context manager that owns a Playwright browser/page pair."""

    def __init__(self, config: Optional[BrowserConfig] = None) -> None:
        self.config = config or BrowserConfig()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.config.headless, slow_mo=self.config.slow_mo
        )
        self._context = self._browser.new_context(
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            }
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.config.navigation_timeout_ms)
        self._page.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("BrowserSession is not started")
        return self._page

    def goto(
        self,
        url: str,
        wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"]
        | None = "load",
    ) -> Page:
        page = self.page
        page.goto(url, wait_until=wait_until)
        return page

    def screenshot(self, path: Path, *, full_page: bool = True) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=full_page)
        return path

    def save_html(self, path: Path) -> Path:
        html = self.page.content()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return path

    def close(self) -> None:
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
