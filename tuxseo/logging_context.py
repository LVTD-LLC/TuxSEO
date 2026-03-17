from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import structlog


@contextmanager
def bind_log_context(**kwargs: Any):
    context = {key: value for key, value in kwargs.items() if value is not None and value != ""}
    if "trace_id" not in context:
        context["trace_id"] = str(uuid4())
    if "request_id" not in context:
        context["request_id"] = context["trace_id"]

    tokens = structlog.contextvars.bind_contextvars(**context)
    try:
        yield context
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


def get_request_correlation_ids(request) -> dict[str, str]:
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    trace_id = request.headers.get("X-Trace-ID") or request_id
    return {
        "request_id": request_id,
        "trace_id": trace_id,
    }
