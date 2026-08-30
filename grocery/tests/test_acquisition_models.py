import uuid
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from grocery.models import FetchAttempt, PageReceipt, SourceConfiguration


def create_source_configuration(**overrides: Any) -> SourceConfiguration:
    values: dict[str, Any] = {
        "source_owner_name": "한국농수산식품유통공사",
        "dataset_id": "15156063",
        "configuration_revision": str(uuid.uuid4()),
        "interface_revision": "recent-price-v1",
        "state": SourceConfiguration.State.ACTIVE,
        "publication_mode": SourceConfiguration.PublicationMode.RECENT_COMPARISON,
        "coverage_identity": "KAMIS_RETAIL_ALL_REGIONS_22_CITIES_V1",
        "coverage_evidence_revision": "2026-08-30",
        "endpoint_host": "apis.data.go.kr",
        "endpoint_path": "/B552845/recent/price",
        "authentication_mode": SourceConfiguration.AuthenticationMode.DATA_GO_KR_SERVICE_KEY,
        "logical_secret_name": "KAMIS_API_KEY",
        "provider_quota_limit": 10_000,
        "provider_quota_period": SourceConfiguration.QuotaPeriod.UNSPECIFIED,
        "request_timeout_seconds": 10,
        "retry_policy": SourceConfiguration.RetryPolicy.BOUNDED_TRANSIENT_ONLY,
        "schedule_execution_mode": (SourceConfiguration.ScheduleExecutionMode.PLATFORM_SINGLETON),
        "schedule_interval_hours": 24,
        "max_retries": 2,
        "max_requests_per_attempt": 12,
        "max_pages_per_attempt": 10,
        "max_page_bytes": 4 * 1024 * 1024,
        "rights_evidence_locator": "https://www.data.go.kr/data/15156063/openapi.do",
        "rights_evidence_sha256": "a" * 64,
        "rights_confirmed_at": timezone.now(),
    }
    values.update(overrides)
    return SourceConfiguration.objects.create(**values)


def create_fetch_attempt(
    source: SourceConfiguration,
    **overrides: Any,
) -> FetchAttempt:
    values: dict[str, Any] = {
        "source_configuration": source,
        "acquisition_run_id": uuid.uuid4(),
        "attempt_ordinal": 1,
        "redacted_request_shape": (
            "GET endpoint parameters=[pageNo,numOfRows,returnType,serviceKey:<redacted>]"
        ),
    }
    values.update(overrides)
    return FetchAttempt.objects.create(**values)


def create_page_receipt(attempt: FetchAttempt, **overrides: Any) -> PageReceipt:
    values: dict[str, Any] = {
        "fetch_attempt": attempt,
        "request_ordinal": 1,
        "page_number": 1,
        "http_status": 200,
        "provider_result_code": "0",
        "declared_total_count": 452,
        "received_row_count": 100,
        "body_state": PageReceipt.BodyState.RECEIVED,
        "body_byte_length": 1024,
        "body_sha256": "b" * 64,
        "media_type": PageReceipt.MediaType.JSON,
        "encoding": PageReceipt.Encoding.UTF_8,
    }
    values.update(overrides)
    return PageReceipt.objects.create(**values)


