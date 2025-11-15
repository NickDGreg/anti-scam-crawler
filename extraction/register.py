"""Registration workflow orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .automation import KEYWORD_CLICKS, click_keywords, submit_form_element
from .browser import BrowserConfig, BrowserSession
from .field_classifier import FieldClassification
from .form_detection import find_best_registration_form
from .form_filling import FieldFillResult, apply_assignments
from .form_models import FormDescriptor
from .io_utils import RunPaths, relative_artifact_path
from .value_assignment import (
    FieldDecision,
    RegistrationContext,
    assign_registration_values,
)

DEFAULT_PASSWORD = "AntiScam!234"


@dataclass(slots=True)
class RegisterInputs:
    url: str
    email: str
    password: str
    run_paths: RunPaths
    logger: logging.Logger


def run_registration(inputs: RegisterInputs) -> Dict[str, object]:
    logger = inputs.logger
    run_paths = inputs.run_paths
    artifacts: List[str] = []
    notes: List[str] = []
    status = "failed"
    final_url = inputs.url
    filled_field_report: List[Dict[str, str]] = []
    resolved_password = inputs.password or DEFAULT_PASSWORD

    try:
        logger.debug("Starting registration against %s as %s", inputs.url, inputs.email)
        with BrowserSession(BrowserConfig()) as browser:
            page = browser.goto(inputs.url, wait_until="networkidle")
            final_url = page.url
            logger.debug("Loaded landing page %s", final_url)
            landing_shot = browser.screenshot(run_paths.build_path("00_landing.png"))
            artifacts.append(relative_artifact_path(landing_shot))

            form_descriptor, classifications = _discover_registration_form(page, logger)
            if not form_descriptor:
                status = "no_form_found"
                notes.append(
                    "Could not identify a registration form after heuristic navigation."
                )
                logger.warning("Registration form still missing after heuristics")
            else:
                _log_field_classifications(classifications, logger)
                context = RegistrationContext(
                    email=inputs.email,
                    password=resolved_password,
                    run_id=run_paths.run_id,
                )
                assignments, decisions = assign_registration_values(
                    classifications, context
                )
                _log_decisions(decisions, logger)

                if not assignments:
                    status = "no_fillable_fields"
                    notes.append("No suitable fields could be auto-filled.")
                    logger.warning("No assignments generated for registration form")
                else:
                    fill_results = apply_assignments(assignments, logger)
                    filled_field_report = _serialize_fill_results(fill_results)

                    filled_html = browser.save_html(
                        run_paths.build_path("01_filled.html")
                    )
                    artifacts.append(relative_artifact_path(filled_html))
                    filled_shot = browser.screenshot(
                        run_paths.build_path("01_filled.png")
                    )
                    artifacts.append(relative_artifact_path(filled_shot))

                    logger.debug("Submitting registration form element")
                    submit_form_element(form_descriptor.element, logger=logger)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except PlaywrightTimeoutError:
                        logger.debug(
                            "Registration submission did not trigger navigation within timeout"
                        )
                    final_url = page.url
                    logger.debug("Post-submission URL: %s", final_url)
                    status = "submitted"
                    notes.append(
                        f"Registration form submitted with {len(filled_field_report)} filled fields."
                    )

            html_path = browser.save_html(run_paths.build_path("final.html"))
            artifacts.append(relative_artifact_path(html_path))
            screenshot_path = browser.screenshot(run_paths.build_path("02_final.png"))
            artifacts.append(relative_artifact_path(screenshot_path))

    except Exception as exc:  # noqa: BLE001
        logger.exception("Registration command failed: %s", exc)
        notes.append(str(exc))
        status = "error"

    result = {
        "run_id": run_paths.run_id,
        "input_url": inputs.url,
        "final_url": final_url,
        "status": status,
        "notes": " | ".join(notes) if notes else "",
        "artifacts": artifacts,
        "filled_fields": filled_field_report,
    }
    return result


def _discover_registration_form(
    page: Page, logger: logging.Logger
) -> Tuple[Optional[FormDescriptor], List[FieldClassification]]:
    form_descriptor, classifications = find_best_registration_form(page, logger)
    if form_descriptor:
        return form_descriptor, classifications

    logger.debug("Registration form not detected; clicking navigation heuristics")
    click_keywords(page, KEYWORD_CLICKS, max_clicks=4, logger=logger)
    return find_best_registration_form(page, logger)


def _log_field_classifications(
    classifications: List[FieldClassification], logger: logging.Logger
) -> None:
    for classification in classifications:
        descriptor = classification.descriptor
        logger.debug(
            "Field #%s '%s' -> %s (required=%s, confidence=%.2f)",
            descriptor.order,
            descriptor.canonical_name(),
            classification.semantic.value,
            descriptor.required,
            classification.confidence,
        )


def _log_decisions(decisions: List[FieldDecision], logger: logging.Logger) -> None:
    for decision in decisions:
        descriptor = decision.descriptor
        if decision.filled:
            logger.debug(
                "Plan: %s (%s) via %s",
                descriptor.canonical_name(),
                decision.semantic.value,
                decision.strategy,
            )
        else:
            logger.debug(
                "Skipped: %s (%s) reason=%s",
                descriptor.canonical_name(),
                decision.semantic.value,
                decision.reason,
            )


def _serialize_fill_results(results: List[FieldFillResult]) -> List[Dict[str, str]]:
    report: List[Dict[str, str]] = []
    for result in results:
        report.append(
            {
                "semantic": result.semantic.value,
                "field_name": result.field_name,
                "strategy": result.strategy,
                "status": "filled" if result.success else "error",
                "required": str(result.required),
                "preview": result.preview,
            }
        )
    return report
