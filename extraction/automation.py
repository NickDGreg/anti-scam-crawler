"""Shared browser automation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from playwright.sync_api import ElementHandle, Page, TimeoutError as PlaywrightTimeoutError

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
SECRET_SELECTORS = PASSWORD_SELECTORS + ["input[name*='code' i]", "input[placeholder*='code' i]"]
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


def _query_first(target: SelectorTarget, selectors: Iterable[str]) -> Optional[ElementHandle]:
    for selector in selectors:
        element = target.query_selector(selector)
        if element:
            return element
    return None


def find_form(target: SelectorTarget, field_map: Dict[str, Iterable[str]]) -> Optional[FormDefinition]:
    forms = target.query_selector_all("form")
    for form in forms:
        handles: Dict[str, ElementHandle] = {}
        for field_name, selectors in field_map.items():
            element = _query_first(form, selectors)
            if not element:
                break
            handles[field_name] = element
        else:
            submit = _query_first(form, SUBMIT_SELECTORS)
            return FormDefinition(form=form, fields=handles, submit=submit)
    return None


def fill_form_fields(form_def: FormDefinition, values: Dict[str, str]) -> None:
    for field_name, value in values.items():
        field = form_def.fields.get(field_name)
        if not field:
            continue
        field.click()
        field.fill(value)


def submit_form(form_def: FormDefinition) -> None:
    if form_def.submit:
        form_def.submit.click()
        return
    # Fallback: press Enter on the first field
    first_field = next(iter(form_def.fields.values()), None)
    if first_field:
        first_field.press("Enter")


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


def click_keywords(page: Page, keywords: Iterable[str], *, max_clicks: int = 3) -> int:
    clicks = 0
    for keyword in keywords:
        if clicks >= max_clicks:
            break
        if click_by_text(page, keyword):
            clicks += 1
            page.wait_for_timeout(1200)
    return clicks


def click_by_text(page: Page, text: str) -> bool:
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    for role in ("button", "link"):
        locator = page.get_by_role(role, name=pattern)
        if locator.count() > 0:
            locator.first.click()
            return True
    locator = page.get_by_text(pattern)
    if locator.count() > 0:
        locator.first.click()
        return True
    return False


def detect_error_banner(page: Page) -> Optional[str]:
    error_keywords = ["error", "invalid", "failed", "incorrect"]
    for keyword in error_keywords:
        locator = page.get_by_text(re.compile(keyword, re.IGNORECASE))
        try:
            if locator.count() > 0:
                text = locator.first.inner_text(timeout=500)
                if text:
                    return text.strip()
        except PlaywrightTimeoutError:
            continue
    return None
