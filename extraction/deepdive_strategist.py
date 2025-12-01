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

from .archival_crawler import (
    INFRA_DOMAIN_BLOCKLIST,
    _normalize_url,
    _registrable_domain,
    extract_links,
)
from .automation import click_by_text, submit_form_element
from .browser import BrowserConfig, BrowserSession
from .io_utils import RunPaths, relative_artifact_path, sanitize_filename, save_text
from .login_flow import (
    perform_login,
)
from .parsing import Indicator, extract_indicators, has_crypto_match

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
                run_paths=run_paths,
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
    visited_urls: Set[str] = set()

    def _mark_visited(url: str) -> str:
        normalized = _normalize_url(url) or url
        visited_urls.add(normalized)
        return normalized

    def process_current_view(label: str) -> None:
        normalized = _normalize_url(page.url) or page.url
        if normalized in visited_urls:
            logger.debug("Skipping capture for already visited URL %s", normalized)
            return
        _mark_visited(normalized)
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
                page.wait_for_load_state("load", timeout=7000)
            except PlaywrightTimeoutError:
                logger.debug(
                    "Navigation after clicking '%s' did not complete in time", keyword
                )
            label = f"{prefix}_{steps:02d}_{sanitize_filename(keyword)}"
            process_current_view(label)

    _mark_visited(page.url)
    run_keywords(FUNDING_KEYWORDS, "step")

    if steps < max_steps and not is_deposit_context(page):
        logger.debug(
            "Deposit context not detected after primary pass; attempting menu fallback"
        )
        if click_menu(page, logger=logger):
            page.wait_for_timeout(800)
            run_keywords(FUNDING_KEYWORDS, "step")

    def follow_deposit_links(max_links: int) -> int:
        consumed = 0
        home_domain = _registrable_domain(page.url)
        try:
            links = extract_links(
                page,
                page.url,
                home_domain=home_domain,
                allow_external=False,
                logger=logger,
                avoid_auth_links=False,
                infra_blocklist=INFRA_DOMAIN_BLOCKLIST,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("extract_links failed during deep-dive deposit scan: %s", exc)
            return consumed
        deposit_links: List[str] = []
        for link in links:
            normalized = _normalize_url(link) or link
            lowered = normalized.lower()
            if any(hint in lowered for hint in DEPOSIT_CONTEXT_HINTS):
                deposit_links.append(link)
        logger.debug(
            "Identified %d deposit-like links on current page (candidates: %s)",
            len(deposit_links),
            deposit_links,
        )
        for idx, link in enumerate(deposit_links):
            if consumed >= max_links:
                break
            normalized = _normalize_url(link) or link
            if normalized in visited_urls:
                continue
            try:
                page.goto(link, wait_until="load")
            except PlaywrightError as exc:
                logger.debug("Navigation to deposit link failed: %s", exc)
                continue
            consumed += 1
            label = f"link_{consumed:02d}_{sanitize_filename(link)}"
            process_current_view(label)
        return consumed

    if steps < max_steps:
        remaining = max_steps - steps
        steps += follow_deposit_links(remaining)

    if indicators:
        logger.info("Detected %d indicators during exploration", len(indicators))
    else:
        logger.info("No deposit indicators detected during exploration")
    return artifacts, indicators


def _looks_like_crypto(candidate: str) -> bool:
    if not candidate:
        return False
    return has_crypto_match(candidate)


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

    # Prefer non-navigational interactions (buttons, anchors without real hrefs)
    # to avoid leaving the deposit page before deeper exploration.
    def _is_navigation_link(handle: ElementHandle) -> bool:
        try:
            tag = (handle.evaluate("el => el.tagName || ''") or "").lower()
        except PlaywrightError:
            tag = ""
        if tag != "a":
            return False
        try:
            href = (handle.get_attribute("href") or "").strip().lower()
        except PlaywrightError:
            href = ""
        if not href:
            return False
        if href in ("#", "javascript:void(0)", "javascript:void(0);", "javascript:;"):
            return False
        if href.startswith("#"):
            return False
        return True

    def _click_reveal_action(keyword: str) -> bool:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        try:
            button_locator = page.get_by_role("button", name=pattern)
            if button_locator.count() > 0:
                try:
                    button_locator.first.click()
                    logger.debug("Clicked button with text '%s'", keyword)
                    return True
                except PlaywrightError as exc:
                    logger.debug("Button click failed for '%s': %s", keyword, exc)
        except PlaywrightError:
            pass
        try:
            text_locator = page.get_by_text(pattern)
            matches = min(text_locator.count(), 5)
        except PlaywrightError as exc:
            logger.debug("Reveal locator lookup failed for '%s': %s", keyword, exc)
            return False
        for idx in range(matches):
            try:
                handle = text_locator.nth(idx).element_handle()
            except PlaywrightError as exc:
                logger.debug(
                    "Failed to get element handle for '%s' occurrence %d: %s",
                    keyword,
                    idx + 1,
                    exc,
                )
                continue
            if not handle or _is_navigation_link(handle):
                continue
            if _safe_click_handle(handle, logger):
                logger.debug("Clicked reveal element with text '%s'", keyword)
                return True
        return False

    for keyword in REVEAL_KEYWORDS:
        if clicks >= max_clicks:
            break
        clicked = _click_reveal_action(keyword)
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


@dataclass(slots=True)
class PaymentOption:
    value: str
    label: str
    handle: Optional[ElementHandle] = None


def _deposit_form_candidate(form: ElementHandle, logger: logging.Logger) -> bool:
    try:
        method = (form.get_attribute("method") or "").lower()
    except PlaywrightError:
        method = ""
    if method and method != "post":
        return False
    try:
        name_and_id = (
            (form.get_attribute("name") or "") + " " + (form.get_attribute("id") or "")
        ).lower()
    except PlaywrightError:
        name_and_id = ""
    try:
        deposit_input = form.query_selector("input[name='a' i][value*='deposit' i]")
        token_input = form.query_selector(
            "input[name='form_id' i], input[name*='token' i], input[name*='csrf' i]"
        )
    except PlaywrightError:
        deposit_input = None
        token_input = None
    return bool(deposit_input or token_input or re.search("spend|deposit", name_and_id))


def _find_payment_select(
    form: ElementHandle, logger: logging.Logger
) -> Optional[ElementHandle]:
    for selector in (
        "select[name='type' i]",
        "select[name*='method' i]",
        "select[name*='payment' i]",
    ):
        try:
            select = form.query_selector(selector)
        except PlaywrightError as exc:
            logger.debug("Payment select lookup failed for '%s': %s", selector, exc)
            continue
        if select:
            return select
    return None


def _extract_payment_options(
    select: ElementHandle, logger: logging.Logger
) -> List[PaymentOption]:
    try:
        option_handles = select.query_selector_all("option")
    except PlaywrightError as exc:
        logger.debug("Unable to enumerate payment options: %s", exc)
        return []
    options: List[PaymentOption] = []
    seen: Set[Tuple[str, str]] = set()
    for handle in option_handles:
        try:
            value = (handle.get_attribute("value") or "").strip()
        except PlaywrightError:
            value = ""
        try:
            label = (handle.inner_text() or handle.text_content() or "").strip()
        except PlaywrightError:
            label = ""
        label = label or value
        if not (value or label):
            continue
        key = (value.lower(), label.lower())
        if key in seen:
            continue
        seen.add(key)
        options.append(PaymentOption(value=value, label=label))
    return options


def _extract_clickable_payment_options(
    form: ElementHandle, logger: logging.Logger
) -> List[PaymentOption]:
    selectors = (
        "[data-method]",
        "[data-payment]",
        "[data-pay]",
        "[data-gateway]",
        "[data-processor]",
        "[data-coin]",
    )
    handles: List[ElementHandle] = []
    seen_handles: Set[int] = set()

    def _add_handles(candidate_list: List[ElementHandle]) -> None:
        for handle in candidate_list:
            if handle and id(handle) not in seen_handles:
                seen_handles.add(id(handle))
                handles.append(handle)

    for selector in selectors:
        try:
            matches = form.query_selector_all(selector)
        except PlaywrightError as exc:
            logger.debug("Clickable payment lookup failed for '%s': %s", selector, exc)
            matches = []
        _add_handles(matches)
    try:
        toggle_inputs = form.query_selector_all(
            "input[type='radio' i], input[type='checkbox' i]"
        )
    except PlaywrightError as exc:
        logger.debug("Toggle payment option lookup failed: %s", exc)
        toggle_inputs = []
    for toggle in toggle_inputs:
        try:
            name_and_id = (
                (toggle.get_attribute("name") or "")
                + " "
                + (toggle.get_attribute("id") or "")
            ).lower()
            value_attr = (toggle.get_attribute("value") or "").lower()
        except PlaywrightError:
            name_and_id = ""
            value_attr = ""
        if not re.search("method|payment|pay|gateway|channel|crypto|coin", name_and_id):
            if not re.search(
                "method|payment|pay|gateway|channel|crypto|coin", value_attr
            ):
                continue
        if toggle and id(toggle) not in seen_handles:
            seen_handles.add(id(toggle))
            handles.append(toggle)

    options: List[PaymentOption] = []
    seen: Set[Tuple[str, str]] = set()
    for handle in handles:
        try:
            data_value = (
                handle.get_attribute("data-method")
                or handle.get_attribute("data-payment")
                or handle.get_attribute("data-pay")
                or handle.get_attribute("data-gateway")
                or handle.get_attribute("data-processor")
                or handle.get_attribute("data-coin")
                or handle.get_attribute("value")
                or ""
            ).strip()
        except PlaywrightError:
            data_value = ""
        try:
            label_text = (handle.inner_text() or handle.text_content() or "").strip()
        except PlaywrightError:
            label_text = ""
        if not label_text:
            try:
                container = _locate_container_from_handle(handle, logger)
                label_text = (container.inner_text() or "").strip()
            except PlaywrightError:
                label_text = ""
        value = data_value or label_text
        label = label_text or data_value
        if not (value or label):
            continue
        key = (value.lower(), label.lower())
        if key in seen:
            continue
        seen.add(key)
        options.append(PaymentOption(value=value, label=label, handle=handle))
    return options


def _find_deposit_form_with_payments(
    page, logger: logging.Logger
) -> Optional[Tuple[ElementHandle, Optional[ElementHandle], List[PaymentOption]]]:
    try:
        forms = page.query_selector_all("form")
    except PlaywrightError as exc:
        logger.debug("Unable to enumerate forms on deposit page: %s", exc)
        return None
    for form in forms:
        if not _deposit_form_candidate(form, logger):
            continue
        select = _find_payment_select(form, logger)
        if select:
            options = _extract_payment_options(select, logger)
            if options:
                return form, select, options
        clickable_options = _extract_clickable_payment_options(form, logger)
        if clickable_options:
            return form, None, clickable_options
    logger.debug("No deposit form with payment options detected on %s", page.url)
    return None


def _select_payment_option(
    page,
    select: Optional[ElementHandle],
    option: PaymentOption,
    logger: logging.Logger,
) -> bool:
    if option.handle:
        if _safe_click_handle(option.handle, logger):
            try:
                page.wait_for_timeout(300)
            except PlaywrightError:
                pass
            return True
        return False
    if not select:
        return False
    target = option.value or option.label
    if not target:
        return False
    try:
        select.select_option(value=target)
        return True
    except PlaywrightError as exc:
        logger.debug("select_option failed for '%s': %s", target, exc)
    try:
        success = bool(
            select.evaluate(
                """
                (el, target) => {
                    const match = [...el.options].find(
                        opt =>
                            (target.value && opt.value === target.value) ||
                            (target.label && opt.textContent.trim() === target.label)
                    );
                    if (!match) {
                        return false;
                    }
                    el.value = match.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                """,
                {"value": option.value, "label": option.label},
            )
        )
        return success
    except PlaywrightError as exc:
        logger.debug("Fallback selection failed for '%s': %s", target, exc)
    return False


def _fill_deposit_amount(
    form: ElementHandle, logger: logging.Logger, amount: str = "1000"
) -> bool:
    selectors = (
        "input[name*='amount' i]",
        "input[id*='amount' i]",
        "input[name*='sum' i]",
        "input[type='number']",
    )
    for selector in selectors:
        try:
            field = form.query_selector(selector)
        except PlaywrightError as exc:
            logger.debug("Amount input lookup failed for '%s': %s", selector, exc)
            continue
        if not field:
            continue
        try:
            field_type = (field.get_attribute("type") or "").lower()
        except PlaywrightError:
            field_type = ""
        if field_type == "hidden":
            continue
        try:
            field.fill(amount)
            return True
        except PlaywrightError as exc:
            logger.debug("Unable to fill amount via '%s': %s", selector, exc)
    logger.debug("No amount input filled on deposit form; continuing without it")
    return False


def _ensure_payment_hidden_value(
    form: ElementHandle, option: PaymentOption, logger: logging.Logger
) -> None:
    try:
        hidden = form.query_selector(
            "input[type='hidden' i][name*='payment' i], "
            "input[type='hidden' i][id*='payment' i], "
            "input[type='hidden' i][name*='method' i], "
            "input[type='hidden' i][id*='method' i]"
        )
    except PlaywrightError as exc:
        logger.debug("Hidden payment input lookup failed: %s", exc)
        return
    if not hidden:
        return
    try:
        current_value = hidden.get_attribute("value") or ""
    except PlaywrightError:
        current_value = ""
    if current_value:
        return
    target_value = option.value or option.label
    if not target_value:
        return
    try:
        hidden.fill(target_value)
        return
    except PlaywrightError:
        pass
    try:
        hidden.evaluate(
            "(el, value) => { el.value = value; el.dispatchEvent(new Event('input', { bubbles: true })); }",
            target_value,
        )
    except PlaywrightError as exc:
        logger.debug("Unable to set hidden payment input: %s", exc)


def _match_payment_option(
    target: PaymentOption, options: List[PaymentOption]
) -> Optional[PaymentOption]:
    desired = (
        (target.value or "").strip().lower(),
        (target.label or "").strip().lower(),
    )
    for option in options:
        candidate = (
            (option.value or "").strip().lower(),
            (option.label or "").strip().lower(),
        )
        if candidate == desired:
            return option
    if options:
        return options[0]
    return None


def _submit_deposit_form(page, form: ElementHandle, logger: logging.Logger) -> None:
    try:
        with page.expect_navigation(wait_until="load", timeout=12000):
            submit_form_element(form, logger=logger)
        return
    except PlaywrightTimeoutError:
        logger.debug("Deposit form submission did not trigger navigation in time")
    except PlaywrightError as exc:
        logger.warning("Failed to submit deposit form: %s", exc)
        return
    try:
        page.wait_for_load_state("load", timeout=6000)
    except PlaywrightTimeoutError:
        logger.debug("Network idle wait after submit timed out; continuing")


def explore_deposit_form(
    browser: BrowserSession,
    run_paths: RunPaths,
    base_label: str,
    logger: logging.Logger,
    max_payment_options: int = 3,
) -> Tuple[List[str], List[Indicator]]:
    page = browser.page
    detection = _find_deposit_form_with_payments(page, logger)
    if not detection:
        return [], []
    _, _, options = detection
    payment_options = options[:max_payment_options]
    if not payment_options:
        logger.debug("Deposit form detected but no selectable payment options found")
        return [], []

    artifacts: List[str] = []
    indicators: List[Indicator] = []
    deposit_url = page.url
    logger.info(
        "Exploring deposit form at %s for %d payment methods (max %d)",
        deposit_url,
        len(payment_options),
        max_payment_options,
    )
    for idx, option in enumerate(payment_options):
        try:
            page.goto(deposit_url, wait_until="load")
        except PlaywrightError as exc:
            logger.warning(
                "Failed to load deposit page before option '%s': %s",
                option.label or option.value or f"option-{idx + 1}",
                exc,
            )
            break

        detection = _find_deposit_form_with_payments(page, logger)
        if not detection:
            logger.debug(
                "Deposit form missing when processing option %d; retrying reload",
                idx + 1,
            )
            try:
                page.goto(deposit_url, wait_until="load")
            except PlaywrightError as exc:
                logger.warning(
                    "Reload failed before option '%s': %s",
                    option.label or option.value or f"option-{idx + 1}",
                    exc,
                )
                break
            detection = _find_deposit_form_with_payments(page, logger)
        if not detection:
            logger.debug("Deposit form still missing after reload (option %d)", idx + 1)
            break
        form, select, current_options = detection
        target_option = _match_payment_option(option, current_options)
        if not target_option:
            logger.debug(
                "Unable to find payment option to match '%s'; skipping", option.label
            )
            continue
        _fill_deposit_amount(form, logger)
        if not _select_payment_option(page, select, target_option, logger):
            logger.debug(
                "Unable to select payment option '%s'; skipping", target_option.label
            )
            continue
        _ensure_payment_hidden_value(form, target_option, logger)
        _submit_deposit_form(page, form, logger)
        try:
            page.wait_for_timeout(800)
        except PlaywrightError:
            pass
        label = (
            f"{base_label}_deposit_{idx + 1:02d}_"
            f"{sanitize_filename(target_option.label or target_option.value or 'option')}"
        )
        view_artifacts, view_indicators = capture_page_state(
            browser, run_paths, label, logger
        )
        artifacts.extend(view_artifacts)
        indicators.extend(view_indicators)
    if page.url != deposit_url:
        try:
            page.goto(deposit_url, wait_until="load")
        except PlaywrightError:
            logger.debug("Unable to return to deposit page after exploration")
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
    seen_crypto = _scan_crypto_fingerprint(page, logger)
    for keyword in DEPOSIT_METHOD_KEYWORDS:
        if clicks >= max_clicks:
            break
        # logger.debug("Searching for deposit method keyword '%s'", keyword)
        for occurrence in range(2):
            if clicks >= max_clicks:
                break
            container = _find_keyword_container(page, keyword, occurrence, logger)
            if not container:
                # if occurrence == 0:
                #     logger.debug("Keyword '%s' not found on current view", keyword)
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
        if is_deposit_context(browser.page):
            form_artifacts, form_indicators = explore_deposit_form(
                browser, run_paths, label, logger
            )
            artifacts.extend(form_artifacts)
            indicators.extend(form_indicators)
            method_artifacts, method_indicators = click_deposit_methods(
                browser, run_paths, label, logger
            )
            artifacts.extend(method_artifacts)
            indicators.extend(method_indicators)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Deposit exploration failed for '%s'; continuing without it: %s",
            label,
            exc,
        )
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
