"""Extraction workflow for deposit instructions."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .automation import (
    AUTH_KEYWORDS,
    EMAIL_SELECTORS,
    KEYWORD_CLICKS,
    SECRET_SELECTORS,
    click_by_text,
    click_keywords,
    detect_error_banner,
    fill_form_fields,
    find_form,
    submit_form,
)
from .browser import BrowserConfig, BrowserSession
from .io_utils import RunPaths, relative_artifact_path, sanitize_filename, save_text
from .parsing import Indicator, extract_indicators

MODULE_LOGGER = logging.getLogger(__name__)

EXPLORATION_KEYWORDS = (
    "deposit",
    "wallet",
    "cashier",
    "bank",
    "iban",
    "transfer",
    "crypto",
    "btc",
    "eth",
    "usdt",
    "wallet",
    "pay",
    "top up",
    "fund",
)
LOGIN_KEYWORDS = ("login", "log in", "signin", "sign in", "client area")
LOGGED_IN_HINTS = ("logout", "log out", "dashboard", "my account", "profile", "cabinet")
DEPOSIT_METHOD_KEYWORDS = (
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "tether",
    "usdt",
    "trc20",
    "erc20",
    "litecoin",
    "ltc",
    "bank transfer",
    "wire",
    "visa",
    "mastercard",
)
REVEAL_KEYWORDS = ("show", "view", "display", "copy", "reveal", "get address")


@dataclass(slots=True)
class ExtractInputs:
    url: str
    email: str
    secret: str
    run_paths: RunPaths
    logger: logging.Logger
    max_steps: int = 5


def run_extraction(inputs: ExtractInputs) -> Dict[str, object]:
    logger = inputs.logger
    run_paths = inputs.run_paths
    artifacts: List[str] = []
    notes: List[str] = []
    status = "error"
    final_url = inputs.url
    indicator_records: List[Indicator] = []

    try:
        logger.debug("Starting extract run for %s as %s", inputs.url, inputs.email)
        with BrowserSession(BrowserConfig()) as browser:
            page = browser.goto(inputs.url, wait_until="networkidle")
            final_url = page.url
            logger.debug("Loaded entry page %s", final_url)
            landing_shot = browser.screenshot(run_paths.build_path("00_landing.png"))
            artifacts.append(relative_artifact_path(landing_shot))

            logger.debug("Looking for login form on %s", page.url)
            form = find_form(
                page,
                {"email": EMAIL_SELECTORS, "secret": SECRET_SELECTORS},
                logger=logger,
            )
            if not form:
                logger.debug("Login form not found, attempting auth navigation")
                navigate_to_login(page, logger=logger)
                form = find_form(
                    page,
                    {"email": EMAIL_SELECTORS, "secret": SECRET_SELECTORS},
                    logger=logger,
                )

            if not form:
                status = "no_form_found"
                notes.append(
                    "Could not locate a login form with email + secret fields."
                )
                logger.warning("Login form still missing after heuristics")
            else:
                logger.debug("Login form located, populating credentials")
                fill_form_fields(
                    form,
                    {"email": inputs.email, "secret": inputs.secret},
                    logger=logger,
                )
                pre_submit_url = page.url
                logger.debug("Submitting login form")
                submit_form(form, logger=logger)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeoutError:
                    logger.debug(
                        "Login submission did not trigger navigation within timeout"
                    )
                final_url = page.url
                logger.debug("Post-login URL candidate: %s", final_url)

                state_artifacts, state_indicators = capture_page_state(
                    browser, run_paths, "01_post_login"
                )
                artifacts.extend(state_artifacts)
                indicator_records.extend(state_indicators)
                reveal_artifacts, reveal_indicators = reveal_hidden_sections(
                    browser, run_paths, "01_post_login", logger
                )
                artifacts.extend(reveal_artifacts)
                indicator_records.extend(reveal_indicators)
                method_artifacts, method_indicators = click_deposit_methods(
                    browser, run_paths, "01_post_login", logger
                )
                artifacts.extend(method_artifacts)
                indicator_records.extend(method_indicators)

                error_text = detect_error_banner(page, logger=logger)
                logged_in = infer_login_success(
                    page, pre_submit_url, error_text, logger=logger
                )
                if error_text:
                    notes.append(error_text)
                if not logged_in:
                    status = "login_failed"
                    logger.warning("Login appears to have failed")
                else:
                    status = "complete"
                    logger.debug("Login succeeded; starting exploration")
                    more_artifacts, more_indicators = explore_interesting_pages(
                        browser, inputs.max_steps, run_paths, logger
                    )
                    artifacts.extend(more_artifacts)
                    indicator_records.extend(more_indicators)

    except Exception as exc:  # noqa: BLE001
        logger.exception("Extraction command failed: %s", exc)
        notes.append(str(exc))
        status = "error"

    result = {
        "run_id": run_paths.run_id,
        "input_url": inputs.url,
        "final_url": final_url,
        "status": status,
        "notes": " | ".join(notes) if notes else "",
        "indicators": [asdict(indicator) for indicator in indicator_records],
        "artifacts": artifacts,
    }
    return result


def infer_login_success(
    page,
    previous_url: str,
    error_text: str | None,
    logger: logging.Logger | None = None,
) -> bool:
    log = logger or MODULE_LOGGER
    if error_text:
        log.info("Error banner present after login attempt: %s", error_text)
        return False
    if page.url != previous_url:
        log.debug("URL changed after login submit (%s -> %s)", previous_url, page.url)
        return True
    for keyword in LOGGED_IN_HINTS:
        locator = page.get_by_text(re.compile(keyword, re.IGNORECASE))
        if locator.count() > 0:
            log.debug("Detected logged-in hint '%s' on page", keyword)
            return True
    log.debug("No sign of logged-in state detected")
    return False


def explore_interesting_pages(
    browser: BrowserSession,
    max_steps: int,
    run_paths: RunPaths,
    logger: logging.Logger,
) -> tuple[List[str], List[Indicator]]:
    artifacts: List[str] = []
    indicators: List[Indicator] = []
    page = browser.page
    steps = 0
    for keyword in EXPLORATION_KEYWORDS:
        if steps >= max_steps:
            break
        logger.debug("Exploration step %d: looking for '%s'", steps + 1, keyword)
        clicked = click_by_text(page, keyword, logger=logger)
        if not clicked:
            logger.debug("Keyword '%s' not found on current page", keyword)
            continue
        steps += 1
        try:
            page.wait_for_load_state("networkidle", timeout=7000)
        except PlaywrightTimeoutError:
            logger.debug(
                "Navigation after clicking '%s' did not complete in time", keyword
            )
        label = f"step_{steps:02d}_{sanitize_filename(keyword)}"
        view_artifacts, view_indicators = capture_page_state(browser, run_paths, label)
        artifacts.extend(view_artifacts)
        indicators.extend(view_indicators)
        reveal_artifacts, reveal_indicators = reveal_hidden_sections(
            browser, run_paths, label, logger
        )
        artifacts.extend(reveal_artifacts)
        indicators.extend(reveal_indicators)
        method_artifacts, method_indicators = click_deposit_methods(
            browser, run_paths, label, logger
        )
        artifacts.extend(method_artifacts)
        indicators.extend(method_indicators)
    if indicators:
        logger.info("Detected %d indicators during exploration", len(indicators))
    else:
        logger.info("No deposit indicators detected during exploration")
    return artifacts, indicators


def navigate_to_login(page, *, logger: logging.Logger, max_clicks: int = 5) -> None:
    clicks = 0
    for keyword in AUTH_KEYWORDS:
        if clicks >= max_clicks:
            break
        logger.debug("Attempting to reach login via keyword '%s'", keyword)
        clicked = click_by_text(page, keyword, logger=logger)
        if clicked:
            clicks += 1
            page.wait_for_timeout(800)


def _tag_indicators(html: str, url: str, html_path: Path) -> List[Indicator]:
    tagged: List[Indicator] = []
    for indicator in extract_indicators(html, url):
        indicator.artifact = relative_artifact_path(html_path)
        tagged.append(indicator)
    return tagged


def capture_page_state(
    browser: BrowserSession, run_paths: RunPaths, label: str
) -> Tuple[List[str], List[Indicator]]:
    page = browser.page
    html = page.content()
    html_path = save_text(run_paths.build_path(f"{label}.html"), html)
    screenshot_path = browser.screenshot(run_paths.build_path(f"{label}.png"))
    artifacts = [
        relative_artifact_path(html_path),
        relative_artifact_path(screenshot_path),
    ]
    indicators = _tag_indicators(html, page.url, html_path)
    return artifacts, indicators


def reveal_hidden_sections(
    browser: BrowserSession,
    run_paths: RunPaths,
    base_label: str,
    logger: logging.Logger,
    max_clicks: int = 5,
) -> Tuple[List[str], List[Indicator]]:
    artifacts: List[str] = []
    indicators: List[Indicator] = []
    page = browser.page
    clicks = 0
    for keyword in REVEAL_KEYWORDS:
        if clicks >= max_clicks:
            break
        clicked = click_by_text(page, keyword, logger=logger)
        if not clicked:
            continue
        clicks += 1
        page.wait_for_timeout(600)
        label = f"{base_label}_reveal_{clicks:02d}_{sanitize_filename(keyword)}"
        view_artifacts, view_indicators = capture_page_state(browser, run_paths, label)
        artifacts.extend(view_artifacts)
        indicators.extend(view_indicators)
    return artifacts, indicators


def click_deposit_methods(
    browser: BrowserSession,
    run_paths: RunPaths,
    base_label: str,
    logger: logging.Logger,
    max_clicks: int = 6,
) -> Tuple[List[str], List[Indicator]]:
    artifacts: List[str] = []
    indicators: List[Indicator] = []
    page = browser.page
    clicks = 0
    for keyword in DEPOSIT_METHOD_KEYWORDS:
        if clicks >= max_clicks:
            break
        clicked = click_by_text(page, keyword, logger=logger)
        if not clicked:
            continue
        clicks += 1
        page.wait_for_timeout(600)
        label = f"{base_label}_method_{clicks:02d}_{sanitize_filename(keyword)}"
        view_artifacts, view_indicators = capture_page_state(browser, run_paths, label)
        artifacts.extend(view_artifacts)
        indicators.extend(view_indicators)
    return artifacts, indicators
