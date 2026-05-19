from contextlib import contextmanager
import re
from typing import Any
from uuid import uuid4

import structlog

_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@contextmanager
def bind_log_context(**kwargs: Any):
    context = {key: value for key, value in kwargs.items() if value is not None and value != ""}
    existing_context = structlog.contextvars.get_contextvars()

    if "trace_id" not in context:
        context["trace_id"] = existing_context.get("trace_id") or str(uuid4())
    if "request_id" not in context:
        context["request_id"] = existing_context.get("request_id") or context["trace_id"]

    tokens = structlog.contextvars.bind_contextvars(**context)
    try:
        yield context
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


def _safe_correlation_id(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if _CORRELATION_ID_RE.fullmatch(candidate):
        return candidate
    return None


def get_request_correlation_ids(request) -> dict[str, str]:
    request_id = _safe_correlation_id(request.headers.get("X-Request-ID")) or str(uuid4())
    trace_id = _safe_correlation_id(request.headers.get("X-Trace-ID")) or request_id
    return {
        "request_id": request_id,
        "trace_id": trace_id,
    }
