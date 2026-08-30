import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from grocery.models import ParseRun, PriceSeriesKey, RetailPriceSnapshot
from grocery.tests.test_artifact_parse_models import create_artifact
from grocery.tests.test_price_series_key_models import create_series


def create_validated_parse_run() -> ParseRun:
    completed_at = timezone.now()
    return ParseRun.objects.create(
        artifact=create_artifact(),
        parser_revision="kamis-recent-v1",
        configuration_hash="d" * 64,
        result_hash="e" * 64,
        status=ParseRun.Status.VALIDATED,
        started_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
        total_row_count=1,
        accepted_row_count=1,
    )


def snapshot_fields(
    parse_run: ParseRun,
    series: PriceSeriesKey,
    **overrides: object,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "parse_run": parse_run,
        "series": series,
        "source_effective_date": date(2026, 8, 29),
        "source_recorded_at": None,
        "current_price": Decimal("2400"),
        "currency": "KRW",
        "source_row_sha256": "a" * 64,
        "source_contract_revision": "data-go-15156063-recent-v1",
    }
    fields.update(overrides)
    return fields


def create_snapshot(
    *,
    parse_run: ParseRun | None = None,
    series: PriceSeriesKey | None = None,
    **overrides: object,
) -> RetailPriceSnapshot:
    return RetailPriceSnapshot.objects.create(
        **snapshot_fields(
            parse_run or create_validated_parse_run(),
            series or create_series(),
            **overrides,
        )
    )


def replay_snapshot(
    *,
    parse_run: ParseRun | None = None,
    series: PriceSeriesKey | None = None,
    source_effective_date: date = date(2026, 8, 29),
    source_recorded_at: datetime | None = None,
    current_price: Decimal = Decimal("2400"),
    source_row_sha256: str = "a" * 64,
    source_contract_revision: str = "data-go-15156063-recent-v1",
) -> RetailPriceSnapshot:
    selected_parse_run = parse_run or create_validated_parse_run()
    selected_series = series or create_series()
    return RetailPriceSnapshot.get_or_validate(
        parse_run_id=selected_parse_run.id,
        series_id=selected_series.id,
        source_effective_date=source_effective_date,
        source_recorded_at=source_recorded_at,
        current_price=current_price,
        source_row_sha256=source_row_sha256,
        source_contract_revision=source_contract_revision,
    )


