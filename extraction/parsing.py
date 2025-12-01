"""Pattern-based parsing helpers for financial indicators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .data_extractor import extract_from_html, iter_crypto_strings


@dataclass(slots=True)
class Indicator:
    type: str
    value: str
    source_url: str
    context: str
    artifact: str | None = None


def extract_indicators(
    raw_html: str,
    source_url: str,
    *,
    extra_strings: Iterable[Tuple[str, str]] | None = None,
) -> List[Indicator]:
    extracted = extract_from_html(raw_html, extra_strings=extra_strings)
    indicators = [
        Indicator(
            type=item.type,
            value=item.value,
            source_url=source_url,
            context=item.context,
        )
        for item in extracted
    ]
    return _deduplicate(indicators)


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


def has_crypto_match(text: str) -> bool:
    return any(iter_crypto_strings(text))
