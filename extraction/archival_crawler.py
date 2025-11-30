"""Site mapping crawler that archives pages without performing extraction."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass
from typing import Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .browser import BrowserConfig, BrowserSession
from .io_utils import RunPaths, relative_artifact_path, save_text, write_json
from .login_flow import perform_login

LOGIN_PATH_HINTS = ("login", "signin", "sign-in", "sign_in")
LOGOUT_PATH_HINTS = ("logout", "log-out", "signout", "signout", "sign-out", "logoff")
REGISTER_PATH_HINTS = (
    "register",
    "signup",
    "sign-up",
    "create-account",
    "create_account",
    "sign-up",
)
SKIP_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".css",
    ".js",
    ".ico",
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".mp4",
    ".mp3",
    ".webm",
    ".webp",
)


@dataclass(slots=True)
class MappingInputs:
    start_url: str
    email: str
    secret: str
    run_paths: RunPaths
    logger: logging.Logger
    max_pages: int = 100
    max_depth: int = 3
    same_origin_only: bool = True


@dataclass(slots=True)
class PageRecord:
    url: str
    original_url: str
    status_code: Optional[int]
    content_path: Optional[str]
    screenshot_path: Optional[str]
    depth: int
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class MappingResult:
    run_id: str
    start_url: str
    pages: List[PageRecord]
    status: str
    notes: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "start_url": self.start_url,
            "pages": [page.to_dict() for page in self.pages],
            "status": self.status,
            "notes": self.notes,
        }


def _origin_host(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower().lstrip("www.")


def _normalize_url(candidate: str) -> Optional[str]:
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if not parsed.scheme or parsed.scheme in {"mailto", "tel"}:
        return None
    host = (parsed.hostname or "").lower().lstrip("www.")
    if not host:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{host}{port}"
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(), netloc=netloc, fragment=""
    )
    return urlunparse(normalized)


def _path_and_query(url: str) -> Tuple[str, str]:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    return path, query


def _contains_hint(url: str, hints: Tuple[str, ...]) -> bool:
    path, query = _path_and_query(url)
    return any(hint and (hint in path or hint in query) for hint in hints)


def _looks_like_login(url: str) -> bool:
    return _contains_hint(url, LOGIN_PATH_HINTS)


def _looks_like_logout(url: str) -> bool:
    return _contains_hint(url, LOGOUT_PATH_HINTS)


def _looks_like_register(url: str) -> bool:
    return _contains_hint(url, REGISTER_PATH_HINTS)


def _should_skip_link(url: str, *, same_origin_only: bool, origin_host: str) -> bool:
    if not url:
        return True
    if same_origin_only and _origin_host(url) != origin_host:
        return True
    lowered = url.lower()
    return lowered.endswith(SKIP_EXTENSIONS)


def extract_links(
    page,
    base_url: str,
    *,
    same_origin_only: bool,
    origin_host: str,
    logger: logging.Logger,
    avoid_auth_links: bool = True,
) -> List[str]:
    """Extract and normalize hyperlink targets from the current page."""
    try:
        raw_links = page.eval_on_selector_all(
            "a[href]",
            "anchors => anchors.map(a => a.href).filter(Boolean)",
        )
    except PlaywrightError as exc:
        logger.debug("Failed to enumerate anchors on %s: %s", base_url, exc)
        return []

    candidates: List[str] = []
    seen: Set[str] = set()
    for href in raw_links or []:
        absolute = urljoin(base_url, href)
        normalized = _normalize_url(absolute)
        if not normalized or normalized in seen:
            continue
        if _should_skip_link(
            normalized, same_origin_only=same_origin_only, origin_host=origin_host
        ):
            continue
        if _looks_like_logout(normalized):
            logger.debug("Skipping potential logout link: %s", normalized)
            continue
        if avoid_auth_links and (
            _looks_like_login(normalized) or _looks_like_register(normalized)
        ):
            logger.debug("Skipping auth-related link: %s", normalized)
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def archive_page(
    browser: BrowserSession,
    run_paths: RunPaths,
    *,
    counter: int,
    original_url: str,
    final_url: str,
    depth: int,
    status_code: Optional[int],
    logger: logging.Logger,
) -> PageRecord:
    base_label = f"{counter:02d}_page"
    html_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    error: Optional[str] = None

    try:
        html = browser.page.content()
        saved_html = save_text(run_paths.build_path(f"{base_label}.html"), html)
        html_path = relative_artifact_path(saved_html)
    except PlaywrightError as exc:
        error = f"html_capture_failed: {exc}"
        logger.debug("Unable to save HTML for %s: %s", final_url, exc)

    try:
        saved_shot = browser.screenshot(run_paths.build_path(f"{base_label}.png"))
        screenshot_path = relative_artifact_path(saved_shot)
    except PlaywrightError as exc:
        error = f"screenshot_failed: {exc}" if not error else error
        logger.debug("Unable to capture screenshot for %s: %s", final_url, exc)

    return PageRecord(
        url=final_url,
        original_url=original_url,
        status_code=status_code,
        content_path=html_path,
        screenshot_path=screenshot_path,
        depth=depth,
        error=error,
    )


def run_mapping(inputs: MappingInputs) -> MappingResult:
    logger = inputs.logger
    notes: List[str] = []
    page_records: List[PageRecord] = []
    queue: Deque[Tuple[str, int]] = deque()
    seen: Set[str] = set()
    page_counter = 1
    start_url = _normalize_url(inputs.start_url) or inputs.start_url
    login_redirects = 0
    page_limit_hit = False
    logged_in = False

    logger.debug(
        "Starting site mapping from %s (max_pages=%d, max_depth=%d, same_origin_only=%s)",
        start_url,
        inputs.max_pages,
        inputs.max_depth,
        inputs.same_origin_only,
    )

    try:
        with BrowserSession(BrowserConfig()) as browser:
            page = browser.goto(start_url, wait_until="load")
            logger.debug("Loaded entry page %s", page.url)

            logger.debug("Attempting authentication for archival crawl")
            login_result = perform_login(
                page,
                email=inputs.email,
                secret=inputs.secret,
                logger=logger,
                run_paths=inputs.run_paths,
            )
            start_url = _normalize_url(page.url) or page.url
            origin_host = _origin_host(start_url)
            if not login_result.success:
                notes.extend(login_result.notes)
                status = login_result.status
                result = MappingResult(
                    run_id=inputs.run_paths.run_id,
                    start_url=start_url,
                    pages=[],
                    status=status,
                    notes=" | ".join(notes) if notes else "",
                )
                write_json(
                    inputs.run_paths.build_path("mapping.json"), result.to_dict()
                )
                return result
            logged_in = True

            queue.append((start_url, 0))
            page = browser.page
            while queue and len(page_records) < inputs.max_pages:
                target_url, depth = queue.popleft()
                normalized_target = _normalize_url(target_url)
                if not normalized_target:
                    logger.debug("Skipping invalid URL: %s", target_url)
                    continue
                if normalized_target in seen:
                    continue
                seen.add(normalized_target)
                logger.info("Crawling depth %d URL: %s", depth, normalized_target)

                try:
                    response = page.goto(
                        normalized_target, wait_until="load", timeout=20000
                    )
                except PlaywrightTimeoutError as exc:
                    logger.warning(
                        "Navigation timeout for %s (depth %d): %s",
                        normalized_target,
                        depth,
                        exc,
                    )
                    page_records.append(
                        PageRecord(
                            url=normalized_target,
                            original_url=target_url,
                            status_code=None,
                            content_path=None,
                            screenshot_path=None,
                            depth=depth,
                            error=str(exc),
                        )
                    )
                    continue
                except PlaywrightError as exc:
                    logger.warning(
                        "Navigation error for %s (depth %d): %s",
                        normalized_target,
                        depth,
                        exc,
                    )
                    page_records.append(
                        PageRecord(
                            url=normalized_target,
                            original_url=target_url,
                            status_code=None,
                            content_path=None,
                            screenshot_path=None,
                            depth=depth,
                            error=str(exc),
                        )
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Unexpected error navigating to %s (depth %d): %s",
                        normalized_target,
                        depth,
                        exc,
                    )
                    page_records.append(
                        PageRecord(
                            url=normalized_target,
                            original_url=target_url,
                            status_code=None,
                            content_path=None,
                            screenshot_path=None,
                            depth=depth,
                            error=str(exc),
                        )
                    )
                    continue

                final_url = _normalize_url(page.url) or page.url
                if _looks_like_login(final_url):
                    login_redirects += 1
                    if login_redirects > 1:
                        logger.warning(
                            "Repeated login redirection detected (count=%d) at %s",
                            login_redirects,
                            final_url,
                        )
                    else:
                        logger.info("Encountered login-like page at %s", final_url)

                status_code = response.status if response else None
                record = archive_page(
                    browser,
                    inputs.run_paths,
                    counter=page_counter,
                    original_url=target_url,
                    final_url=final_url,
                    depth=depth,
                    status_code=status_code,
                    logger=logger,
                )
                page_counter += 1
                page_records.append(record)
                logger.info(
                    "Archived %s (status=%s, depth=%d)",
                    final_url,
                    status_code,
                    depth,
                )

                if depth >= inputs.max_depth:
                    continue

                links = extract_links(
                    page,
                    base_url=final_url,
                    same_origin_only=inputs.same_origin_only,
                    origin_host=origin_host,
                    logger=logger,
                    avoid_auth_links=logged_in,
                )
                for link in links:
                    if link not in seen:
                        queue.append((link, depth + 1))

            page_limit_hit = len(page_records) >= inputs.max_pages
    except Exception as exc:  # noqa: BLE001
        logger.exception("Mapping run failed: %s", exc)
        notes.append(str(exc))

    if not page_records:
        status = "error"
        if not notes:
            notes.append("No pages were archived during mapping.")
    elif page_limit_hit:
        status = "partial"
        notes.append("Reached page crawl limit.")
    else:
        status = "complete"

    result = MappingResult(
        run_id=inputs.run_paths.run_id,
        start_url=start_url,
        pages=page_records,
        status=status,
        notes=" | ".join(notes) if notes else "",
    )
    write_json(inputs.run_paths.build_path("mapping.json"), result.to_dict())
    return result
