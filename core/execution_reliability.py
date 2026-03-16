from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.choices import ExecutionJobOperation, ExecutionJobStatus

FAILURE_CATEGORY_VALIDATION = "validation"
FAILURE_CATEGORY_POLICY = "policy"
FAILURE_CATEGORY_DEPENDENCY = "dependency"
FAILURE_CATEGORY_TIMEOUT = "timeout"
FAILURE_CATEGORY_QUOTA = "quota"
FAILURE_CATEGORY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureSpec:
    category: str
    retryable: bool
    fix_required: bool
    remediation_hints: tuple[str, ...]
    next_actions: tuple[str, ...]


FAILURE_SPECS: dict[str, FailureSpec] = {
    "MISSING_IDEMPOTENCY_KEY": FailureSpec(
        category=FAILURE_CATEGORY_VALIDATION,
        retryable=False,
        fix_required=True,
        remediation_hints=(
            "Send an Idempotency-Key request header when creating or retrying execution jobs.",
        ),
        next_actions=("provide_idempotency_key", "retry_request"),
    ),
    "INVALID_OPERATION": FailureSpec(
        category=FAILURE_CATEGORY_VALIDATION,
        retryable=False,
        fix_required=True,
        remediation_hints=("Use a supported operation value.",),
        next_actions=("fix_request_payload",),
    ),
    "MISSING_TITLE_SUGGESTION_ID": FailureSpec(
        category=FAILURE_CATEGORY_VALIDATION,
        retryable=False,
        fix_required=True,
        remediation_hints=("Provide title_suggestion_id for GENERATE_BLOG_POST.",),
        next_actions=("fix_request_payload",),
    ),
    "TITLE_SUGGESTION_NOT_FOUND": FailureSpec(
        category=FAILURE_CATEGORY_VALIDATION,
        retryable=False,
        fix_required=True,
        remediation_hints=("Use a title suggestion that belongs to this project.",),
        next_actions=("list_title_suggestions", "retry_with_valid_suggestion"),
    ),
    "IDEMPOTENCY_KEY_CONFLICT": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=False,
        fix_required=True,
        remediation_hints=("Reuse idempotency keys only for equivalent requests.",),
        next_actions=("use_new_idempotency_key",),
    ),
    "JOB_CREATION_FAILED": FailureSpec(
        category=FAILURE_CATEGORY_DEPENDENCY,
        retryable=True,
        fix_required=False,
        remediation_hints=("Transient persistence issue while creating the job.",),
        next_actions=("retry_with_new_idempotency_key",),
    ),
    "JOB_NOT_FOUND": FailureSpec(
        category=FAILURE_CATEGORY_VALIDATION,
        retryable=False,
        fix_required=True,
        remediation_hints=("Ensure the execution job exists in this account scope.",),
        next_actions=("list_execution_jobs",),
    ),
    "JOB_ALREADY_TERMINAL": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=False,
        fix_required=True,
        remediation_hints=("Terminal jobs cannot be canceled.",),
        next_actions=("inspect_job_status", "retry_if_failed"),
    ),
    "RETRY_NOT_ALLOWED": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=False,
        fix_required=True,
        remediation_hints=("Only failed or canceled jobs are retryable.",),
        next_actions=("wait_for_terminal_status",),
    ),
    "UNSUPPORTED_OPERATION": FailureSpec(
        category=FAILURE_CATEGORY_VALIDATION,
        retryable=False,
        fix_required=True,
        remediation_hints=("Operation is not implemented by the execution worker.",),
        next_actions=("use_supported_operation",),
    ),
    "EXECUTION_FAILED": FailureSpec(
        category=FAILURE_CATEGORY_UNKNOWN,
        retryable=True,
        fix_required=False,
        remediation_hints=("Inspect error_message and retry. Escalate if it repeats.",),
        next_actions=("retry_job", "inspect_job_history"),
    ),
    "CANCELED": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=True,
        fix_required=False,
        remediation_hints=("Job was canceled by a caller.",),
        next_actions=("retry_job",),
    ),
    "PUBLISH_APPROVAL_PENDING": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=False,
        fix_required=True,
        remediation_hints=("Approve the publish checkpoint before calling publish.",),
        next_actions=("review_publish_checkpoint", "retry_publish"),
    ),
    "PUBLISH_QUALITY_GATE_BLOCKED": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=False,
        fix_required=True,
        remediation_hints=("Address blocking quality checks before publishing.",),
        next_actions=("update_post_content", "retry_publish"),
    ),
    "PUBLISH_ENDPOINT_FAILED": FailureSpec(
        category=FAILURE_CATEGORY_DEPENDENCY,
        retryable=True,
        fix_required=False,
        remediation_hints=("Publishing endpoint failed. Verify endpoint health and credentials.",),
        next_actions=("verify_submission_endpoint", "retry_publish"),
    ),
    "ROLLBACK_NOT_ALLOWED": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=False,
        fix_required=True,
        remediation_hints=("Rollback is only available for succeeded jobs.",),
        next_actions=("inspect_job_status",),
    ),
    "ROLLBACK_NOT_SUPPORTED": FailureSpec(
        category=FAILURE_CATEGORY_VALIDATION,
        retryable=False,
        fix_required=True,
        remediation_hints=("Rollback is not implemented for this operation.",),
        next_actions=("use_supported_rollback_operation",),
    ),
    "ROLLBACK_CONTEXT_MISSING": FailureSpec(
        category=FAILURE_CATEGORY_UNKNOWN,
        retryable=False,
        fix_required=True,
        remediation_hints=("Rollback metadata is missing from this job result.",),
        next_actions=("inspect_job_history", "open_support_ticket"),
    ),
    "ROLLBACK_REQUIRES_MANUAL_UNPUBLISH": FailureSpec(
        category=FAILURE_CATEGORY_POLICY,
        retryable=False,
        fix_required=True,
        remediation_hints=(
            "Generated post is already published to an external endpoint and needs manual revert.",
        ),
        next_actions=("manually_unpublish_external_post",),
    ),
}


