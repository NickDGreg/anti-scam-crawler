"""Pattern-based parsing helpers for financial indicators."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass(slots=True)
class Indicator:
    type: str
    value: str
    source_url: str
    context: str
    artifact: str | None = None


IBAN_PATTERN = re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}\b")
BTC_PATTERN = re.compile(r"\b(?:bc1|[13])[a-zA-Z0-9]{25,59}\b")
ETH_PATTERN = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TRON_PATTERN = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")
BENEFICIARY_PATTERN = re.compile(
    r"(?:Beneficiary(?: Name)?|Account Name|Recipient|Payee)\s*[:\-]\s*([A-Za-z0-9 ,.'&()-]{3,120})",
    re.IGNORECASE,
)
BANK_PATTERN = re.compile(
    r"(?:Bank Name|Bank|Beneficiary Bank)\s*[:\-]\s*([A-Za-z0-9 ,.'&()-]{3,120})",
    re.IGNORECASE,
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


def _collect(pattern: re.Pattern[str], text: str, indicator_type: str, source_url: str) -> List[Indicator]:
    indicators: List[Indicator] = []
    for match in pattern.finditer(text):
        start, end = match.span(1 if pattern is BENEFICIARY_PATTERN or pattern is BANK_PATTERN else 0)
        value = match.group(1 if pattern in {BENEFICIARY_PATTERN, BANK_PATTERN} else 0)
        indicators.append(
            Indicator(
                type=indicator_type,
                value=value.strip(),
                source_url=source_url,
                context=context_snippet(text, start, end),
            )
        )
    return indicators


def extract_indicators(
    raw_html: str,
    source_url: str,
    *,
    extra_strings: Iterable[Tuple[str, str]] | None = None,
) -> List[Indicator]:
    plain_text = strip_html(raw_html)
    raw_text = html.unescape(raw_html)
    corpora: List[str] = [plain_text, raw_text]
    if extra_strings:
        for label, text in extra_strings:
            if not text:
                continue
            prefix = f"{label}: " if label else ""
            corpora.append(f"{prefix}{text}")

    found: List[Indicator] = []
    for corpus in corpora:
        found.extend(_collect(IBAN_PATTERN, corpus, "IBAN", source_url))
        found.extend(_collect(BTC_PATTERN, corpus, "BTC", source_url))
        found.extend(_collect(ETH_PATTERN, corpus, "ETH", source_url))
        found.extend(_collect(TRON_PATTERN, corpus, "TRON", source_url))
        found.extend(
            _collect(BENEFICIARY_PATTERN, corpus, "BENEFICIARY_NAME", source_url)
        )
        found.extend(_collect(BANK_PATTERN, corpus, "BANK_NAME", source_url))
    return _deduplicate(found)


def _deduplicate(indicators: Iterable[Indicator]) -> List[Indicator]:
    seen = set()
    unique: List[Indicator] = []
    for indicator in indicators:
        key = (indicator.type, indicator.value, indicator.source_url)
        if key in seen or not indicator.value:
            continue
        seen.add(key)
        unique.append(indicator)
    return unique
