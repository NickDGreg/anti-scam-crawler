"""Shared navigation heuristics for finding auth forms."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple, TypeVar
from urllib.parse import urljoin, urlparse, urlunparse

from playwright.sync_api import ElementHandle, Page
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .automation import click_keywords

FormT = TypeVar("FormT")
MetaT = TypeVar("MetaT")


@dataclass(slots=True)
class NavigationConfig:
    primary_keywords: Sequence[str]
    secondary_keywords: Sequence[str] = ()
    avoid_keywords: Sequence[str] = (
        "contact",
        "support",
        "help",
        "learn more",
        "faq",
        "about",
        "pricing",
        "blog",
    )
    max_depth: int = 2
    max_candidates: int = 8
    max_visits: int = 8
    fallback_keywords: Sequence[str] = ()
    fallback_clicks: int = 3


@dataclass(slots=True)
class NavCandidate:
    element: ElementHandle
    href: Optional[str]
    score: float
    label: str

    def resolved_url(self, base_url: str) -> Optional[str]:
        if not self.href:
            return None
        return urljoin(base_url, self.href)

    def safe_click(self, logger: logging.Logger) -> bool:
        try:
            self.element.click()
            return True
        except PlaywrightError as exc:  # noqa: PERF203
            logger.debug("Failed to click candidate '%s': %s", self.label, exc)
            return False


def discover_form_with_navigation(
    page: Page,
    *,
    detect_form: Callable[[Page], Tuple[Optional[FormT], MetaT]],
    is_valid_form: Callable[[FormT, MetaT], bool],
    config: NavigationConfig,
    logger: logging.Logger,
) -> Tuple[Optional[FormT], MetaT]:
    """Run bounded navigation until a valid form is found."""
    form, meta = detect_form(page)
    if form and is_valid_form(form, meta):
        return form, meta

    visited: set[str] = set()
    queue: deque[Tuple[str, int]] = deque([(page.url, 0)])

    while queue and len(visited) < config.max_visits:
        target_url, depth = queue.popleft()
        normalized = _normalize_url(target_url)
        if normalized in visited:
            continue
        visited.add(normalized)

        if page.url != target_url:
            try:
                page.goto(target_url, wait_until="networkidle", timeout=8000)
            except PlaywrightTimeoutError as exc:
                logger.debug("Navigation to %s timed out: %s", target_url, exc)
                continue
            except PlaywrightError as exc:  # noqa: PERF203
                logger.debug("Navigation to %s failed: %s", target_url, exc)
                continue

        form, meta = detect_form(page)
        if form and is_valid_form(form, meta):
            return form, meta

        candidates = _collect_nav_candidates(page, config, logger=logger)
        inline_candidates = [cand for cand in candidates if not cand.href][
            : config.max_candidates
        ]
        link_candidates = [cand for cand in candidates if cand.href][
            : config.max_candidates
        ]

        for candidate in inline_candidates:
            logger.debug(
                "Clicking inline candidate '%s' (score=%.1f)",
                candidate.label,
                candidate.score,
            )
            if not candidate.safe_click(logger):
                continue
            page.wait_for_timeout(800)
            form, meta = detect_form(page)
            if form and is_valid_form(form, meta):
                return form, meta
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=6000)
            except PlaywrightTimeoutError as exc:
                logger.debug(
                    "Reset to %s timed out after inline click: %s", target_url, exc
                )
            except PlaywrightError as exc:  # noqa: PERF203
                logger.debug(
                    "Reset to %s failed after inline click: %s", target_url, exc
                )
                break

        if depth >= config.max_depth:
            continue

        for candidate in link_candidates:
            resolved = candidate.resolved_url(target_url)
            if not resolved:
                continue
            normalized_target = _normalize_url(resolved)
            if normalized_target in visited:
                continue
            if len(visited) + len(queue) >= config.max_visits:
                break
            logger.debug(
                "Queueing navigation candidate '%s' -> %s (score=%.1f)",
                candidate.label,
                resolved,
                candidate.score,
            )
            queue.append((resolved, depth + 1))

    if config.fallback_keywords:
        logger.debug("Navigation search exhausted; falling back to keyword clicks")
        click_keywords(
            page,
            config.fallback_keywords,
            max_clicks=config.fallback_clicks,
            logger=logger,
        )
        return detect_form(page)

    return detect_form(page)


def _collect_nav_candidates(
    page: Page, config: NavigationConfig, *, logger: logging.Logger
) -> List[NavCandidate]:
    elements = page.query_selector_all("a, button, input[type=submit], [role='button']")
    candidates: List[NavCandidate] = []
    for element in elements:
        try:
            text = (element.inner_text() or "").strip()
        except PlaywrightError:
            text = ""
        href = (element.get_attribute("href") or "").strip() or None
        classes = (element.get_attribute("class") or "").strip()
        aria_label = (element.get_attribute("aria-label") or "").strip()
        score = _score_navigation_target(
            text=text,
            href=href,
            classes=classes,
            config=config,
        )
        if score <= 0:
            continue
        label = text or aria_label or href or "candidate"
        candidates.append(
            NavCandidate(element=element, href=href, score=score, label=label)
        )
    candidates.sort(key=lambda cand: cand.score, reverse=True)
    logger.debug("Found %d navigation candidates", len(candidates))
    return candidates[: config.max_candidates * 2]


def _score_navigation_target(
    *,
    text: str,
    href: Optional[str],
    classes: str,
    config: NavigationConfig,
) -> float:
    text_l = text.lower()
    href_l = (href or "").lower()
    classes_l = classes.lower()

    score = 0.0
    for keyword in config.primary_keywords:
        keyword_l = keyword.lower()
        if keyword_l in text_l:
            score += 8
        if keyword_l and keyword_l in href_l:
            score += 6
        if keyword_l and keyword_l in classes_l:
            score += 3
    for keyword in config.secondary_keywords:
        keyword_l = keyword.lower()
        if keyword_l in text_l:
            score += 4
        if keyword_l and keyword_l in href_l:
            score += 3
        if keyword_l and keyword_l in classes_l:
            score += 1.5
    for keyword in config.avoid_keywords:
        keyword_l = keyword.lower()
        if keyword_l in text_l or keyword_l in href_l:
            score -= 5
    if not href:
        score += 1
    return score


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, "")
    )
