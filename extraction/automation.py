"""Shared browser automation helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from playwright.sync_api import ElementHandle, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

EMAIL_SELECTORS = [
    "input[type='email']",
    "input[name*='email' i]",
    "input[placeholder*='email' i]",
]
PASSWORD_SELECTORS = [
    "input[type='password']",
    "input[name*='pass' i]",
    "input[placeholder*='password' i]",
]
SECRET_SELECTORS = PASSWORD_SELECTORS + [
    "input[name*='code' i]",
    "input[placeholder*='code' i]",
]
SUBMIT_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Register')",
    "button:has-text('Sign Up')",
    "button:has-text('Log In')",
    "button:has-text('Login')",
]


@dataclass(slots=True)
class FormDefinition:
    form: ElementHandle
    fields: Dict[str, ElementHandle]
    submit: Optional[ElementHandle]


SelectorTarget = Page | ElementHandle
LOGGER = logging.getLogger(__name__)


def _resolve_logger(logger: Optional[logging.Logger]) -> logging.Logger:
    return logger or LOGGER


def _query_first(
    target: SelectorTarget, selectors: Iterable[str]
) -> Optional[ElementHandle]:
    for selector in selectors:
        element = target.query_selector(selector)
        if element:
            return element
    return None


def find_form(
    target: SelectorTarget,
    field_map: Dict[str, Iterable[str]],
    *,
    logger: Optional[logging.Logger] = None,
) -> Optional[FormDefinition]:
    forms = target.query_selector_all("form")
    log = _resolve_logger(logger)
    log.debug("Scanning %d forms for fields: %s", len(forms), list(field_map.keys()))
    for form in forms:
        handles: Dict[str, ElementHandle] = {}
        for field_name, selectors in field_map.items():
            element = _query_first(form, selectors)
            if not element:
                break
            handles[field_name] = element
        else:
            submit = _query_first(form, SUBMIT_SELECTORS)
            log.debug("Form matched with fields: %s", list(handles.keys()))
            return FormDefinition(form=form, fields=handles, submit=submit)
    log.debug("No matching form found")
    return None


def fill_form_fields(
    form_def: FormDefinition,
    values: Dict[str, str],
    *,
    logger: Optional[logging.Logger] = None,
) -> None:
    log = _resolve_logger(logger)
    for field_name, value in values.items():
        field = form_def.fields.get(field_name)
        if not field:
            log.debug("Field '%s' missing on form; skipping", field_name)
            continue
        field.click()
        field.fill(value)
        log.debug("Filled field '%s'", field_name)


def submit_form(
    form_def: FormDefinition, *, logger: Optional[logging.Logger] = None
) -> None:
    log = _resolve_logger(logger)
    if form_def.submit:
        form_def.submit.click()
        log.debug("Clicked explicit submit control")
        return
    # Fallback: press Enter on the first field
    first_field = next(iter(form_def.fields.values()), None)
    if first_field:
        first_field.press("Enter")
        log.debug("Submit fallback via Enter key")
    else:
        log.debug("No submit control or fallback field available")


KEYWORD_CLICKS = (
    "register",
    "sign up",
    "create account",
    "get started",
    "open account",
    "menu",
    "login",
    "log in",
)


def click_keywords(
    page: Page,
    keywords: Iterable[str],
    *,
    max_clicks: int = 3,
    logger: Optional[logging.Logger] = None,
) -> int:
    log = _resolve_logger(logger)
    clicks = 0
    for keyword in keywords:
        if clicks >= max_clicks:
            break
        log.debug("Attempting to click keyword '%s'", keyword)
        if click_by_text(page, keyword, logger=log):
            clicks += 1
            page.wait_for_timeout(1200)
            log.debug("Clicked keyword '%s'", keyword)
        else:
            log.debug("Keyword '%s' not clickable on current page", keyword)
    return clicks


def click_by_text(
    page: Page, text: str, *, logger: Optional[logging.Logger] = None
) -> bool:
    log = _resolve_logger(logger)
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    for role in ("button", "link"):
        locator = page.get_by_role(role, name=pattern)
        if locator.count() > 0:
            locator.first.click()
            log.debug("Clicked %s with text '%s'", role, text)
            return True
    locator = page.get_by_text(pattern)
    if locator.count() > 0:
        locator.first.click()
        log.debug("Clicked element by text '%s'", text)
        return True
    log.debug("No element with text '%s' found to click", text)
    return False


def detect_error_banner(
    page: Page, *, logger: Optional[logging.Logger] = None
) -> Optional[str]:
    log = _resolve_logger(logger)
    error_keywords = ["error", "invalid", "failed", "incorrect"]
    for keyword in error_keywords:
        locator = page.get_by_text(re.compile(keyword, re.IGNORECASE))
        try:
            if locator.count() > 0:
                text = locator.first.inner_text(timeout=500)
                if text:
                    log.debug(
                        "Detected error banner for '%s': %s", keyword, text.strip()
                    )
                    return text.strip()
        except PlaywrightTimeoutError:
            continue
    log.debug("No error banners detected")
    return None
