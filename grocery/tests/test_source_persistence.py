import hashlib
import json
import uuid

import pytest
from django.core.exceptions import ValidationError

from grocery.models import FetchAttempt, PageReceipt, SourceArtifact, SourceConfiguration
from grocery.source.client import KamisFetchResult, KamisTransportError
from grocery.source.client import PageReceipt as ClientPageReceipt
from grocery.source.persistence import (
    complete_kamis_fetch,
    fail_kamis_fetch,
    start_kamis_fetch,
)
from grocery.tests.test_acquisition_models import create_source_configuration

pytestmark = pytest.mark.django_db


def _manifest(*hashes: str) -> str:
    encoded = json.dumps(list(hashes), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def make_result(*, second_hash: str = "b" * 64, call_count: int = 2) -> KamisFetchResult:
    first_hash = "a" * 64
    receipts = (
        ClientPageReceipt(
            ordinal=1,
            requested_page_number=1,
            declared_page_number=1,
            declared_page_size=2,
            declared_total_count=3,
            row_count=2,
            http_status=200,
            provider_result_code="0",
            byte_length=20,
            body_sha256=first_hash,
        ),
        ClientPageReceipt(
            ordinal=2,
            requested_page_number=2,
            declared_page_number=2,
            declared_page_size=2,
            declared_total_count=3,
            row_count=1,
            http_status=200,
            provider_result_code="0",
            byte_length=10,
            body_sha256=second_hash,
        ),
    )
    return KamisFetchResult(
        rows=({"row": 1}, {"row": 2}, {"row": 3}),
        page_receipts=receipts,
        ordered_manifest_sha256=_manifest(first_hash, second_hash),
        call_count=call_count,
    )


def test_start_records_only_a_redacted_shape_for_an_active_recent_source() -> None:
    source = create_source_configuration()
    run_id = uuid.uuid4()

    attempt = start_kamis_fetch(source.id, acquisition_run_id=run_id)

    assert attempt.state == FetchAttempt.State.STARTED
    assert attempt.acquisition_run_id == run_id
    assert "<redacted>" in attempt.redacted_request_shape
    assert "://" not in attempt.redacted_request_shape
    assert "?" not in attempt.redacted_request_shape


def test_success_reconciles_receipts_and_builds_a_hash_only_artifact() -> None:
    source = create_source_configuration()
    attempt = start_kamis_fetch(source.id)
    result = make_result()

    completed = complete_kamis_fetch(attempt.id, result)

    assert completed.artifact_created is True
    assert completed.attempt.state == FetchAttempt.State.SUCCEEDED
    assert completed.attempt.received_page_count == 2
    assert completed.attempt.received_row_count == 3
    assert completed.attempt.received_byte_count == 30
    assert completed.artifact.ordered_manifest_sha256 == result.ordered_manifest_sha256
    assert completed.artifact.retention_mode == SourceArtifact.RetentionMode.HASH_ONLY
    assert list(
        PageReceipt.objects.filter(fetch_attempt=attempt).values_list(
            "request_ordinal", "page_number", "received_row_count", "body_byte_length"
        )
    ) == [(1, 1, 2, 20), (2, 2, 1, 10)]


def test_same_attempt_replay_is_idempotent_and_new_attempt_deduplicates_artifact() -> None:
    source = create_source_configuration()
    result = make_result()
    first_attempt = start_kamis_fetch(source.id)
    first = complete_kamis_fetch(first_attempt.id, result)
    replay = complete_kamis_fetch(first_attempt.id, result)
    second_attempt = start_kamis_fetch(source.id)
    second = complete_kamis_fetch(second_attempt.id, result)

    assert replay.attempt.id == first.attempt.id
    assert replay.artifact_created is False
    assert second.artifact.id == first.artifact.id
    assert second.artifact_created is False
    assert FetchAttempt.objects.count() == 2
    assert SourceArtifact.objects.count() == 1
    assert PageReceipt.objects.count() == 4


def test_manifest_or_budget_mismatch_rolls_back_without_partial_receipts() -> None:
    source = create_source_configuration(max_requests_per_attempt=1)
    attempt = start_kamis_fetch(source.id)

    with pytest.raises(ValidationError, match="request budget"):
        complete_kamis_fetch(attempt.id, make_result(call_count=2))

    attempt.refresh_from_db()
    assert attempt.state == FetchAttempt.State.STARTED
    assert not attempt.page_receipts.exists()
    assert not SourceArtifact.objects.exists()

    source_two = create_source_configuration()
    attempt_two = start_kamis_fetch(source_two.id)
    invalid = make_result()
    invalid = KamisFetchResult(
        rows=invalid.rows,
        page_receipts=invalid.page_receipts,
        ordered_manifest_sha256="f" * 64,
        call_count=invalid.call_count,
    )
    with pytest.raises(ValidationError, match="ordered manifests"):
        complete_kamis_fetch(attempt_two.id, invalid)
    assert not attempt_two.page_receipts.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": SourceConfiguration.State.PAUSED},
        {"publication_mode": SourceConfiguration.PublicationMode.CURRENT_ONLY},
    ],
)
def test_start_rejects_non_active_or_wrong_mode_source(overrides: dict[str, str]) -> None:
    source = create_source_configuration(**overrides)

    with pytest.raises(ValidationError):
        start_kamis_fetch(source.id)


