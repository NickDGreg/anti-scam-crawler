"""Evaluate outcome of a submitted registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Page

ERROR_KEYWORDS = (
    "error",
    "invalid",
    "required",
    "not filled",
    "failed",
    "incorrect",
    "already",
    "must be",
)
SUCCESS_KEYWORDS = (
    "welcome",
    "dashboard",
    "my account",
    "account",
    "client area",
    "thank you",
    "success",
)
SUCCESS_URL_KEYWORDS = (
    "dashboard",
    "client",
    "account",
    "cabinet",
    "user",
)
ERROR_SELECTORS = (
    ".alert",
    ".alert-danger",
    ".validation-summary-errors",
    ".text-danger",
    "[role='alert']",
)
SUCCESS_SELECTORS = (
    ".alert-success",
    ".notification-success",
)


@dataclass(slots=True)
class SubmissionOutcome:
    status: str
    validation_message: Optional[str] = None
    success_message: Optional[str] = None


def evaluate_registration_result(
    page: Page,
    *,
    previous_url: str,
    logger: Optional[logging.Logger] = None,
) -> SubmissionOutcome:
    log = logger or logging.getLogger(__name__)
    error_message = _detect_keyword_message(
        page, ERROR_SELECTORS, ERROR_KEYWORDS, include_body=True
    )
    if error_message:
        log.info("Detected validation failure: %s", error_message)
        return SubmissionOutcome(
            status="validation_failed", validation_message=error_message
        )

    success_message = _detect_success(page, previous_url)
    if success_message:
        log.info("Detected registration success: %s", success_message)
        return SubmissionOutcome(status="registered", success_message=success_message)

    log.info("Submission completed without clear success/failure signals")
    return SubmissionOutcome(status="submitted_no_signal")


def _detect_keyword_message(
    page: Page,
    selectors: tuple[str, ...],
    keywords: tuple[str, ...],
    *,
    include_body: bool,
) -> Optional[str]:
    texts = _collect_text_candidates(page, selectors, include_body=include_body)
    for text in texts:
        lowered = text.lower()
        for keyword in keywords:
            if keyword in lowered:
                return _clean_snippet(text)
    return None


def _detect_success(page: Page, previous_url: str) -> Optional[str]:
    current_url = page.url
    if current_url != previous_url:
        lowered = current_url.lower()
        for keyword in SUCCESS_URL_KEYWORDS:
            if keyword in lowered:
                return f"URL contains '{keyword}'"
    include_body = not _page_has_forms(page)
    texts = _collect_text_candidates(page, SUCCESS_SELECTORS, include_body=include_body)
    for text in texts:
        lowered = text.lower()
        for keyword in SUCCESS_KEYWORDS:
            if keyword in lowered:
                return _clean_snippet(text)
    return None


def _collect_text_candidates(
    page: Page, selectors: tuple[str, ...], *, include_body: bool
) -> List[str]:
    texts: List[str] = []
    for selector in selectors:
        locator = page.locator(selector)
        try:
            entries = locator.all_inner_texts()
        except Exception:  # noqa: BLE001
            entries = []
        for entry in entries:
            entry = entry.strip()
            if entry:
                texts.append(entry)
    if include_body:
        body_text = _safe_inner_text(page, "body")
        if body_text:
            texts.append(body_text)
    return texts


def _page_has_forms(page: Page) -> bool:
    try:
        return page.query_selector("form") is not None
    except Exception:  # noqa: BLE001
        return False


def _safe_inner_text(page: Page, selector: str) -> Optional[str]:
    try:
        text = page.inner_text(selector, timeout=2000)
        return text.strip()
    except Exception:  # noqa: BLE001
        return None


def _clean_snippet(text: str, limit: int = 280) -> str:
    snippet = text.strip()
    if len(snippet) <= limit:
        return snippet
    return f"{snippet[:limit]}…"


__all__ = ["SubmissionOutcome", "evaluate_registration_result"]
