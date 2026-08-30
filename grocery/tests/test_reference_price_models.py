import uuid
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import PROTECT, ProtectedError
from django.test import TestCase

from grocery.models import (
    PriceChangeFact,
    ReferencePrice,
    RetailPriceSnapshot,
    persist_reference_price_facts,
)
from grocery.tests.test_retail_price_snapshot_models import create_snapshot

PERIODS = (
    ReferencePrice.Period.WEEK,
    ReferencePrice.Period.MONTH,
    ReferencePrice.Period.YEAR,
)


def reference_values(**overrides: Decimal | None) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {
        "WEEK": Decimal("10000"),
        "MONTH": Decimal("6400"),
        "YEAR": Decimal("8000"),
    }
    values.update(overrides)
    return values


def create_reference_triplet(
    snapshot: RetailPriceSnapshot,
    *,
    values: Mapping[str, Decimal | None] | None = None,
) -> tuple[ReferencePrice, ...]:
    selected_values = values or reference_values()
    references: list[ReferencePrice] = []
    for period in PERIODS:
        value = selected_values[period]
        references.append(
            ReferencePrice.get_or_validate(
                snapshot_id=snapshot.id,
                period=period,
                value_status=(
                    ReferencePrice.ValueStatus.AVAILABLE
                    if value is not None
                    else ReferencePrice.ValueStatus.UNAVAILABLE
                ),
                value=value,
                unavailable_reason=(
                    None
                    if value is not None
                    else ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING
                ),
            )
        )
    return tuple(references)


