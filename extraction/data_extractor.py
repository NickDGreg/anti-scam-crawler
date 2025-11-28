"""Regex-driven extraction of financial indicators from archived HTML."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List


@dataclass(slots=True)
class ExtractedData:
    type: str
    value: str
    context: str


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


def extract_from_html(html_content: str) -> List[ExtractedData]:
    """Extract crypto addresses, IBANs, and related identifiers from HTML."""
    plain_text = strip_html(html_content)
    raw_text = html.unescape(html_content)
    corpora = [plain_text, raw_text]

    found: List[ExtractedData] = []
    for corpus in corpora:
        found.extend(_collect(IBAN_PATTERN, corpus, "IBAN"))
        found.extend(_collect(BTC_PATTERN, corpus, "BTC"))
        found.extend(_collect(ETH_PATTERN, corpus, "ETH"))
        found.extend(_collect(TRON_PATTERN, corpus, "TRON"))
        found.extend(_collect(BENEFICIARY_PATTERN, corpus, "BENEFICIARY_NAME"))
        found.extend(_collect(BANK_PATTERN, corpus, "BANK_NAME"))

    return _deduplicate(found)
