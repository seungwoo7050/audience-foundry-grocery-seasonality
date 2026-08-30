"""Bounded structured observability without source or user data payloads.

The formatter deliberately ignores ``LogRecord.msg``, arguments, exception text,
and every attribute outside the explicit event envelope.  This keeps request
queries, search terms, response bodies, credentials, and provider identifiers out
of logs by construction.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Final, Literal, cast
from uuid import UUID, uuid4

from django.http import HttpRequest, HttpResponseBase

type Severity = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

OBSERVABILITY_KEYS: Final[tuple[str, ...]] = (
    "timestamp",
    "severity",
    "message_code",
    "request_id",
    "deploy_version",
    "command_run_id",
    "lifecycle_id",
    "lifecycle_status",
    "lifecycle_event",
)

_EVENT_ATTRIBUTE: Final = "_grocery_observability_event"
_NORMALIZED_ATTRIBUTE: Final = "_grocery_normalized_observability_event"
_INVALID_MESSAGE_CODE: Final = "observability.invalid_event"
_REQUEST_ID_HEADER: Final = "X-Request-ID"

_UUID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MESSAGE_CODE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,7}$")
_DEPLOY_VERSION_PATTERN: Final = re.compile(r"^[0-9a-f]{7,40}$")
_LIFECYCLE_TOKEN_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

_LEVEL_BY_SEVERITY: Final[dict[Severity, int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_SEVERITY_BY_LEVEL: Final[dict[int, Severity]] = {
    value: key for key, value in _LEVEL_BY_SEVERITY.items()
}

_request_id_context: ContextVar[str | None] = ContextVar(
    "grocery_request_id",
    default=None,
)


class ObservabilityValidationError(ValueError):
    """An event did not fit the deliberately narrow logging contract."""


def _canonical_uuid(value: str | UUID, *, field_name: str) -> str:
    if type(value) is UUID:
        return str(value)
    if type(value) is not str or _UUID_PATTERN.fullmatch(value) is None:
        raise ObservabilityValidationError(f"{field_name} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ObservabilityValidationError(f"{field_name} must be a canonical UUID") from exc
    canonical = str(parsed)
    if canonical != value:
        raise ObservabilityValidationError(f"{field_name} must be a canonical UUID")
    return canonical


def _validated_text(value: str, *, field_name: str, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ObservabilityValidationError(f"{field_name} has an invalid value")
    return value


def make_observability_event(
    message_code: str,
    *,
    request_id: str | UUID | None = None,
    deploy_version: str | None = None,
    command_run_id: str | UUID | None = None,
    lifecycle_id: str | UUID | None = None,
    lifecycle_status: str | None = None,
    lifecycle_event: str | None = None,
) -> dict[str, str]:
    """Build the only payload shape accepted by the JSON formatter.

    There is intentionally no arbitrary ``extra`` mapping.  Callers can record
    correlation and lifecycle state, but cannot attach source payloads or user
    input to an event.
    """

    event = {
        "message_code": _validated_text(
            message_code,
            field_name="message_code",
            pattern=_MESSAGE_CODE_PATTERN,
        )
    }
    if request_id is not None:
        event["request_id"] = _canonical_uuid(request_id, field_name="request_id")
    if deploy_version is not None:
        event["deploy_version"] = _validated_text(
            deploy_version,
            field_name="deploy_version",
            pattern=_DEPLOY_VERSION_PATTERN,
        )
    if command_run_id is not None:
        event["command_run_id"] = _canonical_uuid(
            command_run_id,
            field_name="command_run_id",
        )
    if lifecycle_id is not None:
        event["lifecycle_id"] = _canonical_uuid(lifecycle_id, field_name="lifecycle_id")
    if lifecycle_status is not None:
        event["lifecycle_status"] = _validated_text(
            lifecycle_status,
            field_name="lifecycle_status",
            pattern=_LIFECYCLE_TOKEN_PATTERN,
        )
    if lifecycle_event is not None:
        event["lifecycle_event"] = _validated_text(
            lifecycle_event,
            field_name="lifecycle_event",
            pattern=_LIFECYCLE_TOKEN_PATTERN,
        )
    return event


def _normalize_event(raw_event: object) -> dict[str, str] | None:
    """Copy known, valid fields without iterating or stringifying unknown values."""

    if type(raw_event) is not dict:
        return None

    event_mapping = cast(dict[object, object], raw_event)
    message_code = event_mapping.get("message_code")
    if type(message_code) is not str:
        return None

    def optional_text(field_name: str) -> str | None:
        value = event_mapping.get(field_name)
        if value is None:
            return None
        if type(value) is not str:
            raise ObservabilityValidationError(f"{field_name} has an invalid value")
        return value

    try:
        return make_observability_event(
            message_code,
            request_id=optional_text("request_id"),
            deploy_version=optional_text("deploy_version"),
            command_run_id=optional_text("command_run_id"),
            lifecycle_id=optional_text("lifecycle_id"),
            lifecycle_status=optional_text("lifecycle_status"),
            lifecycle_event=optional_text("lifecycle_event"),
        )
    except ObservabilityValidationError:
        return None


def _timestamp_text(timestamp: datetime) -> str:
    if type(timestamp) is not datetime or timestamp.tzinfo is None:
        raise ObservabilityValidationError("timestamp must be timezone-aware")
    return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_observability_event(
    event: dict[str, str],
    *,
    timestamp: datetime,
    severity: Severity,
) -> str:
    """Serialize one validated event as deterministic, single-line JSON."""

    if severity not in _LEVEL_BY_SEVERITY:
        raise ObservabilityValidationError("severity has an invalid value")
    normalized = _normalize_event(event)
    if normalized is None:
        raise ObservabilityValidationError("event has an invalid value")

    payload: dict[str, str] = {
        "timestamp": _timestamp_text(timestamp),
        "severity": severity,
        "message_code": normalized["message_code"],
    }
    for key in OBSERVABILITY_KEYS[3:]:
        value = normalized.get(key)
        if value is not None:
            payload[key] = value
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class ObservabilityAllowlistFilter(logging.Filter):
    """Admit only records containing a valid bounded event envelope."""

    def filter(self, record: logging.LogRecord) -> bool:
        normalized = _normalize_event(record.__dict__.get(_EVENT_ATTRIBUTE))
        if normalized is None:
            return False
        setattr(record, _NORMALIZED_ATTRIBUTE, normalized)
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Emit one allowlisted JSON object and ignore normal log message content."""

    def format(self, record: logging.LogRecord) -> str:
        raw_normalized = record.__dict__.get(_NORMALIZED_ATTRIBUTE)
        normalized = _normalize_event(raw_normalized)
        if normalized is None:
            normalized = _normalize_event(record.__dict__.get(_EVENT_ATTRIBUTE))
        if normalized is None:
            normalized = make_observability_event(_INVALID_MESSAGE_CODE)

        severity = _SEVERITY_BY_LEVEL.get(record.levelno, "WARNING")
        try:
            timestamp = datetime.fromtimestamp(record.created, tz=UTC)
        except OverflowError, OSError, ValueError:
            timestamp = datetime.fromtimestamp(0, tz=UTC)
        return format_observability_event(
            normalized,
            timestamp=timestamp,
            severity=severity,
        )


