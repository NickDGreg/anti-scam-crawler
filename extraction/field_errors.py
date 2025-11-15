"""Field-level validation extraction and interpretation."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union

from playwright.sync_api import Locator, Page

from .field_classifier import FieldClassification, FieldSemantic
from .form_models import FieldDescriptor

ERROR_SELECTORS = (
    ".text-danger",
    ".error",
    ".invalid-feedback",
    ".field-validation-error",
    ".help-block",
    ".error-message",
    ".form-error",
    "[aria-live='assertive']",
)


@dataclass(slots=True)
class FieldError:
    field_name: str
    semantic: FieldSemantic
    error_text: str


@dataclass(slots=True)
class ErrorInterpretation:
    semantic: FieldSemantic
    field_name: str
    hints: Dict[str, Union[int, bool]]


DIGIT_PATTERN = re.compile(r"(\d+)[ -]?digit")
REQUIRED_DIGITS_PATTERN = re.compile(r"must be\s*(\d+)")
NUMERIC_KEYWORDS = ("numeric", "numbers only", "digits only", "digits-only")


def extract_field_errors(
    page: Page,
    classifications: Iterable[FieldClassification],
    *,
    logger: Optional[logging.Logger] = None,
) -> List[FieldError]:
    log = logger or logging.getLogger(__name__)
    errors: List[FieldError] = []
    for classification in classifications:
        descriptor = classification.descriptor
        snippet = _nearest_error_snippet(page, descriptor)
        if snippet:
            log.debug(
                "Detected field error for %s (%s): %s",
                descriptor.canonical_name(),
                classification.semantic.value,
                snippet,
            )
            errors.append(
                FieldError(
                    field_name=descriptor.canonical_name(),
                    semantic=classification.semantic,
                    error_text=snippet,
                )
            )
    return errors


def _nearest_error_snippet(page: Page, descriptor: FieldDescriptor) -> Optional[str]:
    handle = descriptor.handle
    try:
        maybe_error = handle.evaluate(
            """
(el, selectors) => {
  const container = el.closest('.form-group, .field, .input, .form-item, label, div, p, section');
  const siblings = [];
  if (el.nextElementSibling) siblings.push(el.nextElementSibling);
  if (el.previousElementSibling) siblings.push(el.previousElementSibling);
  const parent = el.parentElement;
  if (parent) siblings.push(parent);
  const collectText = (node) => {
    if (!node) return null;
    const text = (node.innerText || node.textContent || '').trim();
    return text && text.length < 240 ? text : null;
  };
  const candidates = [];
  const addNodeTexts = (nodeList) => {
    for (const node of nodeList) {
      const text = collectText(node);
      if (text) candidates.push(text);
    }
  };
  for (const sib of siblings) {
    const text = collectText(sib);
    if (text) candidates.push(text);
  }
  if (container) {
    const text = collectText(container);
    if (text) candidates.push(text);
  }
  const searchTargets = [];
  if (container) searchTargets.push(container);
  if (parent) searchTargets.push(parent);
  searchTargets.push(document);
  for (const target of searchTargets) {
    for (const selector of selectors) {
      const found = target.querySelectorAll(selector);
      addNodeTexts(found);
    }
  }
  return candidates;
}
""",
            list(ERROR_SELECTORS),
        )
    except Exception:  # noqa: BLE001
        maybe_error = []
    candidates = maybe_error or []
    for text in candidates:
        snippet = _extract_error_line(str(text))
        if snippet:
            return snippet
    # aria-invalid hint
    try:
        aria_invalid = handle.get_attribute("aria-invalid")
        if aria_invalid == "true":
            return "Field marked invalid"
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_error_line(text: str) -> Optional[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if any(
            keyword in lowered for keyword in ("error", "invalid", "required", "must")
        ):
            return line[:250]
    if lines:
        return lines[0][:250]
    return None


def interpret_field_error(field_error: FieldError) -> Optional[ErrorInterpretation]:
    text = field_error.error_text.lower()
    hints: Dict[str, Union[int, bool]] = {}
    digit_match = DIGIT_PATTERN.search(text) or REQUIRED_DIGITS_PATTERN.search(text)
    if digit_match:
        try:
            required_digits = int(digit_match.group(1))
            hints["required_digits"] = required_digits
        except ValueError:
            pass
    if any(keyword in text for keyword in NUMERIC_KEYWORDS):
        hints["numeric_only"] = True
    if hints:
        return ErrorInterpretation(
            semantic=field_error.semantic,
            field_name=field_error.field_name,
            hints=hints,
        )
    return None


__all__ = [
    "FieldError",
    "ErrorInterpretation",
    "extract_field_errors",
    "interpret_field_error",
]