def test_completed_replay_conflict_fails_closed_without_echoing_rows() -> None:
    source = create_source_configuration()
    attempt = start_kamis_fetch(source.id)
    complete_kamis_fetch(attempt.id, make_result())

    with pytest.raises(ValidationError, match="conflicts") as caught:
        complete_kamis_fetch(attempt.id, make_result(second_hash="c" * 64))

    assert "row" not in str(caught.value)


@pytest.mark.parametrize(
    ("error", "state", "failure_class"),
    [
        (
            KamisTransportError("retry_exhausted", http_status=429),
            FetchAttempt.State.RETRYABLE_FAILED,
            FetchAttempt.FailureClass.HTTP_429,
        ),
        (
            KamisTransportError("retry_exhausted", http_status=503),
            FetchAttempt.State.RETRYABLE_FAILED,
            FetchAttempt.FailureClass.HTTP_5XX,
        ),
        (
            KamisTransportError("retry_exhausted", provider_result_code="-5"),
            FetchAttempt.State.RETRYABLE_FAILED,
            FetchAttempt.FailureClass.PROVIDER_TRANSIENT,
        ),
        (
            KamisTransportError("terminal_http_status", http_status=401),
            FetchAttempt.State.TERMINAL_FAILED,
            FetchAttempt.FailureClass.AUTHENTICATION,
        ),
        (
            KamisTransportError("page_too_large"),
            FetchAttempt.State.TERMINAL_FAILED,
            FetchAttempt.FailureClass.RESPONSE_LIMIT,
        ),
        (
            KamisTransportError("unexpected_envelope_keys"),
            FetchAttempt.State.TERMINAL_FAILED,
            FetchAttempt.FailureClass.SCHEMA,
        ),
        (
            KamisTransportError("declared_total_changed"),
            FetchAttempt.State.TERMINAL_FAILED,
            FetchAttempt.FailureClass.RECONCILIATION,
        ),
    ],
)
def test_failure_finalization_records_only_safe_codes(
    error: KamisTransportError,
    state: str,
    failure_class: str,
) -> None:
    source = create_source_configuration()
    attempt = start_kamis_fetch(source.id)

    failed = fail_kamis_fetch(attempt.id, error)

    assert failed.state == state
    assert failed.failure_class == failure_class
    assert failed.failure_code == error.code.upper()
    assert failed.artifact_id is None
    assert not failed.page_receipts.exists()
    assert "serviceKey" not in failed.failure_code


def test_failure_finalization_cannot_overwrite_a_terminal_attempt() -> None:
    source = create_source_configuration()
    attempt = start_kamis_fetch(source.id)
    fail_kamis_fetch(attempt.id, KamisTransportError("tls_verification_failed"))

    with pytest.raises(ValidationError, match="started"):
        fail_kamis_fetch(attempt.id, KamisTransportError("retry_exhausted"))


def test_unknown_failure_code_is_not_copied_to_a_receipt_or_error_field() -> None:
    source = create_source_configuration()
    attempt = start_kamis_fetch(source.id)
    marker = "KAMIS_API_KEY_synthetic_marker"

    failed = fail_kamis_fetch(attempt.id, KamisTransportError(marker))

    assert failed.failure_code == "UNCLASSIFIED_TRANSPORT_ERROR"
    assert marker not in failed.failure_code