def log_event(
    logger: logging.Logger,
    severity: Severity,
    message_code: str,
    *,
    request_id: str | UUID | None = None,
    deploy_version: str | None = None,
    command_run_id: str | UUID | None = None,
    lifecycle_id: str | UUID | None = None,
    lifecycle_status: str | None = None,
    lifecycle_event: str | None = None,
) -> None:
    """Log one structured event without accepting message text or arbitrary extras."""

    if severity not in _LEVEL_BY_SEVERITY:
        raise ObservabilityValidationError("severity has an invalid value")
    event = make_observability_event(
        message_code,
        request_id=request_id if request_id is not None else current_request_id(),
        deploy_version=deploy_version,
        command_run_id=command_run_id,
        lifecycle_id=lifecycle_id,
        lifecycle_status=lifecycle_status,
        lifecycle_event=lifecycle_event,
    )
    logger.log(
        _LEVEL_BY_SEVERITY[severity],
        "structured-event",
        extra={_EVENT_ATTRIBUTE: event},
    )


def current_request_id() -> str | None:
    """Return the current request correlation UUID, if running inside middleware."""

    return _request_id_context.get()


def _request_id_from_header(value: object) -> str | None:
    if type(value) is not str or _UUID_PATTERN.fullmatch(value) is None:
        return None
    try:
        return _canonical_uuid(value, field_name="request_id")
    except ObservabilityValidationError:
        return None


class RequestIdMiddleware:
    """Propagate a valid correlation UUID or generate one at the HTTP boundary."""

    sync_capable = True
    async_capable = False

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        request_id = _request_id_from_header(request.headers.get(_REQUEST_ID_HEADER))
        if request_id is None:
            request_id = str(uuid4())

        token: Token[str | None] = _request_id_context.set(request_id)
        request.__dict__["request_id"] = request_id
        try:
            response = self.get_response(request)
            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        finally:
            _request_id_context.reset(token)
