from __future__ import annotations

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def _log_fallback(logger) -> None:
    if logger:
        logger.debug("load state timed out, retrying with domcontentloaded")


def safe_goto(
    page,
    url: str,
    *,
    wait_until: str = "load",
    timeout_ms: int = 20000,
    logger=None,
):
    try:
        return page.goto(url, wait_until=wait_until, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        if wait_until != "load":
            raise
        _log_fallback(logger)
        return page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)


def wait_for_page_ready(
    page,
    *,
    wait_until: str = "load",
    timeout_ms: int = 20000,
    logger=None,
):
    try:
        return page.wait_for_load_state(wait_until, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        if wait_until != "load":
            raise
        _log_fallback(logger)
        return page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
