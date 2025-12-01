"""Regex-driven extraction of financial indicators from archived HTML."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urlparse


@dataclass(slots=True)
class ExtractedData:
    type: str
    value: str
    context: str


# Strict crypto patterns
BTC_LEGACY_PATTERN = re.compile(r"\b[13][A-HJ-NP-Za-km-z1-9]{25,34}\b")
BTC_BECH32_PATTERN = re.compile(
    r"\bbc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{11,71}\b", re.IGNORECASE
)
BTC_PATTERN = re.compile(
    r"\b(?:[13][A-HJ-NP-Za-km-z1-9]{25,34}|bc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{11,71})\b",
    re.IGNORECASE,
)
ETH_PATTERN = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TRON_PATTERN = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")

# Bank/beneficiary patterns (unchanged)
IBAN_PATTERN = re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}\b")
BENEFICIARY_PATTERN = re.compile(
    r"(?:Beneficiary(?: Name)?|Account Name|Recipient|Payee)\s*[:\-]\s*([A-Za-z0-9 ,.'&()-]{3,120})",
    re.IGNORECASE,
)
BANK_PATTERN = re.compile(
    r"(?:Bank Name|Bank|Beneficiary Bank)\s*[:\-]\s*([A-Za-z0-9 ,.'&()-]{3,120})",
    re.IGNORECASE,
)

BLOCKED_TAGS = {"script", "style", "iframe"}
BLOCKED_ATTR_TAGS = {"script", "style", "iframe", "link", "meta"}
INFRA_ATTR_NAMES = {"src", "href", "integrity", "content"}
VISIBLE_TEXT_TAGS = {
    "a",
    "b",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "label",
    "li",
    "p",
    "pre",
    "span",
    "strong",
    "td",
    "th",
}
INFRA_DOMAIN_KEYWORDS = (
    "stripe.com",
    "stripe.network",
    "js.stripe.com",
    "code.jquery.com",
    "jquery.com",
    "googleapis.com",
    "gstatic.com",
    "bootstrapcdn.com",
    "cdn.jsdelivr.net",
    "cloudflare.com",
)
INPUT_POSITIVE_CLASS_HINTS = (
    "copy",
    "clipboard",
    "address",
    "wallet",
    "readonly",
    "deposit",
    "payment",
)


def strip_html(raw_html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def context_snippet(text: str, start: int, end: int, radius: int = 80) -> str:
    snippet = text[max(0, start - radius) : min(len(text), end + radius)]
    return snippet.strip()


@dataclass(slots=True)
class _CryptoMatch:
    indicator_type: str
    value: str
    tag: str | None
    attr: str | None
    context_source: str
    positive: bool
    infra: bool


def _is_bech32_case_valid(value: str) -> bool:
    has_lower = any(char.islower() for char in value)
    has_upper = any(char.isupper() for char in value)
    return not (has_lower and has_upper)


def _iter_crypto_matches(
    text: str,
) -> Iterable[Tuple[str, str, int, int]]:
    if not text:
        return
    for pattern in (BTC_LEGACY_PATTERN, BTC_BECH32_PATTERN):
        for match in pattern.finditer(text):
            value = match.group(0)
            if pattern is BTC_BECH32_PATTERN and not _is_bech32_case_valid(value):
                continue
            yield ("BTC", value, match.start(), match.end())
    for match in ETH_PATTERN.finditer(text):
        yield ("ETH", match.group(0), match.start(), match.end())
    for match in TRON_PATTERN.finditer(text):
        yield ("TRON", match.group(0), match.start(), match.end())


def iter_crypto_strings(text: str) -> Iterable[Tuple[str, str]]:
    for indicator_type, value, _, _ in _iter_crypto_matches(text):
        yield indicator_type, value


def _is_infra_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.netloc or "").lower()
    if not host and value.startswith("//"):
        host = urlparse(f"http:{value}").netloc.lower()
    if not host:
        return False
    return any(token in host for token in INFRA_DOMAIN_KEYWORDS)


def _is_positive_context(
    tag: str | None, attr: str | None, attrs: Dict[str, str]
) -> bool:
    if attr:
        if tag == "input" and attr == "value":
            input_type = attrs.get("type", "").lower()
            if input_type == "password":
                return False
            class_value = attrs.get("class", "").lower()
            return bool(
                attrs.get("readonly") is not None
                or attrs.get("disabled") is not None
                or any(hint in class_value for hint in INPUT_POSITIVE_CLASS_HINTS)
                or input_type in ("text", "tel", "hidden", "")
            )
        return False
    if tag is None:
        return True
    return tag in VISIBLE_TEXT_TAGS


class _CryptoDOMScanner(HTMLParser):
    def __init__(self, raw_html: str):
        super().__init__(convert_charrefs=True)
        self.raw_html = raw_html
        self.stack: List[Dict[str, Dict[str, str]]] = []
        self.matches: Dict[Tuple[str, str], List[_CryptoMatch]] = {}
        self.block_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_map: Dict[str, str] = {
            name.lower(): (value or "") for name, value in attrs if name
        }
        self.stack.append({"tag": tag_lower, "attrs": attr_map})
        if tag_lower in BLOCKED_TAGS:
            self.block_depth += 1
        blocked_tag = self.block_depth > 0 or tag_lower in BLOCKED_ATTR_TAGS
        if blocked_tag:
            return

        for name, value in attrs:
            if not name or value is None:
                continue
            name_lower = name.lower()
            if name_lower in INFRA_ATTR_NAMES and tag_lower in BLOCKED_ATTR_TAGS:
                continue
            infra = _is_infra_url(value) if name_lower in INFRA_ATTR_NAMES else False
            positive = _is_positive_context(tag_lower, name_lower, attr_map)
            self._record_matches(value, tag_lower, name_lower, value, positive, infra)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in BLOCKED_TAGS and self.block_depth > 0:
            self.block_depth -= 1
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not data or not data.strip():
            return
        current = self.stack[-1] if self.stack else {"tag": None, "attrs": {}}
        tag = current["tag"]
        if self.block_depth > 0 or (tag and tag in BLOCKED_ATTR_TAGS):
            return
        positive = _is_positive_context(tag, None, current["attrs"])
        self._record_matches(data, tag, None, data, positive, infra=False)

    def _record_matches(
        self,
        text: str,
        tag: str | None,
        attr: str | None,
        context_source: str,
        positive: bool,
        infra: bool,
    ) -> None:
        for indicator_type, value, _, _ in _iter_crypto_matches(text):
            key = (indicator_type, value)
            self.matches.setdefault(key, []).append(
                _CryptoMatch(
                    indicator_type=indicator_type,
                    value=value,
                    tag=tag,
                    attr=attr,
                    context_source=context_source,
                    positive=positive,
                    infra=infra,
                )
            )


def _build_context(raw_html: str, value: str, fallback: str | None) -> str:
    idx = raw_html.find(value)
    if idx != -1:
        return context_snippet(raw_html, idx, idx + len(value))
    if fallback:
        inner_idx = fallback.find(value)
        if inner_idx != -1:
            return context_snippet(fallback, inner_idx, inner_idx + len(value))
    return value


def _extract_crypto_from_html(
    html_content: str, extra_strings: Iterable[Tuple[str, str]] | None
) -> List[ExtractedData]:
    scanner = _CryptoDOMScanner(html_content)
    scanner.feed(html_content)

    if extra_strings:
        for label, text in extra_strings:
            if not text:
                continue
            for indicator_type, value, _, _ in _iter_crypto_matches(text):
                key = (indicator_type, value)
                scanner.matches.setdefault(key, []).append(
                    _CryptoMatch(
                        indicator_type=indicator_type,
                        value=value,
                        tag="extra",
                        attr=label,
                        context_source=text,
                        positive=True,
                        infra=False,
                    )
                )

    results: List[ExtractedData] = []
    for (indicator_type, value), contexts in scanner.matches.items():
        positive_contexts = [ctx for ctx in contexts if ctx.positive and not ctx.infra]
        if not positive_contexts:
            continue
        chosen = positive_contexts[0]
        context = _build_context(html_content, value, chosen.context_source)
        results.append(ExtractedData(indicator_type, value, context))
    return results


def _collect(
    pattern: re.Pattern[str], text: str, indicator_type: str
) -> List[ExtractedData]:
    results: List[ExtractedData] = []
    for match in pattern.finditer(text):
        start, end = match.span(
            1 if pattern in {BENEFICIARY_PATTERN, BANK_PATTERN} else 0
        )
        value = match.group(1 if pattern in {BENEFICIARY_PATTERN, BANK_PATTERN} else 0)
        results.append(
            ExtractedData(
                type=indicator_type,
                value=value.strip(),
                context=context_snippet(text, start, end),
            )
        )
    return results


def _deduplicate(indicators: List[ExtractedData]) -> List[ExtractedData]:
    seen = set()
    unique: List[ExtractedData] = []
    for indicator in indicators:
        key = (indicator.type, indicator.value)
        if key in seen or not indicator.value:
            continue
        seen.add(key)
        unique.append(indicator)
    return unique


def extract_from_html(
    html_content: str, *, extra_strings: Iterable[Tuple[str, str]] | None = None
) -> List[ExtractedData]:
    """Extract crypto addresses, IBANs, and related identifiers from HTML."""
    corpora = [strip_html(html_content), html.unescape(html_content)]
    if extra_strings:
        for label, text in extra_strings:
            if not text:
                continue
            prefix = f"{label}: " if label else ""
            corpora.append(f"{prefix}{text}")

    found: List[ExtractedData] = []
    found.extend(_extract_crypto_from_html(html_content, extra_strings))
    for corpus in corpora:
        found.extend(_collect(IBAN_PATTERN, corpus, "IBAN"))
        found.extend(_collect(BENEFICIARY_PATTERN, corpus, "BENEFICIARY_NAME"))
        found.extend(_collect(BANK_PATTERN, corpus, "BANK_NAME"))

    return _deduplicate(found)
