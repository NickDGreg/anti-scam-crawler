"""Legacy deep-dive strategist for deposit discovery."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from playwright.sync_api import ElementHandle
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .automation import click_by_text
from .browser import BrowserConfig, BrowserSession
from .io_utils import RunPaths, relative_artifact_path, sanitize_filename, save_text
from .login_flow import (
    perform_login,
)
from .parsing import (
    BTC_PATTERN,
    ETH_PATTERN,
    TRON_PATTERN,
    Indicator,
    extract_indicators,
)

MODULE_LOGGER = logging.getLogger(__name__)

FUNDING_KEYWORDS = (
    "deposit",
    "wallet",
    "cashier",
    "fund",
    "add funds",
    "top up",
    "payment",
    "bank transfer",
    "finance",
    "transfer",
)
MENU_KEYWORDS = ("menu", "sidebar", "navigation", "more")
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
DEPOSIT_CONTEXT_HINTS = (
    "deposit",
    "wallet",
    "cashier",
    "fund",
    "add funds",
    "payment",
    "bank transfer",
    "finance",
    "top up",
)
CRYPTO_INDICATOR_TYPES = {"BTC", "ETH", "TRON"}
ACTION_TEXT_HINTS = (
    "deposit",
    "choose",
    "select",
    "continue",
    "confirm",
    "proceed",
    "copy",
    "start",
    "open",
    "fund",
    "wallet",
)
ICON_CLASS_HINTS = (
    "copy",
    "deposit",
    "wallet",
    "select",
    "choose",
    "pay",
    "plus",
)
COPY_BUTTON_KEYWORDS = ("copy", "clipboard")
POTENTIAL_CONTAINER_SELECTORS = (
    "[class*='method' i]",
    "[class*='option' i]",
    "[class*='item' i]",
    "[class*='payment' i]",
    "[class*='card' i]",
    "[class*='tile' i]",
    "li",
    "tr",
    "section",
    "article",
    "div",
)
MODAL_WAIT_SELECTOR = (
    ".modal.show, [role='dialog'], .modal[style*='display: block'], .ant-modal, "
    ".chakra-modal__content, .MuiDialog-root, .v-modal, .ant-drawer-open"
)


@dataclass(slots=True)
class ProbeInputs:
    url: str
    email: str
    secret: str
    run_paths: RunPaths
    logger: logging.Logger
    max_steps: int = 5


@dataclass(slots=True)
class ProbeResult:
    run_id: str
    input_url: str
    final_url: str
    status: str
    notes: str
    indicators: List[Indicator]
    artifacts: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "input_url": self.input_url,
            "final_url": self.final_url,
            "status": self.status,
            "notes": self.notes,
            "indicators": [asdict(indicator) for indicator in self.indicators],
            "artifacts": self.artifacts,
        }


def run_targeted_probe(inputs: ProbeInputs) -> ProbeResult:
    logger = inputs.logger
    run_paths = inputs.run_paths
    artifacts: List[str] = []
    notes: List[str] = []
    status = "error"
    final_url = inputs.url
    indicator_records: List[Indicator] = []

    try:
        logger.debug("Starting targeted probe for %s as %s", inputs.url, inputs.email)
        with BrowserSession(BrowserConfig()) as browser:
            page = browser.goto(inputs.url, wait_until="networkidle")
            final_url = page.url
            logger.debug("Loaded entry page %s", final_url)
            landing_shot = browser.screenshot(run_paths.build_path("00_landing.png"))
            artifacts.append(relative_artifact_path(landing_shot))

            logger.debug("Attempting authentication for targeted probe")
            login_result = perform_login(
                page,
                email=inputs.email,
                secret=inputs.secret,
                logger=logger,
            )
            final_url = page.url

            if not login_result.success:
                status = login_result.status
                notes.extend(login_result.notes)
            else:
                status = "complete"
                logger.debug("Login succeeded; starting exploration")
                scan_artifacts, scan_indicators = scan_current_view(
                    browser, run_paths, "01_post_login", logger
                )
                artifacts.extend(scan_artifacts)
                indicator_records.extend(scan_indicators)
                logger.debug("Login succeeded; starting exploration")
                more_artifacts, more_indicators = explore_interesting_pages(
                    browser, inputs.max_steps, run_paths, logger
                )
                artifacts.extend(more_artifacts)
                indicator_records.extend(more_indicators)

    except Exception as exc:  # noqa: BLE001
        logger.exception("Probe command failed: %s", exc)
        notes.append(str(exc))
        status = "error"

    notes_text = " | ".join(notes) if notes else ""
    return ProbeResult(
        run_id=run_paths.run_id,
        input_url=inputs.url,
        final_url=final_url,
        status=status,
        notes=notes_text,
        indicators=indicator_records,
        artifacts=artifacts,
    )


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

    def process_current_view(label: str) -> None:
        scan_artifacts, scan_indicators = scan_current_view(
            browser, run_paths, label, logger
        )
        artifacts.extend(scan_artifacts)
        indicators.extend(scan_indicators)

    def run_keywords(keywords: Tuple[str, ...], prefix: str) -> None:
        nonlocal steps
        for keyword in keywords:
            if steps >= max_steps:
                break
            logger.debug("Exploration step %d: looking for '%s'", steps + 1, keyword)
            try:
                clicked = click_by_text(page, keyword, logger=logger)
            except PlaywrightError as exc:
                logger.warning(
                    "Keyword click failed for '%s'; skipping interaction: %s",
                    keyword,
                    exc,
                )
                continue
            if not clicked:
                continue
            steps += 1
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except PlaywrightTimeoutError:
                logger.debug(
                    "Navigation after clicking '%s' did not complete in time", keyword
                )
            label = f"{prefix}_{steps:02d}_{sanitize_filename(keyword)}"
            process_current_view(label)

    run_keywords(FUNDING_KEYWORDS, "step")

    if steps < max_steps and not is_deposit_context(page):
        logger.debug(
            "Deposit context not detected after primary pass; attempting menu fallback"
        )
        if click_menu(page, logger=logger):
            page.wait_for_timeout(800)
            run_keywords(FUNDING_KEYWORDS, "step")

    if indicators:
        logger.info("Detected %d indicators during exploration", len(indicators))
    else:
        logger.info("No deposit indicators detected during exploration")
    return artifacts, indicators


def _looks_like_crypto(candidate: str) -> bool:
    if not candidate:
        return False
    if BTC_PATTERN.search(candidate):
        return True
    if ETH_PATTERN.search(candidate):
        return True
    return bool(TRON_PATTERN.search(candidate))


def _collect_input_values(page, logger: logging.Logger) -> List[str]:
    try:
        elements = page.query_selector_all("input, textarea")
    except PlaywrightError as exc:
        logger.debug("Unable to enumerate input controls: %s", exc)
        return []
    values: List[str] = []
    for element in elements:
        try:
            raw = element.input_value()
        except PlaywrightError:
            continue
        value = raw.strip()
        if value:
            values.append(value)
    return values


def _collect_copy_neighbor_text(page, logger: logging.Logger) -> List[str]:
    selectors = "button, a, [role='button'], [class*='copy' i], [class*='clipboard' i]"
    try:
        elements = page.query_selector_all(selectors)
    except PlaywrightError as exc:
        logger.debug("Unable to scan copy controls: %s", exc)
        return []
    snippets: List[str] = []
    for element in elements:
        try:
            text = (element.inner_text() or "").strip().lower()
        except PlaywrightError:
            text = ""
        matched = any(keyword in text for keyword in COPY_BUTTON_KEYWORDS)
        if not matched:
            try:
                attr_text = (element.get_attribute("class") or "").lower()
            except PlaywrightError:
                attr_text = ""
            matched = any(keyword in attr_text for keyword in COPY_BUTTON_KEYWORDS)
        if not matched:
            try:
                aria_label = (element.get_attribute("aria-label") or "").lower()
            except PlaywrightError:
                aria_label = ""
            matched = any(keyword in aria_label for keyword in COPY_BUTTON_KEYWORDS)
        if not matched:
            continue
        try:
            neighbors = (
                element.evaluate(
                    """
                (el) => {
                    const values = [];
                    const pushNode = (node) => {
                        if (!node) {
                            return;
                        }
                        if (typeof node.value === 'string' && node.value.trim()) {
                            values.push(node.value.trim());
                        }
                        if (node.getAttribute) {
                            const attrValue = node.getAttribute('value');
                            if (attrValue && attrValue.trim()) {
                                values.push(attrValue.trim());
                            }
                        }
                        const text = (node.innerText || node.textContent || '').trim();
                        if (text) {
                            values.push(text);
                        }
                    };
                    pushNode(el.previousElementSibling);
                    pushNode(el.nextElementSibling);
                    const parent = el.parentElement;
                    if (parent && parent.children.length <= 5) {
                        for (const child of parent.children) {
                            if (child === el) {
                                continue;
                            }
                            pushNode(child);
                        }
                    }
                    return values;
                }
                """
                )
                or []
            )
        except PlaywrightError:
            continue
        for neighbor in neighbors:
            snippet = str(neighbor).strip()
            if snippet:
                snippets.append(snippet)
    return snippets


def _collect_hidden_value_strings(
    page, logger: logging.Logger
) -> List[Tuple[str, str]]:
    extras: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for value in _collect_input_values(page, logger):
        if value in seen or not _looks_like_crypto(value):
            continue
        extras.append(("input_value", value))
        seen.add(value)
    for snippet in _collect_copy_neighbor_text(page, logger):
        if snippet in seen or not _looks_like_crypto(snippet):
            continue
        extras.append(("copy_neighbor", snippet))
        seen.add(snippet)
    return extras


def _snapshot_dom(
    page,
    logger: logging.Logger | None,
) -> Tuple[str, List[Tuple[str, str]]]:
    log = logger or MODULE_LOGGER
    try:
        html = page.content()
    except PlaywrightError as exc:
        log.debug("Failed to read page.content(): %s", exc)
        html = ""
    extra_strings = _collect_hidden_value_strings(page, log)
    return html, extra_strings


def _scan_crypto_fingerprint(page, logger: logging.Logger) -> Set[Tuple[str, str]]:
    html, extra_strings = _snapshot_dom(page, logger)
    indicators = extract_indicators(html, page.url, extra_strings=extra_strings)
    fingerprint: Set[Tuple[str, str]] = set()
    for indicator in indicators:
        if indicator.type in CRYPTO_INDICATOR_TYPES:
            fingerprint.add((indicator.type, indicator.value))
    return fingerprint


def _tag_indicators(
    html: str,
    url: str,
    html_path: Path,
    *,
    extra_strings: Iterable[Tuple[str, str]] | None = None,
) -> List[Indicator]:
    tagged: List[Indicator] = []
    for indicator in extract_indicators(html, url, extra_strings=extra_strings):
        indicator.artifact = relative_artifact_path(html_path)
        tagged.append(indicator)
    return tagged


def capture_page_state(
    browser: BrowserSession,
    run_paths: RunPaths,
    label: str,
    logger: logging.Logger | None = None,
) -> Tuple[List[str], List[Indicator]]:
    page = browser.page
    log = logger or MODULE_LOGGER
    log.debug("Capturing page state '%s' at URL %s", label, page.url)
    html, extra_strings = _snapshot_dom(page, log)
    html_path = save_text(run_paths.build_path(f"{label}.html"), html)
    screenshot_path = browser.screenshot(run_paths.build_path(f"{label}.png"))
    artifacts = [
        relative_artifact_path(html_path),
        relative_artifact_path(screenshot_path),
    ]
    indicators = _tag_indicators(
        html,
        page.url,
        html_path,
        extra_strings=extra_strings,
    )
    if indicators:
        log.info(
            "Indicator scan for '%s' produced %d matches: %s",
            label,
            len(indicators),
            [(indicator.type, indicator.value) for indicator in indicators],
        )
    else:
        log.debug("Indicator scan for '%s' produced no matches", label)
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
        view_artifacts, view_indicators = capture_page_state(
            browser, run_paths, label, logger
        )
        artifacts.extend(view_artifacts)
        indicators.extend(view_indicators)
    return artifacts, indicators


def _format_has_text_selector(base: str, text: str) -> str:
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'{base}:has-text("{safe}")'


def _locate_container_from_handle(
    handle: ElementHandle, logger: logging.Logger
) -> ElementHandle:
    try:
        container = handle.evaluate_handle(
            """
            (el, selectors) => {
                for (const selector of selectors) {
                    if (typeof el.closest === 'function') {
                        const match = el.closest(selector);
                        if (match) {
                            return match;
                        }
                    }
                }
                let current = el.parentElement;
                let depth = 0;
                while (current && depth < 5) {
                    if (current.matches && current.matches('div, li, section, article, tr')) {
                        return current;
                    }
                    current = current.parentElement;
                    depth += 1;
                }
                return el;
            }
            """,
            list(POTENTIAL_CONTAINER_SELECTORS),
        )
    except PlaywrightError as exc:
        logger.debug("Container detection failed: %s", exc)
        container = None
    return container or handle


def _find_keyword_container(
    page,
    keyword: str,
    occurrence: int,
    logger: logging.Logger,
) -> Optional[ElementHandle]:
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    locator = page.get_by_text(pattern)
    try:
        matches = locator.count()
    except PlaywrightError as exc:
        logger.debug("Unable to count locator matches for '%s': %s", keyword, exc)
        return None
    if matches == 0 or occurrence >= matches:
        return None
    try:
        element = locator.nth(occurrence).element_handle()
    except PlaywrightError as exc:
        logger.debug("Failed to fetch element handle for '%s': %s", keyword, exc)
        return None
    if not element:
        return None
    return _locate_container_from_handle(element, logger)


def _resolve_action_target(
    container: ElementHandle,
    keyword: str,
    logger: logging.Logger,
) -> ElementHandle:
    text_hints = (keyword,) + ACTION_TEXT_HINTS
    bases = ("button", "[role='button']", "a")
    for text in text_hints:
        for base in bases:
            selector = _format_has_text_selector(base, text)
            try:
                target = container.query_selector(selector)
            except PlaywrightError:
                target = None
            if target:
                return target
    for hint in ICON_CLASS_HINTS:
        selector = f"[class*='{hint}' i]"
        try:
            target = container.query_selector(selector)
        except PlaywrightError:
            target = None
        if target:
            return target
    for selector in (
        "button",
        "[role='button']",
        "a",
        "input[type='button']",
        "input[type='submit']",
    ):
        try:
            target = container.query_selector(selector)
        except PlaywrightError:
            target = None
        if target:
            return target
    return container


def _safe_click_handle(handle: ElementHandle, logger: logging.Logger) -> bool:
    try:
        handle.scroll_into_view_if_needed(timeout=1000)
    except PlaywrightError:
        pass
    try:
        handle.click(timeout=4000)
        return True
    except PlaywrightError as exc:
        logger.debug("Direct click failed: %s", exc)
    try:
        handle.evaluate("el => el.click && el.click()")
        return True
    except PlaywrightError as exc:
        logger.debug("Scripted click failed: %s", exc)
    return False


def _wait_for_modal_state(page, logger: logging.Logger) -> None:
    try:
        page.wait_for_selector(MODAL_WAIT_SELECTOR, timeout=2000)
    except PlaywrightTimeoutError:
        logger.debug("Modal selector did not appear before timeout; continuing")
        page.wait_for_timeout(800)


def _dismiss_modal(page, logger: logging.Logger) -> None:
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except PlaywrightError:
        pass
    close_selectors = (
        "button:has-text('Close')",
        "button:has-text('Cancel')",
        "button[aria-label='Close']",
        ".modal button.close",
        ".ant-modal-close",
    )
    for selector in close_selectors:
        try:
            handle = page.query_selector(selector)
        except PlaywrightError:
            handle = None
        if handle and _safe_click_handle(handle, logger):
            page.wait_for_timeout(200)
            break


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
    seen_crypto = _scan_crypto_fingerprint(page, logger)
    for keyword in DEPOSIT_METHOD_KEYWORDS:
        if clicks >= max_clicks:
            break
        logger.debug("Searching for deposit method keyword '%s'", keyword)
        for occurrence in range(2):
            if clicks >= max_clicks:
                break
            container = _find_keyword_container(page, keyword, occurrence, logger)
            if not container:
                if occurrence == 0:
                    logger.debug("Keyword '%s' not found on current view", keyword)
                break
            action_target = _resolve_action_target(container, keyword, logger)
            if not _safe_click_handle(action_target, logger):
                logger.debug(
                    "Unable to click action target for keyword '%s' (occurrence %d)",
                    keyword,
                    occurrence + 1,
                )
                continue
            clicks += 1
            logger.debug(
                "Triggered deposit option for '%s' (interaction %d)", keyword, clicks
            )
            _wait_for_modal_state(page, logger)
            post_crypto = _scan_crypto_fingerprint(page, logger)
            new_crypto = post_crypto - seen_crypto
            if new_crypto:
                label = f"{base_label}_method_{clicks:02d}_{sanitize_filename(keyword)}"
                view_artifacts, view_indicators = capture_page_state(
                    browser, run_paths, label, logger
                )
                artifacts.extend(view_artifacts)
                indicators.extend(view_indicators)
                logger.info(
                    "Active deposit scan surfaced %d new crypto addresses for '%s'",
                    len(new_crypto),
                    keyword,
                )
            else:
                logger.debug(
                    "No new crypto indicators detected after '%s' interaction",
                    keyword,
                )
            seen_crypto |= post_crypto
            _dismiss_modal(page, logger)
            page.wait_for_timeout(400)
            break
    return artifacts, indicators


def scan_current_view(
    browser: BrowserSession, run_paths: RunPaths, label: str, logger: logging.Logger
) -> Tuple[List[str], List[Indicator]]:
    artifacts, indicators = capture_page_state(browser, run_paths, label, logger)
    try:
        reveal_artifacts, reveal_indicators = reveal_hidden_sections(
            browser, run_paths, label, logger
        )
        artifacts.extend(reveal_artifacts)
        indicators.extend(reveal_indicators)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Hidden-section reveal failed for '%s'; continuing without it: %s",
            label,
            exc,
        )
    try:
        if is_deposit_context(browser.page):
            method_artifacts, method_indicators = click_deposit_methods(
                browser, run_paths, label, logger
            )
            artifacts.extend(method_artifacts)
            indicators.extend(method_indicators)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Deposit-method exploration failed for '%s'; continuing without it: %s",
            label,
            exc,
        )
    return artifacts, indicators


def click_menu(page, *, logger: logging.Logger) -> bool:
    for keyword in MENU_KEYWORDS:
        logger.debug("Attempting to open navigation via keyword '%s'", keyword)
        if click_by_text(page, keyword, logger=logger):
            return True
    logger.debug("Navigation keywords did not open a menu")
    return False


def is_deposit_context(page) -> bool:
    url_lower = page.url.lower()
    if any(hint in url_lower for hint in DEPOSIT_CONTEXT_HINTS):
        return True
    try:
        headings = page.locator("h1, h2, .page-title, [role='heading']")
        count = min(3, headings.count())
        for idx in range(count):
            try:
                text = headings.nth(idx).inner_text(timeout=500).strip().lower()
            except PlaywrightError:
                continue
            if any(hint in text for hint in DEPOSIT_CONTEXT_HINTS):
                return True
    except PlaywrightError:
        pass
    try:
        body_snippet = page.inner_text("body", timeout=800).lower()
        if any(hint in body_snippet for hint in DEPOSIT_CONTEXT_HINTS):
            return True
    except PlaywrightError:
        return False
    return False
