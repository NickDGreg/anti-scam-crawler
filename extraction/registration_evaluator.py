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
    error_message = _detect_keyword_message(page, ERROR_SELECTORS, ERROR_KEYWORDS)
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
    page: Page, selectors: tuple[str, ...], keywords: tuple[str, ...]
) -> Optional[str]:
    texts = _collect_text_candidates(page, selectors)
    for text in texts:
        lowered = text.lower()
        for keyword in keywords:
            if keyword in lowered:
                return text.strip()
    return None


def _detect_success(page: Page, previous_url: str) -> Optional[str]:
    current_url = page.url
    if current_url != previous_url:
        lowered = current_url.lower()
        for keyword in SUCCESS_URL_KEYWORDS:
            if keyword in lowered:
                return f"URL contains '{keyword}'"
    texts = _collect_text_candidates(page, SUCCESS_SELECTORS)
    if not texts:
        body_text = _safe_inner_text(page, "body")
        if body_text:
            texts.append(body_text)
    for text in texts:
        lowered = text.lower()
        for keyword in SUCCESS_KEYWORDS:
            if keyword in lowered:
                return text.strip()
    return None


def _collect_text_candidates(page: Page, selectors: tuple[str, ...]) -> List[str]:
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
    # Always include a trimmed sample from the body for error scans
    body_text = _safe_inner_text(page, "body")
    if body_text:
        texts.append(body_text)
    return texts


def _safe_inner_text(page: Page, selector: str) -> Optional[str]:
    try:
        text = page.inner_text(selector, timeout=2000)
        return text.strip()
    except Exception:  # noqa: BLE001
        return None


__all__ = ["SubmissionOutcome", "evaluate_registration_result"]
