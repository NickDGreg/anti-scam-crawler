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
CONTACT_KEYWORDS = ("contact", "support", "message", "help", "assist")
CONTACT_ACTION_KEYWORDS = ("contact", "support", "help", "ticket", "message")
ORPHAN_EXTRA_KEYWORDS = (
    "gender",
    "marital",
    "marriage",
    "address",
    "city",
    "state",
    "province",
    "zip",
    "postal",
)


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
        attach_orphan_controls_to_form(page, best, logger)
        best_classifications = [classify_field(field) for field in best.fields]
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
        action = form.get_attribute("action")
        method = form.get_attribute("method")
        descriptors.append(
            FormDescriptor(
                element=form,
                fields=fields,
                index=index,
                heading_text=heading_text,
                inner_text=inner_text,
                action=action,
                method=method,
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


def _find_form_container(form_handle: ElementHandle) -> ElementHandle:
    try:
        container_handle = form_handle.evaluate_handle(
            """
            (form) => {
              const preferred = form.closest('.main-signup-body, .card, .form-card, .form-wrapper, .form-container');
              if (preferred) return preferred;
              const ancestor = form.closest('section, article, main') || form.parentElement;
              return ancestor || form;
            }
            """
        )
        container_element = container_handle.as_element() if container_handle else None
        if container_element:
            return container_element
    except Exception:
        pass
    return form_handle


def _descriptor_signature(descriptor: FieldDescriptor) -> tuple:
    return (
        descriptor.tag,
        descriptor.name,
        descriptor.identifier,
        descriptor.placeholder,
        descriptor.aria_label,
        descriptor.surrounding_text,
    )


def _descriptor_text(descriptor: FieldDescriptor) -> str:
    parts: List[str] = []
    parts.extend(descriptor.labels or [])
    for candidate in (
        descriptor.placeholder,
        descriptor.aria_label,
        descriptor.surrounding_text,
        descriptor.name,
        descriptor.identifier,
    ):
        if candidate:
            parts.append(str(candidate))
    return " ".join(parts).strip()


def _looks_like_consent(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if "terms" in lowered and ("condition" in lowered or "conditions" in lowered):
        return True
    if "terms" in lowered and "agree" in lowered:
        return True
    return False


def _looks_like_registration_extra(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in ORPHAN_EXTRA_KEYWORDS)


def _should_attach_orphan(descriptor: FieldDescriptor, text: str) -> bool:
    input_type = (descriptor.input_type or "").lower()
    if descriptor.required:
        return True
    if input_type in {"checkbox", "radio"} and _looks_like_consent(text):
        return True
    if _looks_like_registration_extra(text):
        return True
    return False


def attach_orphan_controls_to_form(
    page: Page, descriptor: FormDescriptor, logger: logging.Logger
) -> None:
    _ = page  # page available for future heuristics
    container = _find_form_container(descriptor.element)
    controls = container.query_selector_all(FIELD_QUERY)
    if not controls:
        return

    existing_handles = {id(field.handle) for field in descriptor.fields}
    existing_keys = {
        (field.tag, field.name, field.identifier)
        for field in descriptor.fields
        if field.name or field.identifier
    }
    existing_signatures = {_descriptor_signature(field) for field in descriptor.fields}
    max_order = max((field.order for field in descriptor.fields), default=-1)
    next_order = max_order + 1
    attached = 0

    for control in controls:
        try:
            handle_id = id(control)
            if handle_id in existing_handles:
                continue

            tag = (control.evaluate("el => el.tagName.toLowerCase()") or "").lower()
            input_type = (
                (control.get_attribute("type") or "").lower() if tag == "input" else ""
            )
            if tag == "input" and input_type in IGNORED_INPUT_TYPES:
                continue

            name = control.get_attribute("name")
            identifier = control.get_attribute("id")
            key = (tag, name, identifier)
            if (name or identifier) and key in existing_keys:
                continue

            belongs_to_form = False
            try:
                belongs_to_form = bool(
                    control.evaluate(
                        "(el, formEl) => el.form === formEl", descriptor.element
                    )
                )
            except Exception:
                belongs_to_form = False
            if belongs_to_form:
                continue

            field_descriptor = _build_field_descriptor(control, next_order)
            if not field_descriptor:
                continue

            signature = _descriptor_signature(field_descriptor)
            if signature in existing_signatures:
                continue

            text_blob = _descriptor_text(field_descriptor)
            if not _should_attach_orphan(field_descriptor, text_blob):
                continue

            descriptor.fields.append(field_descriptor)
            existing_handles.add(handle_id)
            existing_signatures.add(signature)
            if name or identifier:
                existing_keys.add(key)
            logger.debug(
                "Attached orphan field %s (%s)",
                field_descriptor.canonical_name(),
                (field_descriptor.surrounding_text or text_blob)[:80],
            )
            next_order += 1
            attached += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping orphan candidate due to error: %s", exc)
            continue

    if attached:
        logger.info(
            "Attached %s orphan controls near form #%s", attached, descriptor.index
        )


def score_form_candidate(
    descriptor: FormDescriptor,
    classifications: Sequence[FieldClassification],
) -> Tuple[float, dict]:
    score = 0.0
    signals = {}

    counts = _semantic_counts(classifications)
    has_password = counts.get(FieldSemantic.PASSWORD, 0) > 0
    has_username = counts.get(FieldSemantic.USERNAME, 0) > 0
    has_email = counts.get(FieldSemantic.EMAIL, 0) > 0
    if has_password:
        score += 25
        signals["password"] = 25
    if has_email:
        score += 20
        signals["email"] = 20
    if counts.get(FieldSemantic.PASSWORD_CONFIRM, 0):
        score += 10
        signals["password_confirm"] = 10
    if has_username:
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

    # penalize forms that look like contact/support forms
    contact_penalty = 0.0
    if _has_textarea(descriptor) and not has_password:
        contact_penalty += 8
    if _is_contact_like_form(descriptor):
        contact_penalty += 8
    if not has_password and not has_username:
        contact_penalty += 4
    if contact_penalty:
        score -= contact_penalty
        signals["contact_penalty"] = -contact_penalty

    return score, signals


def _semantic_counts(classifications: Sequence[FieldClassification]) -> dict:
    counts = {}
    for cls in classifications:
        counts[cls.semantic] = counts.get(cls.semantic, 0) + 1
    return counts


def _has_textarea(descriptor: FormDescriptor) -> bool:
    return any(field.tag == "textarea" for field in descriptor.fields)


def _is_contact_like_form(descriptor: FormDescriptor) -> bool:
    action = (descriptor.action or "").lower()
    text = f"{descriptor.heading_text} {descriptor.inner_text}".lower()
    if any(keyword in text for keyword in CONTACT_KEYWORDS):
        return True
    if any(keyword in action for keyword in CONTACT_ACTION_KEYWORDS):
        return True
    if len(descriptor.fields) <= 4 and _has_textarea(descriptor):
        return True
    return False


__all__ = [
    "find_best_registration_form",
    "extract_form_descriptors",
    "attach_orphan_controls_to_form",
    "score_form_candidate",
]
