"""Site mapping crawler that archives pages without performing extraction."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import tldextract
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .browser import BrowserConfig, BrowserSession
from .io_utils import RunPaths, relative_artifact_path, save_text, write_json
from .login_flow import perform_login
from .page_utils import safe_goto

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
ALLOWED_SCHEMES = {"http", "https"}
INFRA_DOMAIN_BLOCKLIST: Set[str] = {
    "google.com",
    "google.ch",
    "consent.google.com",
    "policies.google.com",
    "about.google.com",
    "about.google",
    "youtube.com",
    "consent.youtube.com",
    "accounts.google.com",
    "g.co",
    "goo.gl",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
}
HIGH_VALUE_EXTERNAL_DOMAINS: Set[str] = set()
AUTH_WALL_REDIRECT_THRESHOLD = 2
_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=None)


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
    allow_external: bool = False


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
    return _registrable_domain(url)


def _extract_host(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower().lstrip("www.")


def _registrable_domain(url: str) -> str:
    host = _extract_host(url)
    if not host:
        return ""
    extracted = _TLD_EXTRACTOR(host)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}".lower()
    return host


def _is_same_site(url: str, home_domain: str) -> bool:
    if not home_domain:
        return False
    return _registrable_domain(url) == home_domain


def _normalize_url(candidate: str) -> Optional[str]:
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if not parsed.scheme or parsed.scheme in {"mailto", "tel"}:
        return None
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
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


def _is_static_asset(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith(SKIP_EXTENSIONS)


def _is_infra_domain(domain: str, home_domain: str) -> bool:
    return bool(domain and domain != home_domain and domain in INFRA_DOMAIN_BLOCKLIST)


def extract_links(
    page,
    base_url: str,
    *,
    home_domain: str,
    allow_external: bool,
    logger: logging.Logger,
    avoid_auth_links: bool = True,
    external_allowlist: Set[str] | None = None,
    infra_blocklist: Set[str] | None = None,
    blocked_domains: Set[str] | None = None,
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

    allowlist = (
        HIGH_VALUE_EXTERNAL_DOMAINS
        if external_allowlist is None
        else external_allowlist
    )
    candidates: List[str] = []
    seen: Set[str] = set()
    for href in raw_links or []:
        absolute = urljoin(base_url, href)
        normalized = _normalize_url(absolute)
        if not normalized or normalized in seen:
            continue
        domain = _registrable_domain(normalized)
        if blocked_domains and domain in blocked_domains:
            logger.debug(
                "Skipping URL on blocked auth-wall domain %s: %s", domain, normalized
            )
            continue
        if infra_blocklist and _is_infra_domain(domain, home_domain=home_domain):
            logger.debug(
                "Skipping infra domain link (%s): %s", domain or "<unknown>", normalized
            )
            continue
        if _is_static_asset(normalized):
            logger.debug("Skipping static asset link: %s", normalized)
            continue
        if _looks_like_logout(normalized):
            logger.debug("Skipping potential logout link: %s", normalized)
            continue
        if avoid_auth_links and (
            _looks_like_login(normalized) or _looks_like_register(normalized)
        ):
            logger.debug("Skipping auth-related link: %s", normalized)
            continue
        same_site = _is_same_site(normalized, home_domain)
        if not same_site:
            if not allow_external:
                logger.debug("Skipping external URL (same-site only): %s", normalized)
                continue
            if domain not in allowlist:
                logger.debug(
                    "Skipping external URL (domain not allowlisted): %s", normalized
                )
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
    same_site_queue: Deque[Tuple[str, int]] = deque()
    external_queue: Deque[Tuple[str, int]] = deque()
    seen: Set[str] = set()
    blocked_auth_domains: Set[str] = set()
    login_redirect_counts: Dict[str, int] = defaultdict(int)
    page_counter = 1
    start_url = _normalize_url(inputs.start_url) or inputs.start_url
    page_limit_hit = False
    logged_in = False
    allow_external = inputs.allow_external or not inputs.same_origin_only

    logger.debug(
        (
            "Starting site mapping from %s "
            "(max_pages=%d, max_depth=%d, same_origin_only=%s, allow_external=%s)"
        ),
        start_url,
        inputs.max_pages,
        inputs.max_depth,
        inputs.same_origin_only,
        allow_external,
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
            home_domain = _registrable_domain(start_url)
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

            same_site_queue.append((start_url, 0))
            page = browser.page
            while (same_site_queue or external_queue) and len(
                page_records
            ) < inputs.max_pages:
                if same_site_queue:
                    target_url, depth = same_site_queue.popleft()
                    queue_label = "same-site"
                else:
                    target_url, depth = external_queue.popleft()
                    queue_label = "external"
                normalized_target = _normalize_url(target_url)
                if not normalized_target:
                    logger.debug("Skipping invalid URL: %s", target_url)
                    continue
                if normalized_target in seen:
                    continue
                domain = _registrable_domain(normalized_target)
                if _is_infra_domain(domain, home_domain):
                    logger.debug(
                        "Skipping infra domain before navigation (%s): %s",
                        domain or "<unknown>",
                        normalized_target,
                    )
                    seen.add(normalized_target)
                    continue
                if domain in blocked_auth_domains:
                    logger.debug(
                        "Skipping URL on blocked auth-wall domain %s: %s",
                        domain or "<unknown>",
                        normalized_target,
                    )
                    seen.add(normalized_target)
                    continue
                seen.add(normalized_target)
                logger.info(
                    "Crawling depth %d %s URL: %s",
                    depth,
                    queue_label,
                    normalized_target,
                )

                try:
                    response = safe_goto(
                        page,
                        normalized_target,
                        logger=logger,
                        timeout_ms=20000,
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
                final_domain = _registrable_domain(final_url)
                skip_link_expansion = False
                if _looks_like_login(final_url):
                    login_redirect_counts[final_domain] += 1
                    redirect_count = login_redirect_counts[final_domain]
                    if (
                        final_domain
                        and final_domain != home_domain
                        and redirect_count >= (AUTH_WALL_REDIRECT_THRESHOLD)
                    ):
                        blocked_auth_domains.add(final_domain)
                        skip_link_expansion = True
                        logger.warning(
                            (
                                "Repeated login redirection detected on external domain "
                                "%s (count=%d); blocking further navigation"
                            ),
                            final_domain or "<unknown>",
                            redirect_count,
                        )
                    elif redirect_count > 1:
                        logger.warning(
                            "Repeated login redirection detected (count=%d) at %s",
                            redirect_count,
                            final_url,
                        )
                    else:
                        logger.info("Encountered login-like page at %s", final_url)
                elif final_domain in blocked_auth_domains:
                    skip_link_expansion = True
                    logger.debug(
                        "Final URL %s is on blocked auth-wall domain %s; not expanding links",
                        final_url,
                        final_domain or "<unknown>",
                    )

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

                if depth >= inputs.max_depth or skip_link_expansion:
                    continue

                links = extract_links(
                    page,
                    base_url=final_url,
                    home_domain=home_domain,
                    allow_external=allow_external,
                    logger=logger,
                    avoid_auth_links=logged_in,
                    external_allowlist=HIGH_VALUE_EXTERNAL_DOMAINS,
                    infra_blocklist=INFRA_DOMAIN_BLOCKLIST,
                    blocked_domains=blocked_auth_domains,
                )
                for link in links:
                    if link in seen:
                        continue
                    link_domain = _registrable_domain(link)
                    if link_domain in blocked_auth_domains:
                        logger.debug(
                            "Not enqueueing URL on blocked auth-wall domain %s: %s",
                            link_domain or "<unknown>",
                            link,
                        )
                        continue
                    if depth + 1 > inputs.max_depth:
                        continue
                    if _is_same_site(link, home_domain):
                        same_site_queue.append((link, depth + 1))
                    else:
                        external_queue.append((link, depth + 1))

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
