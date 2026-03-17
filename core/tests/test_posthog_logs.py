from unittest.mock import Mock

from django.http import HttpResponse
from django.test import RequestFactory

from tuxseo.middleware import RequestLogContextMiddleware
from tuxseo.posthog_logs import ensure_exception_fields, redact_event


def test_redact_event_redacts_sensitive_fields_and_tokens():
    payload = {
        "event": "Test",
        "api_key": "secret-value",
        "authorization": "Bearer abc123",
        "user_email": "alice@example.com",
        "nested": {
            "token": "super-secret",
            "safe": "keep-me",
        },
    }

    redacted = redact_event(payload)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["user_email"] == "[REDACTED_EMAIL]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "keep-me"


def test_ensure_exception_fields_populates_type_and_stack_trace():
    payload = {
        "event": "boom",
        "exception": "ValueError: invalid input\ntraceback-line",
    }

    enriched = ensure_exception_fields(payload)

    assert enriched["exception_type"] == "ValueError"
    assert enriched["stack_trace"].startswith("ValueError: invalid input")


def test_request_logging_middleware_sets_correlation_headers(monkeypatch):
    factory = RequestFactory()
    request = factory.get("/health")
    request.user = Mock(is_authenticated=False)

    middleware = RequestLogContextMiddleware(lambda req: HttpResponse("ok", status=200))
    response = middleware(request)

    assert response.status_code == 200
    assert response["X-Request-ID"]
    assert response["X-Trace-ID"]
