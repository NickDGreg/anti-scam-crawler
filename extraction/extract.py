"""Extraction workflow for deposit instructions."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .automation import (
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
LOGIN_KEYWORDS = ("login", "log in", "sign in", "client area")
LOGGED_IN_HINTS = ("logout", "log out", "dashboard", "my account", "profile", "cabinet")


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
        with BrowserSession(BrowserConfig()) as browser:
            page = browser.goto(inputs.url, wait_until="networkidle")
            final_url = page.url
            landing_shot = browser.screenshot(run_paths.build_path("00_landing.png"))
            artifacts.append(relative_artifact_path(landing_shot))

            form = find_form(
                page, {"email": EMAIL_SELECTORS, "secret": SECRET_SELECTORS}
            )
            if not form:
                click_keywords(page, LOGIN_KEYWORDS + KEYWORD_CLICKS, max_clicks=4)
                form = find_form(
                    page, {"email": EMAIL_SELECTORS, "secret": SECRET_SELECTORS}
                )

            if not form:
                status = "no_form_found"
                notes.append(
                    "Could not locate a login form with email + secret fields."
                )
            else:
                fill_form_fields(form, {"email": inputs.email, "secret": inputs.secret})
                pre_submit_url = page.url
                submit_form(form)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeoutError:
                    logger.debug(
                        "Login submission did not trigger navigation within timeout"
                    )
                final_url = page.url

                post_login_html = page.content()
                html_path = save_text(
                    run_paths.build_path("01_post_login.html"), post_login_html
                )
                artifacts.append(relative_artifact_path(html_path))
                screenshot_path = browser.screenshot(
                    run_paths.build_path("01_post_login.png")
                )
                artifacts.append(relative_artifact_path(screenshot_path))

                error_text = detect_error_banner(page)
                logged_in = infer_login_success(page, pre_submit_url, error_text)
                if error_text:
                    notes.append(error_text)
                if not logged_in:
                    status = "login_failed"
                else:
                    status = "complete"
                    indicator_records.extend(
                        _tag_indicators(post_login_html, page.url, html_path)
                    )
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


def infer_login_success(page, previous_url: str, error_text: str | None) -> bool:
    if error_text:
        return False
    if page.url != previous_url:
        return True
    for keyword in LOGGED_IN_HINTS:
        locator = page.get_by_text(re.compile(keyword, re.IGNORECASE))
        if locator.count() > 0:
            return True
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
        clicked = click_by_text(page, keyword)
        if not clicked:
            continue
        steps += 1
        try:
            page.wait_for_load_state("networkidle", timeout=7000)
        except PlaywrightTimeoutError:
            logger.debug(
                "Navigation after clicking '%s' did not complete in time", keyword
            )
        label = f"step_{steps:02d}_{sanitize_filename(keyword)}"
        html = page.content()
        html_path = save_text(run_paths.build_path(f"{label}.html"), html)
        artifacts.append(relative_artifact_path(html_path))
        screenshot_path = browser.screenshot(run_paths.build_path(f"{label}.png"))
        artifacts.append(relative_artifact_path(screenshot_path))
        indicators.extend(_tag_indicators(html, page.url, html_path))
    if not indicators:
        logger.info("No deposit indicators detected during exploration")
    return artifacts, indicators


def _tag_indicators(html: str, url: str, html_path: Path) -> List[Indicator]:
    tagged: List[Indicator] = []
    for indicator in extract_indicators(html, url):
        indicator.artifact = relative_artifact_path(html_path)
        tagged.append(indicator)
    return tagged
