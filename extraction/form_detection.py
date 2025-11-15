"""Form discovery and scoring utilities."""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

from playwright.sync_api import ElementHandle, Page

from .field_classifier import FieldClassification, FieldSemantic, classify_field
from .form_models import FieldDescriptor, FormDescriptor, OptionMetadata

FIELD_QUERY = "input, select, textarea"
IGNORED_INPUT_TYPES = {"hidden", "submit", "reset", "button", "image"}
REGISTRATION_KEYWORDS = (
    "register",
    "sign up",
    "sign-up",
    "create account",
    "open account",
    "get started",
    "join",
)
LOGIN_KEYWORDS = ("log in", "login", "sign in", "client area")
MIN_REGISTRATION_SCORE = 20


def find_best_registration_form(
    page: Page, logger: logging.Logger
) -> Tuple[Optional[FormDescriptor], List[FieldClassification]]:
    forms = extract_form_descriptors(page)
    best: Optional[FormDescriptor] = None
    best_classifications: List[FieldClassification] = []
    for descriptor in forms:
        classifications = [classify_field(field) for field in descriptor.fields]
        score, signals = score_form_candidate(descriptor, classifications)
        descriptor.score = score
        descriptor.signals = signals
        logger.debug(
            "Form #%s scored %.1f with signals %s",
            descriptor.index,
            score,
            {k: round(v, 2) for k, v in signals.items()},
        )
        if not best or score > best.score:
            best = descriptor
            best_classifications = classifications
    if best and best.score >= MIN_REGISTRATION_SCORE:
        logger.info("Selected form #%s with score %.1f", best.index, best.score)
        return best, best_classifications
    logger.warning(
        "No registration-like form over threshold (best=%.1f)",
        best.score if best else 0.0,
    )
    return None, []


def extract_form_descriptors(page: Page) -> List[FormDescriptor]:
    descriptors: List[FormDescriptor] = []
    forms = page.query_selector_all("form")
    for index, form in enumerate(forms):
        fields = _extract_fields(form)
        heading_text = (form.inner_text() or "").strip()
        inner_text = heading_text.lower()
        descriptors.append(
            FormDescriptor(
                element=form,
                fields=fields,
                index=index,
                heading_text=heading_text,
                inner_text=inner_text,
            )
        )
    return descriptors


def _extract_fields(form: ElementHandle) -> List[FieldDescriptor]:
    controls = form.query_selector_all(FIELD_QUERY)
    descriptors: List[FieldDescriptor] = []
    for order, control in enumerate(controls):
        tag = (control.evaluate("el => el.tagName.toLowerCase()") or "").lower()
        if tag == "input":
            input_type = (control.get_attribute("type") or "").lower()
            if input_type in IGNORED_INPUT_TYPES:
                continue
        descriptor = _build_field_descriptor(control, order)
        if descriptor:
            descriptors.append(descriptor)
    return descriptors


FIELD_PROBE_SCRIPT = """
(el) => {
  const classes = Array.from(el.classList || []);
  const labels = [];
  if (el.labels && el.labels.length) {
    for (const label of Array.from(el.labels)) {
      const text = (label.innerText || label.textContent || '').trim();
      if (text) labels.push(text);
    }
  }
  const closestLabel = el.closest('label');
  if (closestLabel) {
    const text = (closestLabel.innerText || closestLabel.textContent || '').trim();
    if (text) labels.push(text);
  }
  const container = el.closest('.form-group, .field, .input, .form-item, label, div, p, section');
  const containerText = container ? (container.innerText || container.textContent || '').trim() : '';
  const dataset = {};
  for (const attr of Array.from(el.attributes || [])) {
    if (attr.name.startsWith('data-')) {
      dataset[attr.name] = attr.value;
    }
  }
  const options = [];
  if (el.tagName.toLowerCase() === 'select') {
    for (const opt of Array.from(el.options || [])) {
      options.push({
        label: (opt.innerText || opt.textContent || '').trim(),
        value: opt.value || (opt.innerText || opt.textContent || '').trim(),
      });
    }
  }
  return {
    tag: el.tagName.toLowerCase(),
    type: (el.type || '').toLowerCase(),
    name: el.getAttribute('name'),
    id: el.id || null,
    placeholder: el.getAttribute('placeholder'),
    ariaLabel: el.getAttribute('aria-label'),
    labels,
    surroundingText: containerText || null,
    required: !!el.required || el.getAttribute('aria-required') === 'true' || classes.some(cls => /required/i.test(cls)),
    classes,
    autocomplete: el.getAttribute('autocomplete'),
    dataset,
    options,
  };
}
"""


