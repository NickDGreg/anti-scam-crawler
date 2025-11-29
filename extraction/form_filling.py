"""Apply prepared assignments to DOM elements."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

from .field_classifier import FieldSemantic
from .form_models import FieldDescriptor
from .value_assignment import FieldAssignment


@dataclass(slots=True)
class FieldFillResult:
    semantic: FieldSemantic
    field_name: str
    strategy: str
    required: bool
    success: bool
    preview: str
    error: str | None = None


SENSITIVE_SEMANTICS = {
    FieldSemantic.EMAIL,
    FieldSemantic.PASSWORD,
    FieldSemantic.PASSWORD_CONFIRM,
}


def apply_assignments(
    assignments: List[FieldAssignment], logger: logging.Logger
) -> List[FieldFillResult]:
    results: List[FieldFillResult] = []
    for assignment in assignments:
        descriptor = assignment.descriptor
        field_name = descriptor.canonical_name()
        preview = _mask_value(assignment.semantic, assignment.plan.value)
        try:
            _fill_control(descriptor, assignment, logger)
            logger.debug(
                "Filled %s (%s) with strategy %s",
                field_name,
                assignment.semantic.value,
                assignment.plan.strategy,
            )
            results.append(
                FieldFillResult(
                    semantic=assignment.semantic,
                    field_name=field_name,
                    strategy=assignment.plan.strategy,
                    required=assignment.required,
                    success=True,
                    preview=preview,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fill %s: %s", field_name, exc)
            results.append(
                FieldFillResult(
                    semantic=assignment.semantic,
                    field_name=field_name,
                    strategy=assignment.plan.strategy,
                    required=assignment.required,
                    success=False,
                    preview=preview,
                    error=str(exc),
                )
            )
    return results


def _fill_control(
    descriptor: FieldDescriptor, assignment: FieldAssignment, logger: logging.Logger
) -> None:
    handle = descriptor.handle
    value = assignment.plan.value

    if descriptor.tag == "select":
        options = {}
        if assignment.plan.select_option_value:
            options["value"] = assignment.plan.select_option_value
        elif assignment.plan.select_option_label:
            options["label"] = assignment.plan.select_option_label
        else:
            options["index"] = 0
        handle.select_option(**options)
        return

    input_type = (descriptor.input_type or "text").lower()
    if input_type == "checkbox":
        should_check = bool(value)
        before_checked = handle.is_checked()
        before_js_checked = handle.evaluate("el => el.checked")
        logger.debug(
            "Checkbox %s before: is_checked=%s js_checked=%s",
            descriptor.canonical_name(),
            before_checked,
            before_js_checked,
        )
        if should_check and not before_checked:
            try:
                handle.check()
            except Exception:
                handle.evaluate(
                    """
                    el => {
                        el.checked = true;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    """
                )
        elif not should_check and before_checked:
            try:
                handle.uncheck()
            except Exception:
                handle.evaluate(
                    """
                    el => {
                        el.checked = false;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    """
                )
        after_checked = handle.is_checked()
        after_js_checked = handle.evaluate("el => el.checked")
        logger.debug(
            "Checkbox %s after: is_checked=%s js_checked=%s",
            descriptor.canonical_name(),
            after_checked,
            after_js_checked,
        )
        return

    if not handle.is_editable():
        if assignment.required:
            raise ValueError("Field not editable")
        return

    handle.click()
    handle.fill(str(value))


def _mask_value(semantic: FieldSemantic, value: str | bool) -> str:
    if semantic in SENSITIVE_SEMANTICS:
        return "***"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if len(text) > 18:
        return f"{text[:8]}…"
    return text


__all__ = ["apply_assignments", "FieldFillResult"]
