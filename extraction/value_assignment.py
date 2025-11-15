"""Assign values to classified registration fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .field_classifier import FieldClassification, FieldSemantic
from .form_models import FieldDescriptor, OptionMetadata

DEFAULT_FULL_NAME = "John Doe"
DEFAULT_FIRST_NAME = "John"
DEFAULT_LAST_NAME = "Doe"
DEFAULT_PHONE = "+447911123456"
DEFAULT_COUNTRY = "United Kingdom"
DEFAULT_COUNTRY_FALLBACK = "United States"
DEFAULT_CURRENCY = "USD"
GENERIC_PLACEHOLDER = "autofilled"

SEMANTIC_LIMITS = {
    FieldSemantic.EMAIL: 1,
    FieldSemantic.PASSWORD: 1,
    FieldSemantic.PASSWORD_CONFIRM: 1,
    FieldSemantic.USERNAME: 1,
    FieldSemantic.FULL_NAME: 1,
    FieldSemantic.FIRST_NAME: 1,
    FieldSemantic.LAST_NAME: 1,
    FieldSemantic.PHONE: 1,
    FieldSemantic.COUNTRY: 1,
    FieldSemantic.CURRENCY: 1,
    FieldSemantic.REFERRAL: 1,
    FieldSemantic.TERMS: 1,
    FieldSemantic.GENERIC_TEXT: 3,
    FieldSemantic.UNKNOWN: 2,
}


@dataclass(slots=True)
class RegistrationContext:
    email: str
    password: str
    run_id: str


@dataclass(slots=True)
class ValuePlan:
    value: str | bool
    strategy: str
    select_option_value: Optional[str] = None
    select_option_label: Optional[str] = None


@dataclass(slots=True)
class FieldAssignment:
    descriptor: FieldDescriptor
    semantic: FieldSemantic
    plan: ValuePlan
    required: bool
    confidence: float


@dataclass(slots=True)
class FieldDecision:
    descriptor: FieldDescriptor
    semantic: FieldSemantic
    filled: bool
    strategy: Optional[str]
    reason: Optional[str]


def assign_registration_values(
    classifications: Sequence[FieldClassification],
    context: RegistrationContext,
) -> Tuple[List[FieldAssignment], List[FieldDecision]]:
    sorted_fields = sorted(
        classifications,
        key=lambda cls: (
            not cls.descriptor.required,
            -cls.confidence,
            cls.descriptor.order,
        ),
    )
    limits_counter = {semantic: 0 for semantic in SEMANTIC_LIMITS}
    assignments: List[FieldAssignment] = []
    decisions: List[FieldDecision] = []

    for classification in sorted_fields:
        semantic = classification.semantic
        descriptor = classification.descriptor

        if (
            semantic in SEMANTIC_LIMITS
            and limits_counter[semantic] >= SEMANTIC_LIMITS[semantic]
        ):
            decisions.append(
                FieldDecision(
                    descriptor=descriptor,
                    semantic=semantic,
                    filled=False,
                    strategy=None,
                    reason="semantic_already_filled",
                )
            )
            continue

        plan = _plan_value_for_semantic(classification, context)
        if not plan:
            if descriptor.required:
                plan = ValuePlan(
                    value=f"{GENERIC_PLACEHOLDER}-{context.run_id[:4]}",
                    strategy="required_placeholder",
                )
            else:
                decisions.append(
                    FieldDecision(
                        descriptor=descriptor,
                        semantic=semantic,
                        filled=False,
                        strategy=None,
                        reason="optional_skipped",
                    )
                )
                continue

        assignments.append(
            FieldAssignment(
                descriptor=descriptor,
                semantic=semantic,
                plan=plan,
                required=descriptor.required,
                confidence=classification.confidence,
            )
        )
        decisions.append(
            FieldDecision(
                descriptor=descriptor,
                semantic=semantic,
                filled=True,
                strategy=plan.strategy,
                reason=None,
            )
        )
        if semantic in SEMANTIC_LIMITS:
            limits_counter[semantic] += 1

    return assignments, decisions


def _plan_value_for_semantic(
    classification: FieldClassification, context: RegistrationContext
) -> Optional[ValuePlan]:
    semantic = classification.semantic
    descriptor = classification.descriptor

    if semantic == FieldSemantic.EMAIL:
        return ValuePlan(value=context.email, strategy="provided_email")
    if semantic == FieldSemantic.PASSWORD:
        return ValuePlan(value=context.password, strategy="provided_password")
    if semantic == FieldSemantic.PASSWORD_CONFIRM:
        return ValuePlan(value=context.password, strategy="confirm_password")
    if semantic == FieldSemantic.USERNAME:
        return ValuePlan(
            value=_derive_username(context.email, context.run_id),
            strategy="derived_username",
        )
    if semantic == FieldSemantic.FULL_NAME:
        return ValuePlan(value=DEFAULT_FULL_NAME, strategy="default_full_name")
    if semantic == FieldSemantic.FIRST_NAME:
        return ValuePlan(value=DEFAULT_FIRST_NAME, strategy="default_first_name")
    if semantic == FieldSemantic.LAST_NAME:
        return ValuePlan(value=DEFAULT_LAST_NAME, strategy="default_last_name")
    if semantic == FieldSemantic.PHONE:
        return ValuePlan(value=DEFAULT_PHONE, strategy="default_phone")
    if semantic == FieldSemantic.COUNTRY:
        option = _select_option(
            descriptor.options, [DEFAULT_COUNTRY, DEFAULT_COUNTRY_FALLBACK]
        )
        if option:
            return ValuePlan(
                value=option.label,
                strategy="preferred_country",
                select_option_value=option.value,
                select_option_label=option.label,
            )
        # fallback to text input
        return ValuePlan(value=DEFAULT_COUNTRY, strategy="default_country_text")
    if semantic == FieldSemantic.CURRENCY:
        option = _select_option(
            descriptor.options, [DEFAULT_CURRENCY, DEFAULT_CURRENCY.lower()]
        )
        if option:
            return ValuePlan(
                value=option.label,
                strategy="preferred_currency",
                select_option_value=option.value,
                select_option_label=option.label,
            )
        return ValuePlan(value=DEFAULT_CURRENCY, strategy="default_currency_text")
    if semantic == FieldSemantic.REFERRAL:
        if descriptor.required:
            return ValuePlan(value="N/A", strategy="required_referral_placeholder")
        return None
    if semantic == FieldSemantic.TERMS:
        return ValuePlan(value=True, strategy="accept_terms")
    if semantic == FieldSemantic.GENERIC_TEXT:
        return ValuePlan(value=f"user-{context.run_id[:6]}", strategy="generic_text")
    if semantic == FieldSemantic.UNKNOWN and descriptor.required:
        return ValuePlan(
            value=f"{GENERIC_PLACEHOLDER}-{context.run_id[:4]}",
            strategy="required_unknown",
        )
    return None


def _derive_username(email: str, run_id: str) -> str:
    local = email.split("@", 1)[0]
    slug = re.sub(r"[^a-zA-Z0-9]+", "", local)[:10]
    suffix = run_id.replace("-", "")[:6]
    if not slug:
        slug = "user"
    return f"{slug}{suffix}"[:20]


def _select_option(
    options: Sequence[OptionMetadata], preferred: Sequence[str]
) -> Optional[OptionMetadata]:
    if not options:
        return None
    lowered = {opt.label.lower(): opt for opt in options if opt.label}
    for choice in preferred:
        candidate = lowered.get(choice.lower())
        if candidate:
            return candidate
    # fallback: choose first meaningful option
    for opt in options:
        if opt.value:
            return opt
    return options[0]


__all__ = [
    "RegistrationContext",
    "FieldAssignment",
    "FieldDecision",
    "assign_registration_values",
]
