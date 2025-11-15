"""Fallback planning for generic required fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from .form_models import FieldDescriptor
from .value_assignment import ValuePlan

PLACEHOLDER_TOKENS = {"select", "--", "choose", "please select"}


@dataclass(slots=True)
class GenericPlan:
    descriptor: FieldDescriptor
    plan: ValuePlan


def plan_generic_required_fillers(
    descriptors: Iterable[FieldDescriptor],
    planned_field_names: Iterable[str],
) -> List[GenericPlan]:
    planned = {name.lower() for name in planned_field_names}
    generic_plans: List[GenericPlan] = []

    for descriptor in descriptors:
        field_name = descriptor.canonical_name().lower()
        if field_name in planned:
            continue
        if not _is_fillable(descriptor):
            continue
        if not _is_required(descriptor):
            continue
        plan = _build_generic_plan(descriptor)
        if plan:
            generic_plans.append(GenericPlan(descriptor=descriptor, plan=plan))
            planned.add(field_name)
    return generic_plans


def _is_fillable(descriptor: FieldDescriptor) -> bool:
    input_type = (descriptor.input_type or "").lower()
    if descriptor.tag == "input" and input_type in {"hidden", "submit", "button"}:
        return False
    if descriptor.handle.is_disabled():
        return False
    if not descriptor.handle.is_visible():
        return False
    return True


def _is_required(descriptor: FieldDescriptor) -> bool:
    if descriptor.required:
        return True
    lowered_classes = " ".join(descriptor.classes).lower()
    if any(token in lowered_classes for token in ("required", "is-required", "validate-required")):
        return True
    for label in descriptor.labels:
        if "*" in label:
            return True
    return False


def _build_generic_plan(descriptor: FieldDescriptor) -> Optional[ValuePlan]:
    tag = descriptor.tag.lower()
    input_type = (descriptor.input_type or "text").lower()

    if tag == "select":
        option = _first_valid_option(descriptor)
        if option:
            return ValuePlan(
                value=option.label or option.value,
                strategy="generic_required_select",
                select_option_value=option.value or None,
                select_option_label=option.label or None,
            )
        return None

    if input_type in {"checkbox", "radio"}:
        return ValuePlan(value=True, strategy="generic_required_checkbox")

    if tag == "textarea":
        return ValuePlan(value="Autofilled by registration bot.", strategy="generic_required_textarea")

    if input_type == "number":
        return ValuePlan(value="1234", strategy="generic_required_number")

    if input_type == "tel":
        return ValuePlan(value="1234567890", strategy="generic_required_tel")

    return ValuePlan(value="Autofilled", strategy="generic_required_text")


def _first_valid_option(descriptor: FieldDescriptor):
    for option in descriptor.options:
        label = (option.label or "").strip()
        value = (option.value or "").strip()
        if not label and not value:
            continue
        if _is_placeholder(label) or _is_placeholder(value):
            continue
        return option
    return descriptor.options[0] if descriptor.options else None


def _is_placeholder(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in PLACEHOLDER_TOKENS)


__all__ = ["GenericPlan", "plan_generic_required_fillers"]
