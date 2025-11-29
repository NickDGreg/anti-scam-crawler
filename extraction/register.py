"""Registration workflow orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .auth_navigation import NavigationConfig, discover_form_with_navigation
from .automation import KEYWORD_CLICKS, submit_form_element
from .browser import BrowserConfig, BrowserSession
from .field_classifier import FieldClassification
from .field_errors import FieldError, extract_field_errors, interpret_field_error
from .form_detection import find_best_registration_form
from .form_filling import FieldFillResult, apply_assignments
from .form_models import FormDescriptor
from .generic_planner import plan_generic_required_fillers
from .io_utils import RunPaths, relative_artifact_path, write_json
from .network_capture import NetworkCapture
from .registration_evaluator import evaluate_registration_result
from .value_assignment import (
    FieldAssignment,
    FieldDecision,
    FieldSemantic,
    RegistrationContext,
    ValuePlan,
    adjust_value_for_retry,
    assign_registration_values,
)

DEFAULT_PASSWORD = "AntiScam!234"
REG_NAVIGATION = NavigationConfig(
    primary_keywords=(
        "register",
        "sign up",
        "signup",
        "create account",
        "open account",
        "get started",
    ),
    secondary_keywords=(
        "join",
        "start now",
        "start trading",
        "log in",
        "login",
        "sign in",
    ),
    max_depth=2,
    max_candidates=8,
    max_visits=8,
    fallback_keywords=KEYWORD_CLICKS,
    fallback_clicks=4,
)


@dataclass(slots=True)
class RegisterInputs:
    url: str
    email: str
    password: str
    run_paths: RunPaths
    logger: logging.Logger


@dataclass(slots=True)
class AttemptResult:
    status: str
    filled_fields: List[Dict[str, str]]
    field_errors: List[FieldError]
    validation_message: Optional[str]
    success_message: Optional[str]
    assignment_values: Dict[str, str]
    artifacts: List[str]
    field_errors_path: Optional[str]
    notes: List[str]
    final_url: str


def run_registration(inputs: RegisterInputs) -> Dict[str, object]:
    logger = inputs.logger
    run_paths = inputs.run_paths
    artifacts: List[str] = []
    notes: List[str] = []
    status = "failed"
    final_url = inputs.url
    filled_field_report: List[Dict[str, str]] = []
    resolved_password = inputs.password or DEFAULT_PASSWORD
    validation_message: Optional[str] = None
    success_message: Optional[str] = None
    network_artifacts: List[str] = []
    field_error_summary: List[Dict[str, str]] = []
    last_attempt_result: Optional[AttemptResult] = None

    attempts_taken = 0
    adjustments_log: List[Dict[str, str]] = []

    try:
        logger.debug("Starting registration against %s as %s", inputs.url, inputs.email)
        with BrowserSession(BrowserConfig()) as browser:
            page = browser.goto(inputs.url, wait_until="networkidle")
            final_url = page.url
            logger.debug("Loaded landing page %s", final_url)
            landing_shot = browser.screenshot(run_paths.build_path("00_landing.png"))
            artifacts.append(relative_artifact_path(landing_shot))

            with NetworkCapture(page) as network_logger:
                context = RegistrationContext(
                    email=inputs.email,
                    password=resolved_password,
                    run_id=run_paths.run_id,
                )
                adjustments: Dict[str, ValuePlan] = {}

                for attempt_no in (1, 2):
                    result = _perform_attempt(
                        page=page,
                        run_paths=run_paths,
                        attempt_no=attempt_no,
                        logger=logger,
                        context=context,
                        adjustments=adjustments if attempt_no == 2 else {},
                    )
                    attempts_taken = attempt_no
                    artifacts.extend(result.artifacts)
                    notes.extend(result.notes)
                    filled_field_report = result.filled_fields or filled_field_report
                    validation_message = result.validation_message or validation_message
                    success_message = result.success_message or success_message
                    status = result.status
                    final_url = result.final_url or final_url
                    last_attempt_result = result

                    if status == "registered":
                        break

                    if attempt_no == 1 and status == "validation_failed":
                        adjustments, adj_log = _prepare_adjustments(
                            result.field_errors, result.assignment_values, logger
                        )
                        if adjustments:
                            adjustments_log.extend(adj_log)
                            logger.info(
                                "Retrying registration with %d adjustments",
                                len(adjustments),
                            )
                            continue
                        break
                    else:
                        break

                if last_attempt_result:
                    field_error_summary = _serialize_field_errors(
                        last_attempt_result.field_errors
                    )

                network_path = network_logger.dump(run_paths.build_path("network.json"))
                if network_path:
                    network_artifacts.append(relative_artifact_path(network_path))

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
        "validation_message": validation_message,
        "success_message": success_message,
        "network_artifacts": network_artifacts,
        "attempts": attempts_taken or 0,
        "field_errors": field_error_summary,
        "adjustments_made": adjustments_log,
    }
    return result


def _discover_registration_form(
    page: Page, logger: logging.Logger
) -> Tuple[Optional[FormDescriptor], List[FieldClassification]]:
    return discover_form_with_navigation(
        page,
        detect_form=lambda current_page: find_best_registration_form(
            current_page, logger
        ),
        is_valid_form=lambda descriptor, classes: not _is_weak_registration_candidate(
            descriptor, classes
        ),
        config=REG_NAVIGATION,
        logger=logger,
    )


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
        entry = {
            "semantic": result.semantic.value,
            "field_name": result.field_name,
            "strategy": result.strategy,
            "status": "filled" if result.success else "error",
            "required": str(result.required),
            "preview": result.preview,
        }
        if result.error:
            entry["error"] = result.error
        report.append(entry)
    return report


def _perform_attempt(
    page: Page,
    run_paths: RunPaths,
    attempt_no: int,
    logger: logging.Logger,
    context: RegistrationContext,
    adjustments: Dict[str, "ValuePlan"],
) -> AttemptResult:
    artifacts: List[str] = []
    notes: List[str] = []
    filled_fields: List[Dict[str, str]] = []
    assignment_values: Dict[str, str] = {}
    field_errors: List[FieldError] = []
    validation_message: Optional[str] = None
    success_message: Optional[str] = None
    field_errors_path: Optional[str] = None

    form_descriptor, classifications = _discover_registration_form(page, logger)
    if not form_descriptor:
        notes.append("Registration form could not be located during attempt.")
        return AttemptResult(
            status="validation_failed",
            filled_fields=filled_fields,
            field_errors=field_errors,
            validation_message=None,
            success_message=None,
            assignment_values=assignment_values,
            artifacts=artifacts,
            field_errors_path=None,
            notes=notes,
            final_url=page.url,
        )

    _log_field_classifications(classifications, logger)
    assignments, decisions = assign_registration_values(classifications, context)
    _log_decisions(decisions, logger)

    planned_names = {
        assignment.descriptor.canonical_name() for assignment in assignments
    }
    generic_plans = plan_generic_required_fillers(form_descriptor.fields, planned_names)
    for generic in generic_plans:
        assignments.append(
            FieldAssignment(
                descriptor=generic.descriptor,
                semantic=FieldSemantic.GENERIC_TEXT,
                plan=generic.plan,
                required=True,
                confidence=0.0,
            )
        )
        logger.debug(
            "Adding generic plan for %s via %s",
            generic.descriptor.canonical_name(),
            generic.plan.strategy,
        )

    if not assignments:
        notes.append("No fields were eligible for auto-fill.")
        return AttemptResult(
            status="validation_failed",
            filled_fields=filled_fields,
            field_errors=field_errors,
            validation_message=None,
            success_message=None,
            assignment_values=assignment_values,
            artifacts=artifacts,
            field_errors_path=None,
            notes=notes,
            final_url=page.url,
        )

    if adjustments:
        for assignment in assignments:
            field_name = assignment.descriptor.canonical_name()
            custom_plan = adjustments.get(field_name)
            if custom_plan:
                logger.debug(
                    "Applying retry adjustment to %s (%s)",
                    field_name,
                    assignment.semantic.value,
                )
                assignment.plan = custom_plan

    for assignment in assignments:
        field_name = assignment.descriptor.canonical_name()
        assignment_values[field_name] = str(assignment.plan.value)

    fill_results = apply_assignments(assignments, logger)
    filled_fields = _serialize_fill_results(fill_results)

    filled_html_path = run_paths.build_path(f"01_attempt{attempt_no}_filled.html")
    filled_html_path.write_text(page.content(), encoding="utf-8")
    artifacts.append(relative_artifact_path(filled_html_path))
    filled_shot_path = run_paths.build_path(f"01_attempt{attempt_no}_filled.png")
    page.screenshot(path=str(filled_shot_path), full_page=True)
    artifacts.append(relative_artifact_path(filled_shot_path))

    logger.debug("Submitting registration form element (attempt %s)", attempt_no)
    pre_submit_url = page.url
    submit_form_element(form_descriptor.element, logger=logger)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PlaywrightTimeoutError as exc:
        logger.debug("Submission wait timed out: %s", exc)
    final_url = page.url
    logger.debug("Attempt %s post-submit URL: %s", attempt_no, final_url)

    outcome = evaluate_registration_result(
        page, previous_url=pre_submit_url, logger=logger
    )
    status = outcome.status
    validation_message = outcome.validation_message
    success_message = outcome.success_message
    if validation_message:
        notes.append(validation_message)
    if success_message:
        notes.append(success_message)
    if not validation_message and not success_message:
        notes.append(f"Attempt {attempt_no} submitted {len(filled_fields)} fields.")

    field_errors = extract_field_errors(page, classifications, logger=logger)
    errors_payload = _serialize_field_errors(field_errors)
    errors_path = run_paths.build_path(f"field_errors_attempt{attempt_no}.json")
    write_json(errors_path, errors_payload)
    field_errors_path = relative_artifact_path(errors_path)
    artifacts.append(field_errors_path)

    post_html_path = run_paths.build_path(f"02_attempt{attempt_no}_post_submit.html")
    post_html_path.write_text(page.content(), encoding="utf-8")
    artifacts.append(relative_artifact_path(post_html_path))
    post_shot_path = run_paths.build_path(f"02_attempt{attempt_no}_post_submit.png")
    page.screenshot(path=str(post_shot_path), full_page=True)
    artifacts.append(relative_artifact_path(post_shot_path))

    return AttemptResult(
        status=status,
        filled_fields=filled_fields,
        field_errors=field_errors,
        validation_message=validation_message,
        success_message=success_message,
        assignment_values=assignment_values,
        artifacts=artifacts,
        field_errors_path=field_errors_path,
        notes=notes,
        final_url=final_url,
    )


def _prepare_adjustments(
    field_errors: List[FieldError],
    assignment_values: Dict[str, str],
    logger: logging.Logger,
) -> Tuple[Dict[str, "ValuePlan"], List[Dict[str, str]]]:
    adjustments: Dict[str, ValuePlan] = {}
    logs: List[Dict[str, str]] = []
    for field_error in field_errors:
        interpretation = interpret_field_error(field_error)
        if not interpretation:
            continue
        previous_value = assignment_values.get(field_error.field_name)
        if previous_value is None:
            continue
        plan = adjust_value_for_retry(
            field_error.semantic, previous_value, interpretation.hints
        )
        if not plan:
            continue
        adjustments[field_error.field_name] = plan
        preview = str(plan.value)
        if len(preview) > 18:
            preview = preview[:8] + "…"
        logs.append(
            {
                "field_name": field_error.field_name,
                "semantic": field_error.semantic.value,
                "hints": str(interpretation.hints),
                "strategy": plan.strategy,
                "preview": preview,
            }
        )
        logger.debug(
            "Prepared adjustment for %s (%s): %s",
            field_error.field_name,
            field_error.semantic.value,
            interpretation.hints,
        )
    return adjustments, logs


def _serialize_field_errors(errors: List[FieldError]) -> List[Dict[str, str]]:
    return [
        {
            "field_name": err.field_name,
            "semantic": err.semantic.value,
            "error_text": err.error_text,
        }
        for err in errors
    ]


def _is_weak_registration_candidate(
    descriptor: FormDescriptor, classifications: List[FieldClassification]
) -> bool:
    semantics = {cls.semantic for cls in classifications}
    has_password = FieldSemantic.PASSWORD in semantics
    has_username = FieldSemantic.USERNAME in semantics
    has_password_confirm = FieldSemantic.PASSWORD_CONFIRM in semantics
    has_textarea = any(cls.descriptor.tag == "textarea" for cls in classifications)

    contact_text = (
        f"{descriptor.heading_text} {descriptor.inner_text} {descriptor.action or ''}"
    ).lower()
    contact_hits = any(
        keyword in contact_text for keyword in ("contact", "support", "message", "help")
    )

    if contact_hits and not has_password:
        return True
    if has_textarea and not has_password and len(descriptor.fields) <= 4:
        return True
    if not (has_password or has_username or has_password_confirm):
        return True
    return False
