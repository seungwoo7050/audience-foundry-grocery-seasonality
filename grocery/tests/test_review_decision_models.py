import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from grocery.models import (
    FetchAttempt,
    ParseRun,
    PriceChangeFact,
    ReviewDecision,
    SourceArtifact,
    SourceConfiguration,
    record_review_decision,
)
from grocery.tests.test_artifact_parse_models import create_artifact
from grocery.tests.test_reference_price_models import (
    create_reference_triplet,
    reference_values,
)
from grocery.tests.test_retail_price_snapshot_models import (
    create_snapshot,
    create_validated_parse_run,
)


def create_reviewer(*, active: bool = True, permitted: bool = True) -> Any:
    user_model = get_user_model()
    reviewer = user_model.objects.create_user(
        username=f"reviewer-{uuid.uuid4()}",
        password=None,
        is_active=active,
    )
    if permitted:
        reviewer.user_permissions.add(Permission.objects.get(codename="review_generation"))
        reviewer = user_model.objects.get(pk=reviewer.pk)
    return reviewer


def source_for_parse(parse_run: ParseRun) -> SourceConfiguration:
    return FetchAttempt.objects.get(
        artifact=parse_run.artifact,
        state=FetchAttempt.State.SUCCEEDED,
    ).source_configuration


def complete_generation() -> tuple[SourceConfiguration, ParseRun]:
    parse_run = create_validated_parse_run()
    snapshot = create_snapshot(parse_run=parse_run)
    create_reference_triplet(snapshot)
    for reference in snapshot.reference_prices.all():
        PriceChangeFact.get_or_validate(reference_price_id=reference.id)
    return source_for_parse(parse_run), parse_run


def review_arguments(
    *,
    reviewer: Any,
    source_configuration: SourceConfiguration,
    parse_run: ParseRun,
    decision_id: Any = None,
    **overrides: Any,
) -> dict[str, object]:
    values: dict[str, object] = {
        "decision_id": decision_id or uuid.uuid4(),
        "actor": reviewer,
        "decision": ReviewDecision.Decision.APPROVE,
        "source_configuration_id": source_configuration.id,
        "source_artifact_id": parse_run.artifact_id,
        "parse_run_id": parse_run.id,
        "reconciliation_report_sha256": "6" * 64,
        "acceptance_evidence_sha256": "7" * 64,
        "reason_code": "GENERATION_ACCEPTED",
        "approved_mode": SourceConfiguration.PublicationMode.RECENT_COMPARISON,
        "approved_coverage_identity": source_configuration.coverage_identity,
        "approved_coverage_evidence_revision": (source_configuration.coverage_evidence_revision),
    }
    values.update(overrides)
    return values


def record(**values: object) -> tuple[ReviewDecision, bool]:
    return record_review_decision(**values)  # type: ignore[arg-type]