class ReferencePriceFactTests(TestCase):
    def test_exact_triplet_persists_half_up_comparisons_in_public_order(self) -> None:
        snapshot = create_snapshot(current_price=Decimal("8000"))

        facts = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values=reference_values(),
        )

        self.assertEqual(
            [fact.reference_price.period for fact in facts],
            list(PERIODS),
        )
        self.assertEqual(
            [fact.direction for fact in facts],
            [
                PriceChangeFact.Direction.LOWER,
                PriceChangeFact.Direction.HIGHER,
                PriceChangeFact.Direction.EQUAL,
            ],
        )
        self.assertEqual(
            [fact.signed_difference for fact in facts],
            [Decimal("-2000"), Decimal("1600"), Decimal("0")],
        )
        self.assertEqual(
            [fact.signed_percentage for fact in facts],
            [Decimal("-20.0"), Decimal("25.0"), Decimal("0.0")],
        )
        self.assertTrue(
            all(
                fact.calculation_revision == PriceChangeFact.CALCULATION_REVISION
                and fact.rounding_mode == PriceChangeFact.RoundingMode.ROUND_HALF_UP
                and fact.reference_price.snapshot_id == snapshot.id
                and fact.reference_price.snapshot.series_id == snapshot.series_id
                and fact.reference_price.snapshot.currency == RetailPriceSnapshot.Currency.KRW
                for fact in facts
            )
        )
        self.assertEqual(ReferencePrice._meta.get_field("value").max_digits, 12)
        self.assertEqual(ReferencePrice._meta.get_field("value").decimal_places, 0)
        self.assertEqual(PriceChangeFact._meta.get_field("signed_difference").max_digits, 12)
        self.assertEqual(PriceChangeFact._meta.get_field("signed_difference").decimal_places, 0)
        self.assertEqual(PriceChangeFact._meta.get_field("signed_percentage").max_digits, 16)
        self.assertEqual(PriceChangeFact._meta.get_field("signed_percentage").decimal_places, 1)

    def test_missing_source_value_has_no_arithmetic_fact(self) -> None:
        snapshot = create_snapshot(current_price=Decimal("8000"))
        values = reference_values(MONTH=None)

        _, month, _ = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values=values,
        )

        self.assertEqual(month.reference_price.value_status, ReferencePrice.ValueStatus.UNAVAILABLE)
        self.assertIsNone(month.reference_price.value)
        self.assertEqual(
            month.reference_price.unavailable_reason,
            ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING,
        )
        self.assertEqual(month.direction, PriceChangeFact.Direction.UNAVAILABLE)
        self.assertIsNone(month.signed_difference)
        self.assertIsNone(month.signed_percentage)

    def test_reference_dates_remain_explicitly_unavailable_without_derivation(self) -> None:
        snapshot = create_snapshot()
        facts = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values=reference_values(),
        )

        for fact in facts:
            self.assertEqual(
                fact.reference_price.reference_date_status,
                ReferencePrice.ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE,
            )
            self.assertIsNone(fact.reference_price.source_reference_date)

        with self.assertRaisesMessage(ValidationError, "does not provide reference dates"):
            ReferencePrice.get_or_validate(
                snapshot_id=snapshot.id,
                period=ReferencePrice.Period.WEEK,
                value_status=ReferencePrice.ValueStatus.AVAILABLE,
                value=Decimal("2200"),
                unavailable_reason=None,
                source_reference_date=date(2026, 8, 22),
            )

        with self.assertRaises(ValidationError):
            ReferencePrice.get_or_validate(
                snapshot_id=snapshot.id,
                period=ReferencePrice.Period.WEEK,
                value_status=ReferencePrice.ValueStatus.AVAILABLE,
                value=Decimal("2200"),
                unavailable_reason=None,
                reference_date_status="PROVIDED",
                source_reference_date=date(2026, 8, 22),
            )

    def test_helper_requires_exactly_three_periods_and_is_atomic(self) -> None:
        snapshot = create_snapshot()
        invalid_inputs: tuple[dict[str, Decimal | None], ...] = (
            {"WEEK": Decimal("1"), "MONTH": Decimal("1")},
            {
                "WEEK": Decimal("1"),
                "MONTH": Decimal("1"),
                "YEAR": Decimal("1"),
                "DAY": Decimal("1"),
            },
        )

        for invalid in invalid_inputs:
            with self.subTest(invalid=invalid):
                with self.assertRaisesMessage(ValidationError, "exactly WEEK, MONTH, and YEAR"):
                    persist_reference_price_facts(
                        snapshot_id=snapshot.id,
                        reference_values=invalid,
                    )

        self.assertFalse(ReferencePrice.objects.exists())
        self.assertFalse(PriceChangeFact.objects.exists())

    def test_replay_is_idempotent_and_reference_conflict_fails_closed(self) -> None:
        snapshot = create_snapshot()
        first = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values=reference_values(),
        )
        repeated = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values=reference_values(),
        )

        self.assertEqual([fact.id for fact in repeated], [fact.id for fact in first])
        self.assertEqual(ReferencePrice.objects.count(), 3)
        self.assertEqual(PriceChangeFact.objects.count(), 3)

        with self.assertRaisesMessage(ValidationError, "replay conflicts"):
            ReferencePrice.get_or_validate(
                snapshot_id=snapshot.id,
                period=ReferencePrice.Period.WEEK,
                value_status=ReferencePrice.ValueStatus.AVAILABLE,
                value=Decimal("10001"),
                unavailable_reason=None,
            )

        self.assertEqual(ReferencePrice.objects.count(), 3)
        self.assertEqual(PriceChangeFact.objects.count(), 3)

    def test_reference_value_state_zero_scale_and_overflow_fail_closed(self) -> None:
        invalid_variants: tuple[dict[str, object], ...] = (
            {
                "value_status": ReferencePrice.ValueStatus.AVAILABLE,
                "value": None,
                "unavailable_reason": None,
            },
            {
                "value_status": ReferencePrice.ValueStatus.AVAILABLE,
                "value": Decimal("1"),
                "unavailable_reason": ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING,
            },
            {
                "value_status": ReferencePrice.ValueStatus.UNAVAILABLE,
                "value": Decimal("1"),
                "unavailable_reason": ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING,
            },
            {
                "value_status": ReferencePrice.ValueStatus.UNAVAILABLE,
                "value": None,
                "unavailable_reason": None,
            },
            {
                "value_status": ReferencePrice.ValueStatus.AVAILABLE,
                "value": Decimal("0"),
                "unavailable_reason": None,
            },
            {
                "value_status": ReferencePrice.ValueStatus.AVAILABLE,
                "value": Decimal("2400.5"),
                "unavailable_reason": None,
            },
            {
                "value_status": ReferencePrice.ValueStatus.AVAILABLE,
                "value": Decimal("1000000000000"),
                "unavailable_reason": None,
            },
        )

        snapshot = create_snapshot()
        for overrides in invalid_variants:
            with self.subTest(overrides=overrides):
                candidate = ReferencePrice(
                    snapshot=snapshot,
                    period=ReferencePrice.Period.WEEK,
                    reference_date_status=(
                        ReferencePrice.ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
                    ),
                    source_reference_date=None,
                    **overrides,
                )
                with self.assertRaises(ValidationError):
                    candidate.save()

        self.assertFalse(ReferencePrice.objects.exists())

    def test_database_constraints_reject_reference_state_xor_and_dates(self) -> None:
        snapshot = create_snapshot()
        invalid_variants: tuple[dict[str, object], ...] = (
            {
                "value_status": "AVAILABLE",
                "value": None,
                "unavailable_reason": None,
            },
            {
                "value_status": "UNAVAILABLE",
                "value": Decimal("1"),
                "unavailable_reason": "SOURCE_VALUE_MISSING",
            },
            {
                "value_status": "UNAVAILABLE",
                "value": None,
                "unavailable_reason": None,
            },
            {
                "value_status": "AVAILABLE",
                "value": Decimal("0"),
                "unavailable_reason": None,
            },
            {
                "value_status": "AVAILABLE",
                "value": Decimal("1"),
                "unavailable_reason": None,
                "source_reference_date": date(2026, 8, 22),
            },
        )

        for overrides in invalid_variants:
            with self.subTest(overrides=overrides):
                fields: dict[str, object] = {
                    "snapshot": snapshot,
                    "period": "WEEK",
                    "reference_date_status": "SOURCE_REFERENCE_DATE_UNAVAILABLE",
                    "source_reference_date": None,
                }
                fields.update(overrides)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    ReferencePrice.objects.bulk_create([ReferencePrice(**fields)])

        overflow = ReferencePrice(
            snapshot=snapshot,
            period="WEEK",
            value_status="AVAILABLE",
            value=Decimal("1000000000000"),
            unavailable_reason=None,
            reference_date_status="SOURCE_REFERENCE_DATE_UNAVAILABLE",
        )
        with self.assertRaises(DatabaseError), transaction.atomic():
            ReferencePrice.objects.bulk_create([overflow])

        self.assertFalse(ReferencePrice.objects.exists())

    def test_calculation_contract_rejects_wrong_values_and_statuses(self) -> None:
        snapshot = create_snapshot(current_price=Decimal("8000"))
        week, month, _ = create_reference_triplet(
            snapshot,
            values=reference_values(MONTH=None),
        )
        invalid_facts: tuple[PriceChangeFact, ...] = (
            PriceChangeFact(
                reference_price=week,
                direction="LOWER",
                signed_difference=Decimal("-1999"),
                signed_percentage=Decimal("-20.0"),
            ),
            PriceChangeFact(
                reference_price=month,
                direction="EQUAL",
                signed_difference=Decimal("0"),
                signed_percentage=Decimal("0.0"),
            ),
        )

        for fact in invalid_facts:
            with self.subTest(reference=fact.reference_price.period):
                with self.assertRaisesMessage(ValidationError, "deterministic calculation"):
                    fact.save()

        self.assertFalse(PriceChangeFact.objects.exists())

    def test_database_trigger_rejects_wrong_cross_row_arithmetic(self) -> None:
        snapshot = create_snapshot(current_price=Decimal("8000"))
        week, _, _ = create_reference_triplet(snapshot)
        invalid_fact = PriceChangeFact(
            reference_price=week,
            direction="LOWER",
            signed_difference=Decimal("-1999"),
            signed_percentage=Decimal("-20.0"),
        )

        with self.assertRaises(DatabaseError), transaction.atomic():
            PriceChangeFact.objects.bulk_create([invalid_fact])

        self.assertFalse(PriceChangeFact.objects.exists())

    def test_percentage_precision_covers_the_full_current_reference_domain(self) -> None:
        snapshot = create_snapshot(current_price=Decimal("999999999999"))

        facts = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values={
                "WEEK": Decimal("1"),
                "MONTH": Decimal("1"),
                "YEAR": Decimal("1"),
            },
        )

        self.assertEqual(facts[0].signed_difference, Decimal("999999999998"))
        self.assertEqual(facts[0].signed_percentage, Decimal("99999999999800.0"))
        self.assertEqual(facts[0].direction, PriceChangeFact.Direction.HIGHER)

    def test_reference_and_change_facts_are_immutable_in_orm_and_sql(self) -> None:
        snapshot = create_snapshot()
        fact = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values=reference_values(),
        )[0]
        reference = fact.reference_price

        orm_mutations: tuple[Callable[[], object], ...] = (
            lambda: reference.save(),
            lambda: reference.delete(),
            lambda: fact.save(),
            lambda: fact.delete(),
        )
        for mutation in orm_mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesMessage(ValidationError, "immutable"):
                    mutation()

        sql_mutations: tuple[tuple[str, list[Any]], ...] = (
            (
                "UPDATE grocery_referenceprice SET value = %s WHERE id = %s",
                [Decimal("10001"), reference.id],
            ),
            (
                "DELETE FROM grocery_referenceprice WHERE id = %s",
                [reference.id],
            ),
            (
                "UPDATE grocery_pricechangefact SET signed_difference = %s WHERE id = %s",
                [Decimal("-1999"), fact.id],
            ),
            (
                "DELETE FROM grocery_pricechangefact WHERE id = %s",
                [fact.id],
            ),
        )
        for statement, parameters in sql_mutations:
            with self.subTest(statement=statement):
                with self.assertRaisesMessage(DatabaseError, "immutable"), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(statement, parameters)

        reference.refresh_from_db()
        fact.refresh_from_db()
        self.assertEqual(reference.value, Decimal("10000"))
        self.assertEqual(fact.signed_difference, Decimal("-7600"))

    def test_snapshot_and_reference_foreign_keys_are_protected(self) -> None:
        snapshot = create_snapshot()
        fact = persist_reference_price_facts(
            snapshot_id=snapshot.id,
            reference_values=reference_values(),
        )[0]

        self.assertIs(
            ReferencePrice._meta.get_field("snapshot").remote_field.on_delete,
            PROTECT,
        )
        self.assertIs(
            PriceChangeFact._meta.get_field("reference_price").remote_field.on_delete,
            PROTECT,
        )
        with self.assertRaisesMessage(ValidationError, "immutable"):
            snapshot.delete()
        with self.assertRaises(ProtectedError):
            ReferencePrice.objects.filter(pk=fact.reference_price_id).delete()

        self.assertIsInstance(fact.id, uuid.UUID)
