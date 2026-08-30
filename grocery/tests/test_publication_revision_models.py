import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.db.models.deletion import PROTECT, ProtectedError
from django.test import TestCase
from django.utils import timezone

from grocery.models import (
    ParseRun,
    PriceChangeFact,
    PublicationEntry,
    PublicationRevision,
    RetailPriceSnapshot,
    ReviewDecision,
    persist_reference_price_facts,
    seal_recent_publication,
)
from grocery.publication_facts import (
    FACT_SET_HASH_VERSION,
    build_publication_fact_set,
    snapshot_fact_sha256,
)
from grocery.tests.test_artifact_parse_models import create_artifact
from grocery.tests.test_price_series_key_models import create_series
from grocery.tests.test_retail_price_snapshot_models import (
    create_snapshot,
    create_validated_parse_run,
)
from grocery.tests.test_review_decision_models import (
    create_reviewer,
    record,
    review_arguments,
    source_for_parse,
)


def create_approved_generation(
    *,
    unavailable_month: bool = False,
    snapshot_count: int = 1,
    source_recorded_at: datetime | None = None,
) -> tuple[ReviewDecision, tuple[RetailPriceSnapshot, ...], Any]:
    if snapshot_count == 1:
        parse_run = create_validated_parse_run()
    else:
        completed_at = timezone.now()
        parse_run = ParseRun.objects.create(
            artifact=create_artifact(),
            parser_revision="kamis-recent-v1",
            configuration_hash="d" * 64,
            result_hash="e" * 64,
            status=ParseRun.Status.VALIDATED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            total_row_count=snapshot_count,
            accepted_row_count=snapshot_count,
        )

    snapshots: list[RetailPriceSnapshot] = []
    for index in range(snapshot_count):
        series = create_series(
            item_code=str(212 + index),
            item_name=f"품목 {212 + index}",
        )
        snapshot = create_snapshot(
            parse_run=parse_run,
            series=series,
            source_effective_date=date(2026, 8, 29) - timedelta(days=index),
            source_recorded_at=source_recorded_at,
            current_price=Decimal(8000 + index),
            source_row_sha256=str(index + 1).zfill(64),
        )
        persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values={
                "WEEK": Decimal("10000"),
                "MONTH": None if unavailable_month else Decimal("6400"),
                "YEAR": Decimal("8000"),
            },
        )
        snapshots.append(snapshot)

    source_configuration = source_for_parse(parse_run)
    reviewer = create_reviewer()
    decision, _ = record(
        **review_arguments(
            reviewer=reviewer,
            source_configuration=source_configuration,
            parse_run=parse_run,
        )
    )
    return decision, tuple(snapshots), reviewer


def publication_fields(
    decision: ReviewDecision,
    snapshots: tuple[RetailPriceSnapshot, ...],
    **overrides: object,
) -> dict[str, object]:
    fact_set = build_publication_fact_set(list(snapshots))
    fields: dict[str, object] = {
        "channel": PublicationRevision.Channel.RECENT_RETAIL,
        "mode": PublicationRevision.Mode.RECENT_COMPARISON,
        "review_decision": decision,
        "generation": decision.parse_run,
        "parser_revision": decision.parse_run.parser_revision,
        "fact_hash_version": FACT_SET_HASH_VERSION,
        "typed_fact_set_sha256": fact_set.typed_fact_set_sha256,
        "entry_count": len(fact_set.entries),
        "source_effective_date_min": fact_set.source_effective_date_min,
        "source_effective_date_max": fact_set.source_effective_date_max,
        "public_copy_revision": "ko-v1",
    }
    fields.update(overrides)
    return fields


def direct_seal(revision: PublicationRevision) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE grocery_publicationrevision SET sealed_at = %s WHERE id = %s",
            [timezone.now(), revision.id],
        )


