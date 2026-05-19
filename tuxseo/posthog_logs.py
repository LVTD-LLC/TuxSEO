import logging
import re
import threading
from typing import Any

REDACTION_PLACEHOLDER = "[REDACTED]"

_SENSITIVE_KEY_SUBSTRINGS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
    "private_key",
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
_POSTHOG_KEY_RE = re.compile(r"\bph[csp]_[A-Za-z0-9]+\b")

_RESERVED_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__.keys())


def _looks_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in _SENSITIVE_KEY_SUBSTRINGS)


def _redact_string(value: str) -> str:
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _POSTHOG_KEY_RE.sub(REDACTION_PLACEHOLDER, redacted)
    return redacted


def redact_value(value: Any, key: str | None = None) -> Any:
    if key and _looks_sensitive_key(key):
        return REDACTION_PLACEHOLDER

    if isinstance(value, dict):
        return {k: redact_value(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, key=key) for item in value)
    if isinstance(value, str):
        return _redact_string(value)

    return value


def redact_event_dict(event_dict: dict[str, Any]) -> dict[str, Any]:
    return {key: redact_value(value, key=key) for key, value in event_dict.items()}


def redact_event(logger: Any, method_name: str | None = None, event_dict: dict[str, Any] | None = None):
    """Structlog processor for redaction (also backward compatible with direct dict calls in tests)."""
    if event_dict is None and isinstance(logger, dict):
        return redact_event_dict(logger)
    assert event_dict is not None
    return redact_event_dict(event_dict)


def _to_level(level: str) -> int:
    return {
        "critical": logging.CRITICAL,
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
    }.get((level or "").lower(), logging.INFO)


class PostHogLogsEmitter:
    """Best-effort async OTLP log emission to PostHog Logs."""

    def __init__(
        self,
        *,
        enabled: bool,
        endpoint: str,
        api_key: str,
        service_name: str,
        environment: str,
        batch_max_queue_size: int = 2048,
        batch_max_export_batch_size: int = 256,
        batch_schedule_delay_millis: int = 4000,
        batch_export_timeout_millis: int = 30000,
    ):
        self._enabled = bool(enabled and endpoint and api_key)
        self._endpoint = endpoint
        self._api_key = api_key
        self._service_name = service_name
        self._environment = environment
        self._batch_max_queue_size = batch_max_queue_size
        self._batch_max_export_batch_size = batch_max_export_batch_size
        self._batch_schedule_delay_millis = batch_schedule_delay_millis
        self._batch_export_timeout_millis = batch_export_timeout_millis

        self._init_lock = threading.Lock()
        self._initialized = False
        self._logger = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _init_if_needed(self) -> None:
        if not self._enabled or self._initialized:
            return

        with self._init_lock:
            if self._initialized:
                return
            try:
                from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
                from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
                from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
                from opentelemetry.sdk.resources import Resource
            except Exception:
                logging.getLogger(__name__).warning(
                    "[PostHogLogs] OpenTelemetry logging dependencies unavailable; disabling exporter",
                    exc_info=True,
                )
                self._enabled = False
                self._initialized = True
                return

            resource = Resource.create(
                {
                    "service.name": self._service_name,
                    "deployment.environment": self._environment,
                }
            )
            provider = LoggerProvider(resource=resource)
            exporter = OTLPLogExporter(
                endpoint=self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    exporter,
                    max_queue_size=self._batch_max_queue_size,
                    max_export_batch_size=self._batch_max_export_batch_size,
                    schedule_delay_millis=self._batch_schedule_delay_millis,
                    export_timeout_millis=self._batch_export_timeout_millis,
                )
            )

            handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
            exporter_logger = logging.getLogger("tuxseo.posthog_logs")
            exporter_logger.handlers = [handler]
            exporter_logger.propagate = False
            exporter_logger.setLevel(logging.INFO)

            self._logger = exporter_logger
            self._initialized = True

    def emit(self, event_dict: dict[str, Any]) -> None:
        if not self._enabled:
            return

        self._init_if_needed()
        if not self._enabled or self._logger is None:
            return

        payload = event_dict

        message = str(payload.get("event") or payload.get("message") or "")
        level = _to_level(str(payload.get("level") or "info"))

        attributes: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"event", "message"}:
                continue
            if key in _RESERVED_LOG_RECORD_FIELDS:
                attributes[f"attr_{key}"] = value
            else:
                attributes[key] = value

        self._logger.log(level, message, extra=attributes)


class PostHogLogsProcessor:
    """Structlog processor that forwards structured events to PostHog Logs."""

    def __init__(self, emitter: PostHogLogsEmitter):
        self._emitter = emitter

    def __call__(self, logger, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        if self._emitter.enabled:
            self._emitter.emit(event_dict)
        return event_dict


def ensure_exception_fields(
    logger: Any,
    method_name: str | None = None,
    event_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structlog processor for exception normalization (also accepts direct dict usage)."""
    if event_dict is None and isinstance(logger, dict):
        event_dict = logger
    assert event_dict is not None

    exception = event_dict.get("exception")
    if isinstance(exception, str) and exception:
        first_line = exception.splitlines()[0]
        event_dict.setdefault("exception_type", first_line.split(":", 1)[0].strip())
        event_dict.setdefault("stack_trace", exception)
    return event_dict
