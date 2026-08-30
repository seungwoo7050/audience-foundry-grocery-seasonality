import io
import json
import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from grocery.observability import (
    OBSERVABILITY_KEYS,
    ObservabilityAllowlistFilter,
    ObservabilityValidationError,
    RequestIdMiddleware,
    StructuredJsonFormatter,
    current_request_id,
    format_observability_event,
    log_event,
    make_observability_event,
)

REQUEST_ID = "018f47d2-f9b2-7cc4-8ddf-fce39c000001"
COMMAND_RUN_ID = "018f47d2-f9b2-7cc4-8ddf-fce39c000002"
LIFECYCLE_ID = "018f47d2-f9b2-7cc4-8ddf-fce39c000003"
DEPLOY_VERSION = "0123456789abcdef0123456789abcdef01234567"


class MustNotBeStringified:
    def __str__(self) -> str:
        raise AssertionError("an arbitrary object was stringified")


def test_event_formatter_has_deterministic_allowlisted_schema() -> None:
    event = make_observability_event(
        "publication.activation.completed",
        request_id=REQUEST_ID,
        deploy_version=DEPLOY_VERSION,
        command_run_id=COMMAND_RUN_ID,
        lifecycle_id=LIFECYCLE_ID,
        lifecycle_status="ACTIVE",
        lifecycle_event="PUBLICATION_ACTIVATED",
    )

    line = format_observability_event(
        event,
        timestamp=datetime(2026, 8, 30, 1, 2, 3, 456789, tzinfo=UTC),
        severity="INFO",
    )

    assert line == (
        '{"timestamp":"2026-08-30T01:02:03.456Z","severity":"INFO",'
        '"message_code":"publication.activation.completed",'
        f'"request_id":"{REQUEST_ID}","deploy_version":"{DEPLOY_VERSION}",'
        f'"command_run_id":"{COMMAND_RUN_ID}","lifecycle_id":"{LIFECYCLE_ID}",'
        '"lifecycle_status":"ACTIVE","lifecycle_event":"PUBLICATION_ACTIVATED"}'
    )
    assert tuple(json.loads(line)) == OBSERVABILITY_KEYS
    assert "\n" not in line
    assert "\r" not in line


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message_code", "source.fetch\ncredential"),
        ("message_code", "https://provider.example/path?query=secret"),
        ("request_id", "search=한우"),
        ("deploy_version", "provider-dataset-15156063"),
        ("lifecycle_status", "ACTIVE\r\ncredential"),
        ("lifecycle_event", "RAW_BODY={secret}"),
    ],
)
def test_event_helper_rejects_unbounded_or_injectable_values(field: str, value: str) -> None:
    kwargs = {field: value}
    if field == "message_code":
        with pytest.raises(ObservabilityValidationError):
            make_observability_event(value)
    else:
        with pytest.raises(ObservabilityValidationError):
            make_observability_event("source.fetch.completed", **kwargs)


def test_filter_and_formatter_drop_every_unknown_sensitive_field() -> None:
    raw_event: dict[str, object] = {
        "message_code": "source.fetch.completed",
        "lifecycle_status": "SUCCEEDED",
        "query_string": "serviceKey=secret-value",
        "search_term": "민감한 검색어",
        "url": "https://provider.example/path?serviceKey=secret-value",
        "raw_body": '{"credential":"secret-value"}',
        "credentials": "secret-value",
        "secret_value": "secret-value",
        "provider_identifier": "provider-dataset-15156063",
        "exception": MustNotBeStringified(),
    }
    record = logging.LogRecord(
        name="test.observability",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=MustNotBeStringified(),
        args=(),
        exc_info=None,
    )
    record.created = 0
    record.__dict__["_grocery_observability_event"] = raw_event

    assert ObservabilityAllowlistFilter().filter(record)
    line = StructuredJsonFormatter().format(record)

    assert json.loads(line) == {
        "timestamp": "1970-01-01T00:00:00.000Z",
        "severity": "INFO",
        "message_code": "source.fetch.completed",
        "lifecycle_status": "SUCCEEDED",
    }
    for forbidden in (
        "serviceKey",
        "secret-value",
        "민감한 검색어",
        "https://",
        "provider-dataset",
        "raw_body",
        "query_string",
    ):
        assert forbidden not in line


def test_filter_rejects_invalid_event_and_formatter_uses_safe_fallback() -> None:
    record = logging.LogRecord(
        name="test.observability",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="https://provider.example/?credential=secret-value\nforged-line",
        args=(),
        exc_info=None,
    )
    record.created = 0
    record.__dict__["_grocery_observability_event"] = {
        "message_code": "invalid\nsecret-value",
        "raw_body": "secret-value",
    }

    assert not ObservabilityAllowlistFilter().filter(record)
    line = StructuredJsonFormatter().format(record)

    assert json.loads(line) == {
        "timestamp": "1970-01-01T00:00:00.000Z",
        "severity": "ERROR",
        "message_code": "observability.invalid_event",
    }
    assert "secret-value" not in line
    assert "forged-line" not in line
    assert "\n" not in line


@override_settings(DEPLOY_VERSION=DEPLOY_VERSION)
def test_log_event_emits_one_line_without_normal_log_message_data() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(ObservabilityAllowlistFilter())
    handler.setFormatter(StructuredJsonFormatter())
    logger = logging.getLogger("grocery.tests.structured-event")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        log_event(
            logger,
            "INFO",
            "review.decision.recorded",
            command_run_id=UUID(COMMAND_RUN_ID),
            lifecycle_id=UUID(LIFECYCLE_ID),
            lifecycle_status="APPROVED",
            lifecycle_event="REVIEW_RECORDED",
        )
    finally:
        logger.handlers = []
        logger.propagate = True

    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["message_code"] == "review.decision.recorded"
    assert payload["deploy_version"] == DEPLOY_VERSION
    assert payload["command_run_id"] == COMMAND_RUN_ID
    assert payload["lifecycle_id"] == LIFECYCLE_ID
    assert set(payload).issubset(OBSERVABILITY_KEYS)


def test_request_id_middleware_propagates_valid_uuid_and_clears_context() -> None:
    observed: list[str | None] = []

    def get_response(request: object) -> HttpResponse:
        del request
        observed.append(current_request_id())
        return HttpResponse(status=204)

    request = RequestFactory().get("/health")
    request.META["HTTP_X_REQUEST_ID"] = REQUEST_ID
    response = RequestIdMiddleware(get_response)(request)

    assert observed == [REQUEST_ID]
    assert request.__dict__["request_id"] == REQUEST_ID
    assert response.headers["X-Request-ID"] == REQUEST_ID
    assert current_request_id() is None


@pytest.mark.parametrize(
    "untrusted_header",
    [
        "not-a-uuid",
        "https://provider.example/?serviceKey=secret-value",
        "018f47d2-f9b2-7cc4-8ddf-fce39c000001\r\nX-Forged: true",
    ],
)
def test_request_id_middleware_replaces_untrusted_header(untrusted_header: str) -> None:
    request = RequestFactory().get("/health")
    request.META["HTTP_X_REQUEST_ID"] = untrusted_header

    response = RequestIdMiddleware(lambda unused: HttpResponse(status=204))(request)
    generated = response.headers["X-Request-ID"]

    assert generated != untrusted_header
    assert str(UUID(generated)) == generated
    assert request.__dict__["request_id"] == generated
    assert "secret-value" not in generated
    assert "\r" not in generated
    assert "\n" not in generated
