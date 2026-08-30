import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from grocery.models import (
    FetchAttempt,
    ParseRun,
    SourceArtifact,
    build_source_artifact,
)
from grocery.tests.test_acquisition_models import (
    create_fetch_attempt,
    create_page_receipt,
    create_source_configuration,
)


def create_succeeded_attempt() -> FetchAttempt:
    source = create_source_configuration()
    attempt = create_fetch_attempt(source)
    create_page_receipt(
        attempt,
        declared_total_count=3,
        received_row_count=2,
        body_byte_length=10,
        body_sha256="b" * 64,
    )
    create_page_receipt(
        attempt,
        request_ordinal=2,
        page_number=2,
        declared_total_count=3,
        received_row_count=1,
        body_byte_length=20,
        body_sha256="c" * 64,
    )
    attempt.state = FetchAttempt.State.SUCCEEDED
    attempt.completed_at = timezone.now()
    attempt.received_page_count = 2
    attempt.received_row_count = 3
    attempt.received_byte_count = 30
    attempt.save()
    return attempt


def create_artifact() -> SourceArtifact:
    artifact, _ = build_source_artifact(create_succeeded_attempt().id)
    return artifact


class SourceArtifactTests(TestCase):
    def test_succeeded_attempt_builds_hash_only_artifact_and_links_it(self) -> None:
        attempt = create_succeeded_attempt()

        artifact, created = build_source_artifact(attempt.id)

        attempt.refresh_from_db()
        self.assertTrue(created)
        self.assertIsInstance(artifact.id, uuid.UUID)
        self.assertEqual(attempt.artifact, artifact)
        self.assertEqual(artifact.page_count, 2)
        self.assertEqual(artifact.total_bytes, 30)
        self.assertEqual(artifact.media_type, SourceArtifact.MediaType.JSON)
        self.assertEqual(artifact.encoding, SourceArtifact.Encoding.UTF_8)
        self.assertEqual(artifact.retention_mode, SourceArtifact.RetentionMode.HASH_ONLY)
        self.assertEqual(artifact.first_seen_at, attempt.completed_at)
        self.assertEqual(len(artifact.ordered_manifest_sha256), 64)
        field_names = {field.name for field in artifact._meta.fields}
        self.assertFalse(
            {"body", "payload", "raw", "raw_body", "locator", "private_object_locator"}
            & field_names
        )

    def test_same_manifest_from_a_new_attempt_deduplicates_the_artifact(self) -> None:
        first_attempt = create_succeeded_attempt()
        first, first_created = build_source_artifact(first_attempt.id)
        second_attempt = create_fetch_attempt(first_attempt.source_configuration)
        create_page_receipt(
            second_attempt,
            declared_total_count=3,
            received_row_count=2,
            body_byte_length=10,
            body_sha256="b" * 64,
        )
        create_page_receipt(
            second_attempt,
            request_ordinal=2,
            page_number=2,
            declared_total_count=3,
            received_row_count=1,
            body_byte_length=20,
            body_sha256="c" * 64,
        )
        second_attempt.state = FetchAttempt.State.SUCCEEDED
        second_attempt.completed_at = timezone.now()
        second_attempt.received_page_count = 2
        second_attempt.received_row_count = 3
        second_attempt.received_byte_count = 30
        second_attempt.save()

        second, second_created = build_source_artifact(second_attempt.id)
        repeated, repeated_created = build_source_artifact(second_attempt.id)

        second_attempt.refresh_from_db()
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.id, repeated.id)
        self.assertEqual(second_attempt.artifact_id, first.id)
        self.assertEqual(SourceArtifact.objects.count(), 1)

    def test_builder_rejects_gaps_cross_attempt_mixing_and_counter_drift(self) -> None:
        source = create_source_configuration()
        gap_attempt = create_fetch_attempt(source)
        create_page_receipt(
            gap_attempt,
            page_number=2,
            declared_total_count=1,
            received_row_count=1,
        )
        gap_attempt.state = FetchAttempt.State.SUCCEEDED
        gap_attempt.completed_at = timezone.now()
        gap_attempt.received_page_count = 1
        gap_attempt.received_row_count = 1
        gap_attempt.received_byte_count = 1024
        gap_attempt.save()
        other_attempt = create_fetch_attempt(source)
        create_page_receipt(
            other_attempt,
            declared_total_count=1,
            received_row_count=1,
            body_sha256="c" * 64,
        )

        with self.assertRaisesMessage(ValidationError, "page numbers"):
            build_source_artifact(gap_attempt.id)

        valid_attempt = create_succeeded_attempt()
        valid_attempt.received_byte_count = 31
        valid_attempt.save()
        with self.assertRaisesMessage(ValidationError, "counters"):
            build_source_artifact(valid_attempt.id)

        self.assertEqual(SourceArtifact.objects.count(), 0)

    def test_builder_requires_success_and_full_declared_total_reconciliation(self) -> None:
        source = create_source_configuration()
        started = create_fetch_attempt(source)
        with self.assertRaisesMessage(ValidationError, "succeeded"):
            build_source_artifact(started.id)

        attempt = create_fetch_attempt(source)
        create_page_receipt(attempt, declared_total_count=2, received_row_count=1)
        attempt.state = FetchAttempt.State.SUCCEEDED
        attempt.completed_at = timezone.now()
        attempt.received_page_count = 1
        attempt.received_row_count = 1
        attempt.received_byte_count = 1024
        attempt.save()
        with self.assertRaisesMessage(ValidationError, "declared total"):
            build_source_artifact(attempt.id)

    def test_artifact_is_immutable_and_protected(self) -> None:
        attempt = create_succeeded_attempt()
        artifact, _ = build_source_artifact(attempt.id)
        artifact.total_bytes = 31

        with self.assertRaisesMessage(ValidationError, "immutable"):
            artifact.save()
        with self.assertRaises(ProtectedError):
            artifact.delete()

    def test_database_rejects_invalid_hash_and_retention_when_validation_is_bypassed(self) -> None:
        artifact = create_artifact()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceArtifact.objects.filter(pk=artifact.pk).update(ordered_manifest_sha256="A" * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceArtifact.objects.filter(pk=artifact.pk).update(retention_mode="RAW")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceArtifact.objects.filter(pk=artifact.pk).update(page_count=0)


class ParseRunTests(TestCase):
    def test_validated_run_reconciles_counts_and_is_unique(self) -> None:
        artifact = create_artifact()
        completed_at = timezone.now()
        parse_run = ParseRun.objects.create(
            artifact=artifact,
            parser_revision="kamis-recent-v1",
            configuration_hash="d" * 64,
            result_hash="e" * 64,
            status=ParseRun.Status.VALIDATED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            total_row_count=3,
            accepted_row_count=2,
            missing_reference_row_count=1,
            out_of_scope_row_count=1,
            quarantined_row_count=0,
        )

        self.assertIsInstance(parse_run.id, uuid.UUID)
        with self.assertRaises(ValidationError):
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="kamis-recent-v1",
                configuration_hash="d" * 64,
            )
        with self.assertRaises(ProtectedError):
            artifact.delete()

    def test_validated_requires_result_hash_and_zero_quarantine(self) -> None:
        artifact = create_artifact()
        completed_at = timezone.now()

        with self.assertRaises(ValidationError):
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="kamis-recent-v1",
                configuration_hash="d" * 64,
                status=ParseRun.Status.VALIDATED,
                completed_at=completed_at,
                total_row_count=1,
                accepted_row_count=1,
            )
        with self.assertRaises(ValidationError):
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="kamis-recent-v1",
                configuration_hash="f" * 64,
                result_hash="e" * 64,
                status=ParseRun.Status.VALIDATED,
                completed_at=completed_at,
                total_row_count=1,
                quarantined_row_count=1,
            )

    def test_quarantined_and_failed_runs_fail_closed(self) -> None:
        artifact = create_artifact()
        completed_at = timezone.now()
        quarantined = ParseRun.objects.create(
            artifact=artifact,
            parser_revision="kamis-recent-v1",
            configuration_hash="d" * 64,
            status=ParseRun.Status.QUARANTINED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            total_row_count=1,
            quarantined_row_count=1,
            failure_code="IDENTITY_DRIFT",
        )
        failed = ParseRun.objects.create(
            artifact=artifact,
            parser_revision="kamis-recent-v1",
            configuration_hash="e" * 64,
            status=ParseRun.Status.FAILED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            failure_code="SCHEMA_INVALID",
        )

        self.assertEqual(quarantined.result_hash, "")
        self.assertEqual(failed.result_hash, "")
        with self.assertRaises(ValidationError):
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="kamis-recent-v1",
                configuration_hash="f" * 64,
                result_hash="a" * 64,
                status=ParseRun.Status.FAILED,
                started_at=completed_at - timedelta(seconds=1),
                completed_at=completed_at,
                failure_code="SCHEMA_INVALID",
            )

    def test_count_invariants_and_lowercase_hashes_are_enforced(self) -> None:
        artifact = create_artifact()

        with self.assertRaises(ValidationError):
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="kamis-recent-v1",
                configuration_hash="D" * 64,
            )
        with self.assertRaises(ValidationError):
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="kamis-recent-v1",
                configuration_hash="d" * 64,
                total_row_count=2,
                accepted_row_count=1,
            )
        with self.assertRaises(ValidationError):
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="kamis-recent-v1",
                configuration_hash="e" * 64,
                total_row_count=1,
                accepted_row_count=1,
                missing_reference_row_count=2,
            )

    def test_started_run_can_complete_once_and_database_rejects_invalid_state(self) -> None:
        artifact = create_artifact()
        parse_run = ParseRun.objects.create(
            artifact=artifact,
            parser_revision="kamis-recent-v1",
            configuration_hash="d" * 64,
        )
        parse_run.status = ParseRun.Status.VALIDATED
        parse_run.result_hash = "e" * 64
        parse_run.completed_at = timezone.now()
        parse_run.save()
        parse_run.result_hash = "f" * 64

        with self.assertRaisesMessage(ValidationError, "immutable"):
            parse_run.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ParseRun.objects.filter(pk=parse_run.pk).update(status="UNLISTED")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ParseRun.objects.filter(pk=parse_run.pk).update(total_row_count=1)
