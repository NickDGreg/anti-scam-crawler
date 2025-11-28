"""Shared login helpers for Playwright-based flows."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Tuple
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .automation import (
    AUTH_KEYWORDS,
    EMAIL_SELECTORS,
    SECRET_SELECTORS,
    click_by_text,
    detect_error_banner,
    fill_form_fields,
    find_form,
    submit_form,
)

LOGGED_IN_HINTS = ("logout", "log out", "dashboard", "my account", "profile", "cabinet")
LOGIN_PATH_HINTS = ("login", "signin", "sign-in", "sign_in")


@dataclass(slots=True)
class LoginResult:
    success: bool
    status: str
    notes: List[str]


def get_login_form(page, *, logger: logging.Logger | None = None):
    return find_form(
        page,
        {"email": EMAIL_SELECTORS, "secret": SECRET_SELECTORS},
        logger=logger,
    )


def navigate_to_login(page, *, logger: logging.Logger, max_clicks: int = 5) -> None:
    clicks = 0
    for keyword in AUTH_KEYWORDS:
        if clicks >= max_clicks:
            break
        if get_login_form(page, logger=logger):
            logger.debug("Login form detected during auth navigation; stopping")
            return
        logger.debug("Attempting to reach login via keyword '%s'", keyword)
        clicked = click_by_text(page, keyword, logger=logger)
        if clicked:
            clicks += 1
            page.wait_for_timeout(800)
            if get_login_form(page, logger=logger):
                logger.debug("Login form detected after clicking '%s'", keyword)
                return
    logger.debug("Auth navigation finished without detecting login form")


def infer_login_success(
    page,
    previous_url: str,
    error_text: str | None,
    *,
    logger: logging.Logger | None = None,
    login_form_present: bool = False,
) -> bool:
    log = logger or logging.getLogger(__name__)
    if error_text:
        log.info("Error banner present after login attempt: %s", error_text)
        return False

    prev = urlparse(previous_url)
    curr = urlparse(page.url)
    prev_host = (prev.hostname or "").lower().lstrip("www.")
    curr_host = (curr.hostname or "").lower().lstrip("www.")
    same_path = prev.path == curr.path
    same_query = prev.query == curr.query
    same_host = prev_host == curr_host
    curr_path = curr.path or ""

    if is_login_path(curr_path) and login_form_present:
        log.debug("Still on login path '%s' with login form visible", curr_path)
        return False

    if not is_login_path(curr_path):
        for keyword in LOGGED_IN_HINTS:
            locator = page.get_by_text(re.compile(keyword, re.IGNORECASE))
            if locator.count() > 0:
                log.debug("Detected logged-in hint '%s' on page", keyword)
                return True
        if not (same_path and same_host and same_query):
            log.debug(
                "URL changed after login submit (%s -> %s)", previous_url, page.url
            )
            return True
    else:
        log.debug("Current path '%s' still resembles a login route", curr_path)

    if login_form_present:
        log.debug("Login form still present after submit; treating as login failure")
        return False

    log.debug("No sign of logged-in state detected")
    return False


def attempt_login_with_retries(
    page,
    *,
    email: str,
    secret: str,
    logger: logging.Logger,
    max_attempts: int = 2,
) -> Tuple[bool, str | None]:
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        form = get_login_form(page, logger=logger)
        if not form:
            logger.warning("Login form missing before attempt %d", attempt)
            break

        logger.debug("Login attempt %d: populating credentials", attempt)
        fill_form_fields(form, {"email": email, "secret": secret}, logger=logger)
        pre_submit_url = page.url
        logger.debug("Submitting login form (attempt %d)", attempt)
        submit_form(form, logger=logger)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            logger.debug(
                "Login attempt %d did not trigger navigation within timeout", attempt
            )

        login_form_present_flag = login_form_still_present(page, logger)
        error_text = detect_error_banner(page, logger=logger)
        success = infer_login_success(
            page,
            pre_submit_url,
            error_text,
            logger=logger,
            login_form_present=login_form_present_flag,
        )
        logger.debug(
            "Login attempt %d result: success=%s (pre=%s -> post=%s)",
            attempt,
            success,
            pre_submit_url,
            page.url,
        )
        if success:
            return True, error_text
        last_error = error_text
        if error_text:
            logger.warning("Login attempt %d returned error: %s", attempt, error_text)
            break
        logger.debug(
            "Login attempt %d failed without explicit error; retrying", attempt
        )
    return False, last_error


def login_form_still_present(page, logger: logging.Logger | None = None) -> bool:
    form = get_login_form(page, logger=logger)
    return form is not None


def is_login_path(path: str) -> bool:
    normalized = (path or "").lower()
    return any(hint in normalized for hint in LOGIN_PATH_HINTS)


def perform_login(
    page,
    *,
    email: str,
    secret: str,
    logger: logging.Logger,
    max_attempts: int = 2,
) -> LoginResult:
    notes: List[str] = []
    form = get_login_form(page, logger=logger)
    if not form:
        logger.debug("Login form not found, attempting auth navigation")
        navigate_to_login(page, logger=logger)
        form = get_login_form(page, logger=logger)

    if not form:
        notes.append("Could not locate a login form with email + secret fields.")
        logger.warning("Login form still missing after heuristics")
        return LoginResult(success=False, status="no_form_found", notes=notes)

    logger.debug("Login form located, attempting authentication")
    success, attempt_error = attempt_login_with_retries(
        page,
        email=email,
        secret=secret,
        logger=logger,
        max_attempts=max_attempts,
    )
    if not success:
        if attempt_error:
            notes.append(attempt_error)
        else:
            notes.append("Login attempts did not transition away from the login page.")
        logger.warning("Login failed after retries")
        return LoginResult(success=False, status="login_failed", notes=notes)

    logger.debug("Login succeeded")
    return LoginResult(success=True, status="complete", notes=notes)