class ReviewDecisionTests(TestCase):
    def test_authorized_approval_records_only_actor_reference_and_exact_evidence(self) -> None:
        source_configuration, parse_run = complete_generation()
        reviewer = create_reviewer()

        decision, created = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source_configuration,
                parse_run=parse_run,
            )
        )

        self.assertTrue(created)
        self.assertIsInstance(decision.id, uuid.UUID)
        self.assertEqual(decision.reviewer_id, reviewer.pk)
        self.assertEqual(decision.decision, ReviewDecision.Decision.APPROVE)
        self.assertEqual(decision.approved_mode, source_configuration.publication_mode)
        self.assertEqual(
            decision.approved_coverage_identity,
            source_configuration.coverage_identity,
        )
        self.assertEqual(
            decision.approved_coverage_evidence_revision,
            source_configuration.coverage_evidence_revision,
        )
        self.assertEqual(len(decision.reconciliation_report_sha256), 64)
        self.assertEqual(len(decision.acceptance_evidence_sha256), 64)
        field_names = {field.name for field in ReviewDecision._meta.fields}
        self.assertFalse(
            {"reviewer_name", "reviewer_email", "username", "email", "display_name"} & field_names
        )

    def test_service_requires_authenticated_active_permitted_reviewer(self) -> None:
        source_configuration, parse_run = complete_generation()
        denied_reviewers = (
            create_reviewer(permitted=False),
            create_reviewer(active=False),
        )

        for reviewer in denied_reviewers:
            with self.subTest(reviewer=reviewer.pk):
                with self.assertRaises(PermissionDenied):
                    record(
                        **review_arguments(
                            reviewer=reviewer,
                            source_configuration=source_configuration,
                            parse_run=parse_run,
                        )
                    )

        class AnonymousActor:
            pk = None
            is_authenticated = False
            is_active = False

            def has_perm(self, _permission: str) -> bool:
                return False

        with self.assertRaises(PermissionDenied):
            record(
                **review_arguments(
                    reviewer=AnonymousActor(),
                    source_configuration=source_configuration,
                    parse_run=parse_run,
                )
            )
        self.assertFalse(ReviewDecision.objects.exists())

    def test_approval_waits_for_exact_snapshot_reference_and_change_fact_set(self) -> None:
        reviewer = create_reviewer()
        parse_run = create_validated_parse_run()
        source_configuration = source_for_parse(parse_run)
        arguments = review_arguments(
            reviewer=reviewer,
            source_configuration=source_configuration,
            parse_run=parse_run,
        )

        with self.assertRaisesMessage(ValidationError, "Snapshot count"):
            record(**arguments)

        snapshot = create_snapshot(parse_run=parse_run)
        with self.assertRaisesMessage(ValidationError, "exact WEEK, MONTH, and YEAR"):
            record(**arguments)

        references = create_reference_triplet(snapshot, values=reference_values())
        with self.assertRaisesMessage(ValidationError, "price change fact"):
            record(**arguments)

        for reference in references:
            PriceChangeFact.get_or_validate(reference_price_id=reference.id)
        decision, created = record(**arguments)

        self.assertTrue(created)
        self.assertEqual(decision.parse_run_id, parse_run.id)

    def test_approval_requires_active_rights_matching_fetch_parse_mode_and_coverage(self) -> None:
        source_configuration, parse_run = complete_generation()
        reviewer = create_reviewer()

        mismatch_variants: tuple[dict[str, object], ...] = (
            {"approved_mode": SourceConfiguration.PublicationMode.CURRENT_ONLY},
            {"approved_coverage_identity": "UNVERIFIED_COVERAGE"},
            {"approved_coverage_evidence_revision": "unreviewed"},
        )
        for overrides in mismatch_variants:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    record(
                        **review_arguments(
                            reviewer=reviewer,
                            source_configuration=source_configuration,
                            parse_run=parse_run,
                            **overrides,
                        )
                    )

        unrelated_artifact = SourceArtifact.objects.create(
            source_identity=source_configuration.artifact_source_identity,
            ordered_manifest_sha256="f" * 64,
            page_count=1,
            total_bytes=1,
            first_seen_at=timezone.now(),
        )
        with self.assertRaisesMessage(ValidationError, "linked"):
            record(
                **review_arguments(
                    reviewer=reviewer,
                    source_configuration=source_configuration,
                    parse_run=parse_run,
                    source_artifact_id=unrelated_artifact.id,
                )
            )

        SourceConfiguration.objects.filter(pk=source_configuration.pk).update(state="PAUSED")
        source_configuration.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "active source"):
            record(
                **review_arguments(
                    reviewer=reviewer,
                    source_configuration=source_configuration,
                    parse_run=parse_run,
                )
            )

    def test_reject_allows_only_completed_parse_and_has_no_approved_fields(self) -> None:
        reviewer = create_reviewer()
        artifact = create_artifact()
        source_configuration = source_for_parse(
            ParseRun.objects.create(
                artifact=artifact,
                parser_revision="temporary-link-v1",
                configuration_hash="8" * 64,
            )
        )
        started = artifact.parse_runs.get(parser_revision="temporary-link-v1")
        rejected_fields = {
            "decision": ReviewDecision.Decision.REJECT,
            "reason_code": "PARSE_RECONCILIATION_FAILED",
            "approved_mode": "",
            "approved_coverage_identity": "",
            "approved_coverage_evidence_revision": "",
        }

        with self.assertRaisesMessage(ValidationError, "completed parse"):
            record(
                **review_arguments(
                    reviewer=reviewer,
                    source_configuration=source_configuration,
                    parse_run=started,
                    **rejected_fields,
                )
            )

        completed_at = timezone.now()
        quarantined = ParseRun.objects.create(
            artifact=artifact,
            parser_revision="quarantined-v1",
            configuration_hash="9" * 64,
            status=ParseRun.Status.QUARANTINED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            total_row_count=1,
            quarantined_row_count=1,
            failure_code="IDENTITY_DRIFT",
        )
        rejected, created = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source_configuration,
                parse_run=quarantined,
                **rejected_fields,
            )
        )

        self.assertTrue(created)
        self.assertEqual(rejected.decision, ReviewDecision.Decision.REJECT)
        self.assertEqual(rejected.approved_mode, "")
        self.assertEqual(rejected.approved_coverage_identity, "")
        self.assertEqual(rejected.approved_coverage_evidence_revision, "")

    def test_uuid_replay_is_idempotent_and_conflicting_replay_fails_closed(self) -> None:
        source_configuration, parse_run = complete_generation()
        reviewer = create_reviewer()
        decision_id = uuid.uuid4()
        arguments = review_arguments(
            reviewer=reviewer,
            source_configuration=source_configuration,
            parse_run=parse_run,
            decision_id=decision_id,
        )

        first, first_created = record(**arguments)
        repeated, repeated_created = record(**arguments)

        self.assertTrue(first_created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.id, repeated.id)
        self.assertEqual(ReviewDecision.objects.count(), 1)
        with self.assertRaisesMessage(ValidationError, "UUID replay conflicts"):
            record(**{**arguments, "reason_code": "CONFLICTING_REPLAY"})

    def test_supersession_is_linear_current_tail_and_same_generation_only(self) -> None:
        source_configuration, parse_run = complete_generation()
        reviewer = create_reviewer()
        reject_fields = {
            "decision": ReviewDecision.Decision.REJECT,
            "reason_code": "MANUAL_REVIEW_REJECTED",
            "approved_mode": "",
            "approved_coverage_identity": "",
            "approved_coverage_evidence_revision": "",
        }
        root, _ = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source_configuration,
                parse_run=parse_run,
                **reject_fields,
            )
        )
        approved, _ = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source_configuration,
                parse_run=parse_run,
                supersedes_id=root.id,
            )
        )

        self.assertEqual(approved.supersedes_id, root.id)
        with self.assertRaises(ValidationError):
            record(
                **review_arguments(
                    reviewer=reviewer,
                    source_configuration=source_configuration,
                    parse_run=parse_run,
                    supersedes_id=root.id,
                )
            )

        completed_at = timezone.now()
        other_parse = ParseRun.objects.create(
            artifact=parse_run.artifact,
            parser_revision="kamis-recent-v2",
            configuration_hash="a" * 64,
            result_hash="b" * 64,
            status=ParseRun.Status.VALIDATED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            total_row_count=1,
            accepted_row_count=1,
        )
        reviewed_series = parse_run.retail_price_snapshots.get().series
        other_snapshot = create_snapshot(parse_run=other_parse, series=reviewed_series)
        other_references = create_reference_triplet(other_snapshot)
        for reference in other_references:
            PriceChangeFact.get_or_validate(reference_price_id=reference.id)
        other_source = source_configuration
        with self.assertRaisesMessage(ValidationError, "same generation"):
            record(
                **review_arguments(
                    reviewer=reviewer,
                    source_configuration=other_source,
                    parse_run=other_parse,
                    supersedes_id=approved.id,
                )
            )

    def test_database_trigger_rejects_incomplete_generation_when_validation_is_bypassed(
        self,
    ) -> None:
        reviewer = create_reviewer()
        parse_run = create_validated_parse_run()
        source_configuration = source_for_parse(parse_run)
        invalid = ReviewDecision(
            id=uuid.uuid4(),
            reviewer=reviewer,
            decision=ReviewDecision.Decision.APPROVE,
            source_configuration=source_configuration,
            source_artifact=parse_run.artifact,
            parse_run=parse_run,
            reconciliation_report_sha256="6" * 64,
            acceptance_evidence_sha256="7" * 64,
            reason_code="GENERATION_ACCEPTED",
            approved_mode=SourceConfiguration.PublicationMode.RECENT_COMPARISON,
            approved_coverage_identity=source_configuration.coverage_identity,
            approved_coverage_evidence_revision=(source_configuration.coverage_evidence_revision),
        )

        with self.assertRaises(DatabaseError), transaction.atomic():
            ReviewDecision.objects.bulk_create([invalid])

        self.assertFalse(ReviewDecision.objects.exists())

    def test_decisions_are_append_only_in_orm_and_direct_sql(self) -> None:
        source_configuration, parse_run = complete_generation()
        reviewer = create_reviewer()
        decision, _ = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source_configuration,
                parse_run=parse_run,
            )
        )

        orm_mutations: tuple[Callable[[], object], ...] = (
            lambda: decision.save(),
            lambda: decision.delete(),
        )
        for mutation in orm_mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesMessage(ValidationError, "append-only"):
                    mutation()

        sql_mutations: tuple[tuple[str, list[Any]], ...] = (
            (
                "UPDATE grocery_reviewdecision SET reason_code = %s WHERE id = %s",
                ["MUTATED", decision.id],
            ),
            ("DELETE FROM grocery_reviewdecision WHERE id = %s", [decision.id]),
        )
        for statement, parameters in sql_mutations:
            with self.subTest(statement=statement):
                with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(statement, parameters)

        decision.refresh_from_db()
        self.assertEqual(decision.reason_code, "GENERATION_ACCEPTED")

    def test_database_checks_and_foreign_keys_fail_closed(self) -> None:
        source_configuration, parse_run = complete_generation()
        reviewer = create_reviewer()
        invalid_variants: tuple[dict[str, object], ...] = (
            {"decision": "UNKNOWN"},
            {"reason_code": "lowercase"},
            {"reconciliation_report_sha256": "A" * 64},
            {"approved_mode": ""},
        )
        for overrides in invalid_variants:
            values = review_arguments(
                reviewer=reviewer,
                source_configuration=source_configuration,
                parse_run=parse_run,
                **overrides,
            )
            values.pop("actor")
            values["id"] = values.pop("decision_id")
            values["reviewer_id"] = reviewer.pk
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    ReviewDecision.objects.bulk_create([ReviewDecision(**values)])

        decision, _ = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source_configuration,
                parse_run=parse_run,
            )
        )
        protected_deletes: tuple[Callable[[], object], ...] = (
            lambda: reviewer.delete(),
            lambda: decision.source_configuration.delete(),
            lambda: decision.source_artifact.delete(),
            lambda: decision.parse_run.delete(),
        )
        for protected_delete in protected_deletes:
            with self.subTest(protected_delete=protected_delete):
                with self.assertRaises(ProtectedError):
                    protected_delete()
