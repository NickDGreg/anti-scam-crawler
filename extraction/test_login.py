"""
Simple helper to open a Playwright inspector against an arbitrary login page.
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright


def launch_login_inspector(url: str) -> None:
    """Open the given URL with Playwright in headed mode and pause for manual exploration."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url)
        page.pause()


if __name__ == "__main__":  # pragma: no cover
    launch_login_inspector("https://www.smartcryptoplatform.com/login.php")
