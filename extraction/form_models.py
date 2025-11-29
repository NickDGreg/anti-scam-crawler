"""Data models shared across form analysis helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.sync_api import ElementHandle


@dataclass(slots=True)
class OptionMetadata:
    label: str
    value: str


@dataclass(slots=True)
class FieldDescriptor:
    handle: ElementHandle
    tag: str
    input_type: Optional[str]
    name: Optional[str]
    identifier: Optional[str]
    placeholder: Optional[str]
    aria_label: Optional[str]
    labels: List[str]
    surrounding_text: Optional[str]
    required: bool
    classes: List[str]
    autocomplete: Optional[str]
    dataset: Dict[str, str]
    options: List[OptionMetadata]
    order: int

    def canonical_name(self) -> str:
        for candidate in (
            self.name,
            self.identifier,
            self.placeholder,
            self.aria_label,
        ):
            if candidate:
                return candidate
        return f"field_{self.order}"


@dataclass(slots=True)
class FormDescriptor:
    element: ElementHandle
    fields: List[FieldDescriptor]
    index: int
    heading_text: str
    inner_text: str
    action: Optional[str]
    method: Optional[str]
    score: float = 0.0
    signals: Dict[str, float] = field(default_factory=dict)


__all__ = [
    "OptionMetadata",
    "FieldDescriptor",
    "FormDescriptor",
]