def _default_spec_for_category(category: str) -> FailureSpec:
    if category == FAILURE_CATEGORY_TIMEOUT:
        return FailureSpec(
            category=category,
            retryable=True,
            fix_required=False,
            remediation_hints=("Operation timed out. Safe to retry with backoff.",),
            next_actions=("retry_with_backoff",),
        )
    if category == FAILURE_CATEGORY_QUOTA:
        return FailureSpec(
            category=category,
            retryable=False,
            fix_required=True,
            remediation_hints=("Quota limit reached. Wait for reset or upgrade plan.",),
            next_actions=("wait_for_quota_reset", "upgrade_plan"),
        )
    return FailureSpec(
        category=category,
        retryable=False,
        fix_required=True,
        remediation_hints=("Inspect failure details and fix request or dependencies.",),
        next_actions=("inspect_job_history",),
    )


def build_failure_payload(
    code: str,
    message: str,
    *,
    category: str | None = None,
    retryable: bool | None = None,
    fix_required: bool | None = None,
    remediation_hints: list[str] | None = None,
    next_actions: list[str] | None = None,
    retry_after_seconds: int | None = None,
) -> dict[str, Any]:
    spec = FAILURE_SPECS.get(code)
    if spec is None:
        spec = _default_spec_for_category(category or FAILURE_CATEGORY_UNKNOWN)

    payload: dict[str, Any] = {
        "taxonomy_version": "v1",
        "category": category or spec.category,
        "code": code,
        "message": message,
        "retryable": spec.retryable if retryable is None else retryable,
        "fix_required": spec.fix_required if fix_required is None else fix_required,
        "remediation_hints": remediation_hints or list(spec.remediation_hints),
        "next_actions": next_actions or list(spec.next_actions),
    }
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = retry_after_seconds
    return payload


def error_response(code: str, message: str, *, status_code: int) -> tuple[int, dict[str, Any]]:
    return (
        status_code,
        {
            "status": "error",
            "code": code,
            "message": message,
            "failure": build_failure_payload(code, message),
        },
    )


def append_job_history(
    result: dict[str, Any] | None,
    *,
    event: str,
    status: str,
    details: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    enriched = deepcopy(result) if isinstance(result, dict) else {}
    history = enriched.get("history")
    if not isinstance(history, list):
        history = []

    history.append(
        {
            "event": event,
            "status": status.lower(),
            "at": (at or datetime.utcnow()).isoformat(),
            "details": details or {},
        }
    )

    enriched["history"] = history
    return enriched


def get_job_failure_payload(job) -> dict[str, Any] | None:
    result = job.result or {}
    failure = result.get("failure") if isinstance(result, dict) else None
    if isinstance(failure, dict):
        return failure

    if job.error_code:
        return build_failure_payload(job.error_code, job.error_message or job.error_code)

    return None


def get_job_rollback_hook(job) -> dict[str, Any]:
    result = job.result or {}
    rollback = result.get("rollback") if isinstance(result, dict) else None
    if isinstance(rollback, dict):
        return rollback

    if job.operation == ExecutionJobOperation.GENERATE_BLOG_POST:
        available = bool((job.result or {}).get("blog_post_id")) and job.status == ExecutionJobStatus.SUCCEEDED
        return {
            "supported": True,
            "state": "available" if available else "unavailable",
            "hook": f"/public-api/executions/{job.id}/rollback",
            "summary": "Reverts generated draft blog post for this execution when possible.",
        }

    return {
        "supported": False,
        "state": "unavailable",
        "hook": None,
        "summary": "Rollback hook not implemented for this operation.",
    }