class PublicationRevisionTests(TestCase):
    def test_seals_unavailable_reference_generation_as_complete_typed_membership(self) -> None:
        decision, snapshots, _ = create_approved_generation(unavailable_month=True)

        revision = seal_recent_publication(decision.id, "ko-v1")

        revision.refresh_from_db()
        self.assertIsInstance(revision.id, uuid.UUID)
        self.assertIsNotNone(revision.sealed_at)
        self.assertEqual(revision.channel, PublicationRevision.Channel.RECENT_RETAIL)
        self.assertEqual(revision.mode, PublicationRevision.Mode.RECENT_COMPARISON)
        self.assertEqual(revision.review_decision_id, decision.id)
        self.assertEqual(revision.generation_id, decision.parse_run_id)
        self.assertEqual(revision.parser_revision, decision.parse_run.parser_revision)
        self.assertEqual(revision.fact_hash_version, FACT_SET_HASH_VERSION)
        self.assertEqual(revision.entry_count, 1)
        entry = revision.entries.get()
        self.assertEqual(entry.ordinal, 1)
        self.assertEqual(entry.snapshot_id, snapshots[0].id)
        month = snapshots[0].reference_prices.get(period="MONTH")
        self.assertEqual(month.value_status, "UNAVAILABLE")
        self.assertEqual(month.change_fact.direction, PriceChangeFact.Direction.UNAVAILABLE)

    def test_exact_replay_returns_same_revision_and_copy_change_creates_new_revision(self) -> None:
        decision, _, _ = create_approved_generation()

        first = seal_recent_publication(decision.id, "ko-v1")
        repeated = seal_recent_publication(decision.id, "ko-v1")
        copy_change = seal_recent_publication(decision.id, "ko-v2")

        self.assertEqual(first.id, repeated.id)
        self.assertNotEqual(first.id, copy_change.id)
        self.assertEqual(first.typed_fact_set_sha256, copy_change.typed_fact_set_sha256)
        self.assertEqual(PublicationRevision.objects.count(), 2)

    def test_superseded_or_rejected_review_tail_cannot_publish_and_conflict_fails_closed(
        self,
    ) -> None:
        root, snapshots, reviewer = create_approved_generation()
        first = seal_recent_publication(root.id, "ko-v1")
        source = source_for_parse(root.parse_run)
        replacement, _ = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source,
                parse_run=root.parse_run,
                supersedes_id=root.id,
            )
        )

        with self.assertRaisesMessage(ValidationError, "latest"):
            seal_recent_publication(root.id, "ko-v2")
        with self.assertRaisesMessage(ValidationError, "conflicts"):
            seal_recent_publication(replacement.id, "ko-v1")

        rejected, _ = record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source,
                parse_run=root.parse_run,
                decision=ReviewDecision.Decision.REJECT,
                reason_code="PUBLICATION_REJECTED",
                approved_mode="",
                approved_coverage_identity="",
                approved_coverage_evidence_revision="",
                supersedes_id=replacement.id,
            )
        )
        with self.assertRaisesMessage(ValidationError, "APPROVE"):
            seal_recent_publication(rejected.id, "ko-v3")

        self.assertEqual(PublicationRevision.objects.get(), first)
        self.assertEqual(first.entries.get().snapshot_id, snapshots[0].id)

    def test_invalid_copy_token_and_injected_entry_failure_roll_back_revision(self) -> None:
        decision, _, _ = create_approved_generation()

        with self.assertRaises(ValidationError):
            seal_recent_publication(decision.id, "KO V1")
        self.assertFalse(PublicationRevision.objects.exists())

        with patch.object(PublicationEntry, "save", side_effect=RuntimeError("injected")):
            with self.assertRaisesMessage(RuntimeError, "injected"):
                seal_recent_publication(decision.id, "ko-v1")
        self.assertFalse(PublicationRevision.objects.exists())
        self.assertFalse(PublicationEntry.objects.exists())

    def test_database_seal_rejects_incomplete_noncontiguous_or_wrong_hash_membership(
        self,
    ) -> None:
        decision, snapshots, _ = create_approved_generation(snapshot_count=2)
        fact_set = build_publication_fact_set(list(snapshots))

        incomplete = PublicationRevision.objects.create(**publication_fields(decision, snapshots))
        with self.assertRaisesMessage(DatabaseError, "complete"), transaction.atomic():
            direct_seal(incomplete)

        noncontiguous = PublicationRevision.objects.create(
            **publication_fields(decision, snapshots, public_copy_revision="ko-v2")
        )
        PublicationEntry.objects.bulk_create(
            [
                PublicationEntry(
                    revision=noncontiguous,
                    snapshot_id=entry.snapshot_id,
                    ordinal=entry.ordinal * 2 - 1,
                    fact_sha256=entry.fact_sha256,
                )
                for entry in fact_set.entries
            ]
        )
        with self.assertRaisesMessage(DatabaseError, "contiguous"), transaction.atomic():
            direct_seal(noncontiguous)

        wrong_hash = PublicationRevision.objects.create(
            **publication_fields(
                decision,
                snapshots,
                typed_fact_set_sha256="0" * 64,
                public_copy_revision="ko-v3",
            )
        )
        PublicationEntry.objects.bulk_create(
            [
                PublicationEntry(
                    revision=wrong_hash,
                    snapshot_id=entry.snapshot_id,
                    ordinal=entry.ordinal,
                    fact_sha256=entry.fact_sha256,
                )
                for entry in fact_set.entries
            ]
        )
        with self.assertRaisesMessage(DatabaseError, "fact-set hash"), transaction.atomic():
            direct_seal(wrong_hash)

        self.assertFalse(
            PublicationRevision.objects.filter(
                id__in=(incomplete.id, noncontiguous.id, wrong_hash.id),
                sealed_at__isnull=False,
            ).exists()
        )

    def test_database_rejects_mixed_generation_entry_before_seal(self) -> None:
        decision, snapshots, _ = create_approved_generation()
        completed_at = timezone.now()
        other_parse = ParseRun.objects.create(
            artifact=create_artifact(),
            parser_revision="kamis-recent-v1",
            configuration_hash=uuid.uuid4().hex * 2,
            result_hash=uuid.uuid4().hex * 2,
            status=ParseRun.Status.VALIDATED,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            total_row_count=1,
            accepted_row_count=1,
        )
        other_snapshot = create_snapshot(
            parse_run=other_parse,
            series=create_series(item_code="999", item_name="다른 품목"),
            source_row_sha256="9" * 64,
        )
        revision = PublicationRevision.objects.create(**publication_fields(decision, snapshots))

        with self.assertRaisesMessage(DatabaseError, "does not match"), transaction.atomic():
            PublicationEntry.objects.bulk_create(
                [
                    PublicationEntry(
                        revision=revision,
                        snapshot=other_snapshot,
                        ordinal=1,
                        fact_sha256="a" * 64,
                    )
                ]
            )

        self.assertFalse(PublicationEntry.objects.filter(revision=revision).exists())

        with (
            self.assertRaisesMessage(DatabaseError, "canonical snapshot facts"),
            transaction.atomic(),
        ):
            PublicationEntry.objects.bulk_create(
                [
                    PublicationEntry(
                        revision=revision,
                        snapshot=snapshots[0],
                        ordinal=1,
                        fact_sha256="a" * 64,
                    )
                ]
            )

    def test_database_canonical_entry_hash_matches_python_contract(self) -> None:
        _, snapshots, _ = create_approved_generation(
            unavailable_month=True,
            source_recorded_at=datetime(2026, 8, 30, 1, 2, 3, 123456, tzinfo=UTC),
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT grocery_publication_snapshot_fact_sha256(%s)",
                [snapshots[0].id],
            )
            database_hash = cursor.fetchone()[0]

        self.assertEqual(database_hash, snapshot_fact_sha256(snapshots[0]))

    def test_database_rejects_revision_inserted_as_already_sealed(self) -> None:
        decision, snapshots, _ = create_approved_generation()
        invalid = PublicationRevision(
            **publication_fields(decision, snapshots),
            sealed_at=timezone.now(),
        )

        with self.assertRaisesMessage(DatabaseError, "inserted unsealed"), transaction.atomic():
            PublicationRevision.objects.bulk_create([invalid])

        self.assertFalse(PublicationRevision.objects.exists())

    def test_sealed_revision_and_entries_are_immutable_in_orm_and_direct_sql(self) -> None:
        decision, _, _ = create_approved_generation()
        revision = seal_recent_publication(decision.id, "ko-v1")
        entry = revision.entries.get()

        orm_mutations: tuple[Callable[[], object], ...] = (
            lambda: revision.save(),
            lambda: revision.delete(),
            lambda: entry.save(),
            lambda: entry.delete(),
        )
        for mutation in orm_mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValidationError):
                    mutation()

        sql_mutations: tuple[tuple[str, list[Any]], ...] = (
            (
                "UPDATE grocery_publicationrevision SET public_copy_revision = %s WHERE id = %s",
                ["ko-mutated", revision.id],
            ),
            ("DELETE FROM grocery_publicationrevision WHERE id = %s", [revision.id]),
            (
                "UPDATE grocery_publicationentry SET ordinal = %s WHERE id = %s",
                [2, entry.id],
            ),
            ("DELETE FROM grocery_publicationentry WHERE id = %s", [entry.id]),
        )
        for statement, parameters in sql_mutations:
            with self.subTest(statement=statement):
                with self.assertRaises(DatabaseError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(statement, parameters)

    def test_foreign_keys_protect_approved_evidence_and_snapshot_membership(self) -> None:
        decision, snapshots, _ = create_approved_generation()
        revision = seal_recent_publication(decision.id, "ko-v1")

        protected_deletes: tuple[Callable[[], object], ...] = (
            lambda: ReviewDecision.objects.filter(pk=decision.pk).delete(),
            lambda: ParseRun.objects.filter(pk=decision.parse_run_id).delete(),
            lambda: RetailPriceSnapshot.objects.filter(pk=snapshots[0].pk).delete(),
        )
        for protected_delete in protected_deletes:
            with self.subTest(protected_delete=protected_delete):
                with self.assertRaises(ProtectedError):
                    protected_delete()

        self.assertTrue(PublicationRevision.objects.filter(pk=revision.pk).exists())

    def test_schema_is_recent_typed_and_contains_no_generic_or_json_payload(self) -> None:
        revision_fields = {field.name for field in PublicationRevision._meta.fields}
        entry_fields = {field.name for field in PublicationEntry._meta.fields}

        self.assertFalse(
            {"payload", "data", "content_type", "object_id", "monthly_snapshot"}
            & (revision_fields | entry_fields)
        )
        self.assertEqual(list(PublicationRevision.Channel), ["RECENT_RETAIL"])
        self.assertEqual(list(PublicationRevision.Mode), ["RECENT_COMPARISON"])
        self.assertEqual(
            PublicationRevision._meta.get_field("review_decision").remote_field.on_delete,
            PROTECT,
        )