class SourceConfigurationTests(TestCase):
    def test_valid_configuration_uses_uuid_and_only_logical_secret_reference(self) -> None:
        source = create_source_configuration()

        self.assertIsInstance(source.pk, uuid.UUID)
        self.assertEqual(source.endpoint_scheme, "https")
        self.assertEqual(source.endpoint_method, "GET")
        self.assertEqual(source.raw_retention, "HASH_ONLY")
        self.assertEqual(source.schedule_execution_mode, "PLATFORM_SINGLETON")
        self.assertEqual(source.schedule_interval_hours, 24)
        field_names = {field.name for field in source._meta.fields}
        self.assertIn("logical_secret_name", field_names)
        self.assertFalse({"secret", "credential", "api_key"} & field_names)

    def test_endpoint_query_invalid_hash_and_zero_budget_are_rejected(self) -> None:
        invalid = create_source_configuration()
        invalid.endpoint_path = "/B552845/recent/price?serviceKey=forbidden"
        with self.assertRaises(ValidationError):
            invalid.save()

        invalid.endpoint_path = "/B552845/recent/price"
        invalid.rights_evidence_sha256 = "A" * 64
        with self.assertRaises(ValidationError):
            invalid.save()

        invalid.rights_evidence_sha256 = "a" * 64
        invalid.max_pages_per_attempt = 0
        with self.assertRaises(ValidationError):
            invalid.save()

    def test_authenticated_configuration_requires_a_logical_secret_name(self) -> None:
        with self.assertRaises(ValidationError):
            create_source_configuration(logical_secret_name="")
        with self.assertRaises(ValidationError):
            create_source_configuration(logical_secret_name="not-a-logical-name")

    def test_configuration_is_immutable_and_protected_after_first_fetch(self) -> None:
        source = create_source_configuration()
        create_fetch_attempt(source)
        source.state = SourceConfiguration.State.PAUSED
        source.state_changed_at = timezone.now()

        with self.assertRaisesMessage(ValidationError, "immutable"):
            source.save()
        with self.assertRaises(ProtectedError):
            source.delete()

    def test_database_rejects_an_unlisted_state_when_model_validation_is_bypassed(self) -> None:
        source = create_source_configuration()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceConfiguration.objects.filter(pk=source.pk).update(state="UNLISTED")

    def test_database_rejects_invalid_schedule_when_validation_is_bypassed(self) -> None:
        source = create_source_configuration()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceConfiguration.objects.filter(pk=source.pk).update(
                schedule_execution_mode="INLINE_WEB_REQUEST"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceConfiguration.objects.filter(pk=source.pk).update(schedule_interval_hours=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceConfiguration.objects.filter(pk=source.pk).update(schedule_interval_hours=25)


class FetchAttemptTests(TestCase):
    def test_attempt_ordinal_is_unique_per_acquisition_run(self) -> None:
        source = create_source_configuration()
        run_id = uuid.uuid4()
        create_fetch_attempt(source, acquisition_run_id=run_id)

        with self.assertRaises(ValidationError):
            create_fetch_attempt(source, acquisition_run_id=run_id)
        second = create_fetch_attempt(source, acquisition_run_id=run_id, attempt_ordinal=2)
        self.assertEqual(second.attempt_ordinal, 2)

    def test_terminal_states_require_completion_and_failure_fields(self) -> None:
        source = create_source_configuration()
        completed_at = timezone.now()

        with self.assertRaises(ValidationError):
            create_fetch_attempt(source, state=FetchAttempt.State.SUCCEEDED)
        with self.assertRaises(ValidationError):
            create_fetch_attempt(
                source,
                state=FetchAttempt.State.RETRYABLE_FAILED,
                started_at=completed_at - timedelta(seconds=1),
                completed_at=completed_at,
            )

        failed = create_fetch_attempt(
            source,
            state=FetchAttempt.State.RETRYABLE_FAILED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            failure_class=FetchAttempt.FailureClass.TIMEOUT,
            failure_code="CLIENT_TIMEOUT",
        )
        self.assertEqual(failed.failure_class, FetchAttempt.FailureClass.TIMEOUT)

    def test_completion_cannot_precede_start(self) -> None:
        source = create_source_configuration()
        started_at = timezone.now()

        with self.assertRaises(ValidationError):
            create_fetch_attempt(
                source,
                state=FetchAttempt.State.SUCCEEDED,
                started_at=started_at,
                completed_at=started_at - timedelta(seconds=1),
            )

    def test_request_shape_rejects_full_urls_and_query_strings(self) -> None:
        source = create_source_configuration()
        with self.assertRaises(ValidationError):
            create_fetch_attempt(
                source,
                redacted_request_shape="GET https://apis.data.go.kr/path?serviceKey=<redacted>",
            )


class PageReceiptTests(TestCase):
    def test_received_and_not_received_receipts_have_distinct_shapes(self) -> None:
        source = create_source_configuration()
        attempt = create_fetch_attempt(source)
        received = create_page_receipt(attempt)
        absent = create_page_receipt(
            attempt,
            request_ordinal=2,
            page_number=2,
            http_status=None,
            provider_result_code="",
            declared_total_count=None,
            received_row_count=0,
            body_state=PageReceipt.BodyState.NOT_RECEIVED,
            body_byte_length=0,
            body_sha256="",
            body_absence_reason=PageReceipt.BodyAbsenceReason.TIMEOUT,
            media_type="",
            encoding="",
        )

        self.assertIsInstance(received.pk, uuid.UUID)
        self.assertEqual(absent.body_absence_reason, PageReceipt.BodyAbsenceReason.TIMEOUT)

    def test_page_and_request_ordinals_are_independently_unique_per_attempt(self) -> None:
        source = create_source_configuration()
        attempt = create_fetch_attempt(source)
        create_page_receipt(attempt)

        with self.assertRaises(ValidationError):
            create_page_receipt(attempt, page_number=2)
        with self.assertRaises(ValidationError):
            create_page_receipt(attempt, request_ordinal=2)

    def test_invalid_body_shape_and_uppercase_hash_are_rejected(self) -> None:
        source = create_source_configuration()
        attempt = create_fetch_attempt(source)

        with self.assertRaises(ValidationError):
            create_page_receipt(attempt, body_sha256="B" * 64)
        with self.assertRaises(ValidationError):
            create_page_receipt(
                attempt,
                body_state=PageReceipt.BodyState.NOT_RECEIVED,
                body_absence_reason="",
            )

    def test_database_hash_constraint_and_receipt_immutability(self) -> None:
        source = create_source_configuration()
        attempt = create_fetch_attempt(source)
        receipt = create_page_receipt(attempt)

        with self.assertRaises(IntegrityError), transaction.atomic():
            PageReceipt.objects.filter(pk=receipt.pk).update(body_sha256="B" * 64)
        receipt.provider_result_code = "CHANGED"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            receipt.save()

    def test_receipt_requires_a_started_attempt_and_fk_is_protected(self) -> None:
        source = create_source_configuration()
        completed_at = timezone.now()
        attempt = create_fetch_attempt(
            source,
            state=FetchAttempt.State.SUCCEEDED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
        )

        with self.assertRaises(ValidationError):
            create_page_receipt(attempt)

        started_attempt = create_fetch_attempt(source)
        create_page_receipt(started_attempt)
        with self.assertRaises(ProtectedError):
            started_attempt.delete()
