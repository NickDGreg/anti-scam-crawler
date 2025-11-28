"""Pattern-based parsing helpers for financial indicators."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .data_extractor import (
    BANK_PATTERN,
    BENEFICIARY_PATTERN,
    BTC_PATTERN,
    ETH_PATTERN,
    IBAN_PATTERN,
    TRON_PATTERN,
    context_snippet,
    strip_html,
)


@dataclass(slots=True)
class Indicator:
    type: str
    value: str
    source_url: str
    context: str
    artifact: str | None = None


def _collect(
    pattern, text: str, indicator_type: str, source_url: str
) -> List[Indicator]:
    indicators: List[Indicator] = []
    for match in pattern.finditer(text):
        start, end = match.span(
            1 if pattern in {BENEFICIARY_PATTERN, BANK_PATTERN} else 0
        )
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
    corpora: List[str] = [strip_html(raw_html), html.unescape(raw_html)]
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
