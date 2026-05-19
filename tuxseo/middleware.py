import time

from core.acquisition import capture_request_attribution
from tuxseo.logging_context import bind_log_context, get_request_correlation_ids
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


class RequestLogContextMiddleware:
    """Bind per-request correlation context for structured logging."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        capture_request_attribution(request)
        correlation = get_request_correlation_ids(request)

        user_id = None
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            user_id = request.user.id

        project_id = request.GET.get("project_id") or request.POST.get("project_id")

        with bind_log_context(
            request_id=correlation["request_id"],
            trace_id=correlation["trace_id"],
            user_id=user_id,
            project_id=project_id,
            service="tuxseo-backend",
            module="django.request",
        ):
            try:
                response = self.get_response(request)
            except Exception:
                duration_ms = round((time.monotonic() - start) * 1000, 2)
                logger.error(
                    "[HTTP Request] Failed",
                    method=request.method,
                    path=request.path,
                    duration_ms=duration_ms,
                    exc_info=True,
                )
                raise

            duration_ms = round((time.monotonic() - start) * 1000, 2)
            logger.info(
                "[HTTP Request] Completed",
                method=request.method,
                path=request.path,
                status_code=getattr(response, "status_code", None),
                duration_ms=duration_ms,
            )

        response["X-Request-ID"] = correlation["request_id"]
        response["X-Trace-ID"] = correlation["trace_id"]
        return response