class RetailPriceSnapshotTests(TestCase):
    def test_valid_snapshot_preserves_source_time_and_scale_zero_krw(self) -> None:
        snapshot = create_snapshot()

        self.assertIsInstance(snapshot.id, uuid.UUID)
        self.assertEqual(snapshot.source_effective_date, date(2026, 8, 29))
        self.assertIsNone(snapshot.source_recorded_at)
        self.assertEqual(snapshot.current_price, Decimal("2400"))
        self.assertEqual(snapshot.currency, RetailPriceSnapshot.Currency.KRW)
        self.assertEqual(
            RetailPriceSnapshot._meta.get_field("current_price").max_digits,
            12,
        )
        self.assertEqual(
            RetailPriceSnapshot._meta.get_field("current_price").decimal_places,
            0,
        )

    def test_only_validated_parse_runs_can_create_snapshots(self) -> None:
        started = ParseRun.objects.create(
            artifact=create_artifact(),
            parser_revision="kamis-recent-v1",
            configuration_hash="f" * 64,
        )
        series = create_series()

        with self.assertRaisesMessage(ValidationError, "validated parse run"):
            create_snapshot(parse_run=started, series=series)
        with self.assertRaisesMessage(ValidationError, "validated parse run"):
            RetailPriceSnapshot.get_or_validate(
                parse_run_id=started.id,
                series_id=series.id,
                source_effective_date=date(2026, 8, 29),
                source_recorded_at=None,
                current_price=Decimal("2400"),
                source_row_sha256="a" * 64,
                source_contract_revision="data-go-15156063-recent-v1",
            )

        self.assertFalse(RetailPriceSnapshot.objects.exists())

    def test_replay_is_idempotent_and_conflicts_fail_closed(self) -> None:
        parse_run = create_validated_parse_run()
        series = create_series()
        original = replay_snapshot(parse_run=parse_run, series=series)
        repeated = replay_snapshot(parse_run=parse_run, series=series)

        self.assertEqual(repeated.id, original.id)
        self.assertEqual(RetailPriceSnapshot.objects.count(), 1)

        conflict_variants: tuple[Callable[[], RetailPriceSnapshot], ...] = (
            lambda: replay_snapshot(
                parse_run=parse_run,
                series=series,
                source_effective_date=date(2026, 8, 30),
            ),
            lambda: replay_snapshot(
                parse_run=parse_run,
                series=series,
                source_recorded_at=timezone.now(),
            ),
            lambda: replay_snapshot(
                parse_run=parse_run,
                series=series,
                current_price=Decimal("2500"),
            ),
            lambda: replay_snapshot(
                parse_run=parse_run,
                series=series,
                source_row_sha256="b" * 64,
            ),
            lambda: replay_snapshot(
                parse_run=parse_run,
                series=series,
                source_contract_revision="data-go-15156063-recent-v2",
            ),
        )
        for conflict in conflict_variants:
            with self.subTest(conflict=conflict):
                with self.assertRaisesMessage(ValidationError, "conflicts"):
                    conflict()

        self.assertEqual(RetailPriceSnapshot.objects.count(), 1)

    def test_unique_constraints_cover_generation_series_and_source_date(self) -> None:
        original = create_snapshot()
        duplicate = RetailPriceSnapshot(**snapshot_fields(original.parse_run, original.series))
        with self.assertRaises(IntegrityError), transaction.atomic():
            RetailPriceSnapshot.objects.bulk_create([duplicate])

        changed_date = RetailPriceSnapshot(
            **snapshot_fields(
                original.parse_run,
                original.series,
                source_effective_date=date(2026, 8, 30),
            )
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            RetailPriceSnapshot.objects.bulk_create([changed_date])

        self.assertEqual(RetailPriceSnapshot.objects.count(), 1)

    def test_database_checks_reject_price_currency_hash_and_contract_drift(self) -> None:
        invalid_variants: tuple[dict[str, object], ...] = (
            {"current_price": Decimal("0")},
            {"current_price": Decimal("-1")},
            {"currency": "USD"},
            {"source_row_sha256": "A" * 64},
            {"source_contract_revision": ""},
        )
        parse_run = create_validated_parse_run()
        series = create_series()
        for overrides in invalid_variants:
            with self.subTest(overrides=overrides):
                invalid = RetailPriceSnapshot(**snapshot_fields(parse_run, series, **overrides))
                with self.assertRaises(IntegrityError), transaction.atomic():
                    RetailPriceSnapshot.objects.bulk_create([invalid])

        self.assertFalse(RetailPriceSnapshot.objects.exists())

    def test_scale_and_precision_overflow_fail_without_coercion(self) -> None:
        parse_run = create_validated_parse_run()
        series = create_series()

        for invalid_price in (Decimal("2400.5"), Decimal("1000000000000")):
            with self.subTest(invalid_price=invalid_price):
                with self.assertRaises(ValidationError):
                    RetailPriceSnapshot.get_or_validate(
                        parse_run_id=parse_run.id,
                        series_id=series.id,
                        source_effective_date=date(2026, 8, 29),
                        source_recorded_at=None,
                        current_price=invalid_price,
                        source_row_sha256="a" * 64,
                        source_contract_revision="data-go-15156063-recent-v1",
                    )

        overflow = RetailPriceSnapshot(
            **snapshot_fields(
                parse_run,
                series,
                current_price=Decimal("1000000000000"),
            )
        )
        with self.assertRaises(DatabaseError), transaction.atomic():
            RetailPriceSnapshot.objects.bulk_create([overflow])

        self.assertFalse(RetailPriceSnapshot.objects.exists())

    def test_snapshot_is_immutable_through_orm_and_direct_sql(self) -> None:
        snapshot = create_snapshot()
        snapshot.current_price = Decimal("2500")

        with self.assertRaisesMessage(ValidationError, "immutable"):
            snapshot.save()
        with self.assertRaisesMessage(ValidationError, "immutable"):
            snapshot.delete()

        with self.assertRaisesMessage(DatabaseError, "immutable"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE grocery_retailpricesnapshot SET current_price = %s WHERE id = %s",
                    [Decimal("2500"), snapshot.id],
                )

        with self.assertRaisesMessage(DatabaseError, "immutable"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM grocery_retailpricesnapshot WHERE id = %s",
                    [snapshot.id],
                )

        snapshot.refresh_from_db()
        self.assertEqual(snapshot.current_price, Decimal("2400"))

    def test_parse_run_and_series_references_are_protected(self) -> None:
        snapshot = create_snapshot()

        with self.assertRaises(ProtectedError):
            snapshot.parse_run.delete()
        with self.assertRaises(ProtectedError):
            type(snapshot.series).objects.filter(pk=snapshot.series_id).delete()
