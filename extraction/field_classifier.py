"""Field classification heuristics for registration forms."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List

from .form_models import FieldDescriptor


class FieldSemantic(str, Enum):
    EMAIL = "email"
    PASSWORD = "password"
    PASSWORD_CONFIRM = "password_confirm"
    USERNAME = "username"
    FULL_NAME = "full_name"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    PHONE = "phone"
    COUNTRY = "country"
    CURRENCY = "currency"
    GENDER = "gender"
    REFERRAL = "referral"
    TERMS = "terms_checkbox"
    GENERIC_TEXT = "generic_text"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class FieldClassification:
    descriptor: FieldDescriptor
    semantic: FieldSemantic
    scores: Dict[FieldSemantic, float]
    confidence: float


EMAIL_KEYWORDS = ["email", "e-mail", "mail"]
PASSWORD_KEYWORDS = ["password", "pass", "pwd", "passcode"]
CONFIRM_KEYWORDS = ["confirm", "repeat", "retype", "again", "verify"]
USERNAME_KEYWORDS = [
    "username",
    "user name",
    "user",
    "login",
    "loginid",
    "userid",
    "nick",
]
FULLNAME_KEYWORDS = ["full name", "fullname", "full-name", "your name"]
FIRST_NAME_KEYWORDS = ["first name", "firstname", "given name"]
LAST_NAME_KEYWORDS = ["last name", "lastname", "surname", "family name"]
PHONE_KEYWORDS = ["phone", "mobile", "telephone", "tel", "cell"]
COUNTRY_KEYWORDS = ["country", "nation"]
CURRENCY_KEYWORDS = ["currency", "account currency", "usd", "eur", "gbp"]
REFERRAL_KEYWORDS = ["referral", "promo", "code", "coupon", "partner"]
TERMS_KEYWORDS = ["terms", "conditions", "privacy", "agreement", "policy", "consent"]
ADDRESS_KEYWORDS = ["address", "street", "city", "zip", "postal"]
GENDER_KEYWORDS = ["gender", "sex"]

COUNTRY_SAMPLE = {
    "united states",
    "united kingdom",
    "germany",
    "france",
    "spain",
    "italy",
    "canada",
    "australia",
    "austria",
    "switzerland",
    "sweden",
    "norway",
    "ireland",
    "poland",
    "romania",
    "hungary",
    "portugal",
    "netherlands",
    "belgium",
    "greece",
    "latvia",
    "lithuania",
    "estonia",
    "cyprus",
    "malta",
    "bulgaria",
    "czech",
    "slovakia",
    "slovenia",
    "croatia",
    "serbia",
    "turkey",
    "russia",
    "ukraine",
    "belarus",
    "mexico",
    "brazil",
    "argentina",
    "chile",
    "peru",
    "china",
    "japan",
    "singapore",
    "hong kong",
    "india",
    "pakistan",
    "uae",
    "south africa",
    "nigeria",
    "kenya",
}

CURRENCY_CODES = {
    "usd",
    "eur",
    "gbp",
    "aud",
    "cad",
    "chf",
    "nzd",
    "jpy",
    "cny",
    "usdt",
    "btc",
    "eth",
}


def classify_field(field: FieldDescriptor) -> FieldClassification:
    scores: Dict[FieldSemantic, float] = defaultdict(float)
    tokens = _collect_tokens(field)
    tag = field.tag.lower()
    input_type = (field.input_type or "").lower()

    _apply_keyword_scores(
        tokens, EMAIL_KEYWORDS, FieldSemantic.EMAIL, scores, weight=1.5
    )
    if input_type == "email":
        scores[FieldSemantic.EMAIL] += 2.5

    _apply_keyword_scores(
        tokens, PASSWORD_KEYWORDS, FieldSemantic.PASSWORD, scores, weight=1.2
    )
    if input_type == "password":
        scores[FieldSemantic.PASSWORD] += 3
    if _contains_any(tokens, CONFIRM_KEYWORDS):
        scores[FieldSemantic.PASSWORD_CONFIRM] += 3
        if FieldSemantic.PASSWORD in scores:
            scores[FieldSemantic.PASSWORD_CONFIRM] += (
                scores[FieldSemantic.PASSWORD] * 0.5
            )

    _apply_keyword_scores(
        tokens, USERNAME_KEYWORDS, FieldSemantic.USERNAME, scores, weight=1.1
    )
    if input_type == "text" and "user" in tokens:
        scores[FieldSemantic.USERNAME] += 0.3
    if re.search(r"\bname\b", tokens) and not re.search(r"\buser(name)?\b", tokens):
        scores[FieldSemantic.FULL_NAME] += 1.0

    _apply_keyword_scores(tokens, FULLNAME_KEYWORDS, FieldSemantic.FULL_NAME, scores)
    _apply_keyword_scores(tokens, FIRST_NAME_KEYWORDS, FieldSemantic.FIRST_NAME, scores)
    _apply_keyword_scores(tokens, LAST_NAME_KEYWORDS, FieldSemantic.LAST_NAME, scores)

    _apply_keyword_scores(
        tokens, PHONE_KEYWORDS, FieldSemantic.PHONE, scores, weight=1.3
    )
    if input_type in {"tel", "number"}:
        scores[FieldSemantic.PHONE] += 1.5

    if _looks_like_country_field(field, tokens):
        scores[FieldSemantic.COUNTRY] += 2.5
    _apply_keyword_scores(tokens, COUNTRY_KEYWORDS, FieldSemantic.COUNTRY, scores)

    if _looks_like_currency_field(field):
        scores[FieldSemantic.CURRENCY] += 2.5
    _apply_keyword_scores(
        tokens, CURRENCY_KEYWORDS, FieldSemantic.CURRENCY, scores, weight=0.8
    )

    _apply_keyword_scores(
        tokens, GENDER_KEYWORDS, FieldSemantic.GENDER, scores, weight=1.5
    )
    if field.tag == "select" and _has_gender_options(field):
        scores[FieldSemantic.GENDER] += 2.0

    _apply_keyword_scores(
        tokens, REFERRAL_KEYWORDS, FieldSemantic.REFERRAL, scores, weight=0.8
    )

    if input_type == "checkbox":
        if _contains_any(tokens, TERMS_KEYWORDS):
            scores[FieldSemantic.TERMS] += 3
        else:
            # keep checkbox but with low certainty
            scores[FieldSemantic.TERMS] += 0.2

    # generic fallback for visible text inputs
    if tag in {"input", "textarea"} and input_type not in {
        "checkbox",
        "radio",
        "submit",
        "button",
        "hidden",
        "file",
    }:
        scores[FieldSemantic.GENERIC_TEXT] += 0.2

    semantic = FieldSemantic.UNKNOWN
    confidence = 0.0
    if scores:
        semantic = max(scores, key=scores.get)
        confidence = scores[semantic]

    return FieldClassification(
        descriptor=field,
        semantic=semantic,
        scores=dict(scores),
        confidence=confidence,
    )


def _collect_tokens(field: FieldDescriptor) -> str:
    pieces: List[str] = []
    for value in (
        field.name,
        field.identifier,
        field.placeholder,
        field.aria_label,
        " ".join(field.labels),
        field.surrounding_text,
    ):
        if value:
            pieces.append(value)
    if field.dataset:
        pieces.extend(field.dataset.values())
    return " ".join(pieces).lower()


def _apply_keyword_scores(
    text: str,
    keywords: Iterable[str],
    semantic: FieldSemantic,
    scores: Dict[FieldSemantic, float],
    *,
    weight: float = 1.0,
) -> None:
    for keyword in keywords:
        if keyword in text:
            scores[semantic] += weight


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _looks_like_country_field(field: FieldDescriptor, tokens: str) -> bool:
    if field.tag == "select":
        labels = {opt.label.lower() for opt in field.options}
        matches = labels & COUNTRY_SAMPLE
        if len(matches) >= 3:
            return True
    if "country" in tokens:
        return True
    return False


def _looks_like_currency_field(field: FieldDescriptor) -> bool:
    if field.tag == "select":
        labels = {opt.label.lower() for opt in field.options}
        values = {opt.value.lower() for opt in field.options}
        if labels & CURRENCY_CODES or values & CURRENCY_CODES:
            return True
    if field.input_type == "text" and field.placeholder:
        if any(code in field.placeholder.lower() for code in CURRENCY_CODES):
            return True
    return False


def _has_gender_options(field: FieldDescriptor) -> bool:
    labels = {opt.label.lower() for opt in field.options if opt.label}
    candidates = {"male", "female", "other", "others"}
    return bool(labels & candidates)


__all__ = [
    "FieldSemantic",
    "FieldClassification",
    "classify_field",
]