def _build_field_descriptor(
    handle: ElementHandle, order: int
) -> Optional[FieldDescriptor]:
    data = handle.evaluate(FIELD_PROBE_SCRIPT)
    if not data:
        return None
    options = [
        OptionMetadata(label=opt["label"], value=opt["value"])
        for opt in data.get("options", [])
        if opt.get("label")
    ]
    return FieldDescriptor(
        handle=handle,
        tag=data.get("tag", "input"),
        input_type=data.get("type"),
        name=data.get("name"),
        identifier=data.get("id"),
        placeholder=data.get("placeholder"),
        aria_label=data.get("ariaLabel"),
        labels=data.get("labels", []) or [],
        surrounding_text=data.get("surroundingText"),
        required=bool(data.get("required")),
        classes=data.get("classes", []) or [],
        autocomplete=data.get("autocomplete"),
        dataset=data.get("dataset", {}) or {},
        options=options,
        order=order,
    )


def score_form_candidate(
    descriptor: FormDescriptor,
    classifications: Sequence[FieldClassification],
) -> Tuple[float, dict]:
    score = 0.0
    signals = {}

    counts = _semantic_counts(classifications)
    if counts.get(FieldSemantic.PASSWORD, 0):
        score += 25
        signals["password"] = 25
    if counts.get(FieldSemantic.EMAIL, 0):
        score += 20
        signals["email"] = 20
    if counts.get(FieldSemantic.PASSWORD_CONFIRM, 0):
        score += 10
        signals["password_confirm"] = 10
    if counts.get(FieldSemantic.USERNAME, 0):
        score += 6
        signals["username"] = 6
    name_signals = (
        counts.get(FieldSemantic.FULL_NAME, 0)
        + counts.get(FieldSemantic.FIRST_NAME, 0)
        + counts.get(FieldSemantic.LAST_NAME, 0)
    )
    if name_signals:
        score += 4
        signals["name"] = 4
    if counts.get(FieldSemantic.PHONE, 0):
        score += 3
        signals["phone"] = 3
    if counts.get(FieldSemantic.COUNTRY, 0):
        score += 4
        signals["country"] = 4
    if counts.get(FieldSemantic.CURRENCY, 0):
        score += 3
        signals["currency"] = 3
    if counts.get(FieldSemantic.GENDER, 0):
        score += 2
        signals["gender"] = 2
    if len(descriptor.fields) >= 5:
        score += 4
        signals["field_volume"] = 4

    text = f"{descriptor.heading_text} {descriptor.inner_text}".lower()
    for keyword in REGISTRATION_KEYWORDS:
        if keyword in text:
            score += 4
            signals.setdefault("keywords", 0)
            signals["keywords"] += 4
    for keyword in LOGIN_KEYWORDS:
        if keyword in text:
            score -= 6
            signals.setdefault("login_terms", 0)
            signals["login_terms"] -= 6

    # reward higher confidence on key fields
    confidence_bonus = sum(
        cls.confidence
        for cls in classifications
        if cls.semantic
        in {FieldSemantic.EMAIL, FieldSemantic.PASSWORD, FieldSemantic.USERNAME}
    )
    if confidence_bonus:
        signals["confidence"] = confidence_bonus
        score += confidence_bonus

    return score, signals


def _semantic_counts(classifications: Sequence[FieldClassification]) -> dict:
    counts = {}
    for cls in classifications:
        counts[cls.semantic] = counts.get(cls.semantic, 0) + 1
    return counts


__all__ = [
    "find_best_registration_form",
    "extract_form_descriptors",
    "score_form_candidate",
]
