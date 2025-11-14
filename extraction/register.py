"""Registration workflow orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .automation import (
    EMAIL_SELECTORS,
    KEYWORD_CLICKS,
    PASSWORD_SELECTORS,
    click_keywords,
    fill_form_fields,
    find_form,
    submit_form,
)
from .browser import BrowserConfig, BrowserSession
from .io_utils import RunPaths, relative_artifact_path

DEFAULT_PASSWORD = "AntiScam!234"


@dataclass(slots=True)
class RegisterInputs:
    url: str
    email: str
    password: str
    run_paths: RunPaths
    logger: logging.Logger


def run_registration(inputs: RegisterInputs) -> Dict[str, object]:
    logger = inputs.logger
    run_paths = inputs.run_paths
    artifacts: List[str] = []
    notes: List[str] = []
    status = "failed"
    final_url = inputs.url

    try:
        logger.debug("Starting registration against %s as %s", inputs.url, inputs.email)
        with BrowserSession(BrowserConfig()) as browser:
            page = browser.goto(inputs.url, wait_until="networkidle")
            final_url = page.url
            logger.debug("Loaded landing page %s", final_url)
            landing_shot = browser.screenshot(run_paths.build_path("00_landing.png"))
            artifacts.append(relative_artifact_path(landing_shot))

            form = find_form(
                page,
                {"email": EMAIL_SELECTORS, "password": PASSWORD_SELECTORS},
                logger=logger,
            )
            if not form:
                logger.debug("Registration form not found, trying keyword navigation")
                click_keywords(page, KEYWORD_CLICKS, max_clicks=4, logger=logger)
                form = find_form(
                    page,
                    {"email": EMAIL_SELECTORS, "password": PASSWORD_SELECTORS},
                    logger=logger,
                )

            if not form:
                status = "no_form_found"
                notes.append(
                    "Could not identify a registration form with email + password fields."
                )
                logger.warning("Registration form still missing after heuristics")
            else:
                logger.debug("Registration form located, filling fields")
                fill_form_fields(
                    form,
                    {
                        "email": inputs.email,
                        "password": inputs.password or DEFAULT_PASSWORD,
                    },
                    logger=logger,
                )
                pre_submit_shot = browser.screenshot(
                    run_paths.build_path("01_filled.png")
                )
                artifacts.append(relative_artifact_path(pre_submit_shot))
                logger.debug("Submitting registration form")
                submit_form(form, logger=logger)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except PlaywrightTimeoutError:
                    logger.debug(
                        "Registration submission did not trigger navigation within timeout"
                    )
                final_url = page.url
                logger.debug("Post-submission URL: %s", final_url)
                status = "submitted"
                notes.append("Registration form submitted.")

            html_path = browser.save_html(run_paths.build_path("final.html"))
            artifacts.append(relative_artifact_path(html_path))
            screenshot_path = browser.screenshot(run_paths.build_path("02_final.png"))
            artifacts.append(relative_artifact_path(screenshot_path))

    except Exception as exc:  # noqa: BLE001
        logger.exception("Registration command failed: %s", exc)
        notes.append(str(exc))
        status = "error"

    result = {
        "run_id": run_paths.run_id,
        "input_url": inputs.url,
        "final_url": final_url,
        "status": status,
        "notes": " | ".join(notes) if notes else "",
        "artifacts": artifacts,
    }
    return result
