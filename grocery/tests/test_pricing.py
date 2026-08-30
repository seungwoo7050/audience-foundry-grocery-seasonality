from datetime import date, datetime
from decimal import Decimal
from typing import cast

import pytest

from grocery.pricing import (
    ComparisonPeriod,
    Direction,
    PriceSnapshot,
    PriceValidationError,
    ReferenceDateStatus,
    ReferencePrice,
    ValueStatus,
    compare_snapshot,
)


def available_reference(
    period: ComparisonPeriod,
    value: Decimal,
    *,
    reference_date: date | None = None,
) -> ReferencePrice:
    reference_date_status = (
        ReferenceDateStatus.PROVIDED
        if reference_date is not None
        else ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
    )
    return ReferencePrice(
        period=period,
        value_status=ValueStatus.AVAILABLE,
        value=value,
        unavailable_reason=None,
        reference_date_status=reference_date_status,
        reference_date=reference_date,
    )


def unavailable_reference(period: ComparisonPeriod) -> ReferencePrice:
    return ReferencePrice(
        period=period,
        value_status=ValueStatus.UNAVAILABLE,
        value=None,
        unavailable_reason="SOURCE_VALUE_UNAVAILABLE",
        reference_date_status=ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE,
        reference_date=None,
    )


def test_comparison_uses_signed_decimal_half_up_arithmetic() -> None:
    snapshot = PriceSnapshot(
        current_value=Decimal("8000"),
        references=(
            available_reference(ComparisonPeriod.YEAR, Decimal("8000")),
            available_reference(ComparisonPeriod.WEEK, Decimal("10000")),
            available_reference(ComparisonPeriod.MONTH, Decimal("6400")),
        ),
    )

    week, month, year = compare_snapshot(snapshot)

    assert [week.period, month.period, year.period] == list(ComparisonPeriod)
    assert week.difference == Decimal("-2000")
    assert week.percentage == Decimal("-20.0")
    assert week.direction is Direction.LOWER
    assert month.difference == Decimal("1600")
    assert month.percentage == Decimal("25.0")
    assert month.direction is Direction.HIGHER
    assert year.difference == Decimal("0")
    assert year.percentage == Decimal("0.0")
    assert year.direction is Direction.EQUAL
    assert all(type(comparison.percentage) is Decimal for comparison in (week, month, year))


def test_higher_example_is_positive_twenty_five_percent() -> None:
    snapshot = PriceSnapshot(
        current_value=Decimal("12500"),
        references=(
            available_reference(ComparisonPeriod.WEEK, Decimal("10000")),
            available_reference(ComparisonPeriod.MONTH, Decimal("12500")),
            available_reference(ComparisonPeriod.YEAR, Decimal("12500")),
        ),
    )

    comparison = compare_snapshot(snapshot)[0]

    assert comparison.difference == Decimal("2500")
    assert comparison.percentage == Decimal("25.0")
    assert comparison.direction is Direction.HIGHER


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (Decimal("81"), Decimal("1.3")),
        (Decimal("79"), Decimal("-1.3")),
    ],
)
def test_percentage_rounds_half_up_to_one_decimal_place(
    current: Decimal,
    expected: Decimal,
) -> None:
    snapshot = PriceSnapshot(
        current_value=current,
        references=(
            available_reference(ComparisonPeriod.WEEK, Decimal("80")),
            available_reference(ComparisonPeriod.MONTH, Decimal("80")),
            available_reference(ComparisonPeriod.YEAR, Decimal("80")),
        ),
    )

    assert compare_snapshot(snapshot)[0].percentage == expected


def test_unavailable_reference_produces_no_arithmetic_fact() -> None:
    snapshot = PriceSnapshot(
        current_value=Decimal("10000"),
        references=(
            unavailable_reference(ComparisonPeriod.WEEK),
            available_reference(ComparisonPeriod.MONTH, Decimal("9000")),
            available_reference(ComparisonPeriod.YEAR, Decimal("11000")),
        ),
    )

    comparison = compare_snapshot(snapshot)[0]

    assert comparison.reference_value is None
    assert comparison.difference is None
    assert comparison.percentage is None
    assert comparison.direction is Direction.UNAVAILABLE
    assert comparison.unavailable_reason == "SOURCE_VALUE_UNAVAILABLE"


def test_reference_date_is_preserved_without_derivation() -> None:
    provided = available_reference(
        ComparisonPeriod.WEEK,
        Decimal("10000"),
        reference_date=date(2026, 8, 20),
    )
    unavailable = available_reference(ComparisonPeriod.MONTH, Decimal("10000"))

    assert provided.reference_date_status is ReferenceDateStatus.PROVIDED
    assert provided.reference_date == date(2026, 8, 20)
    assert unavailable.reference_date_status is (
        ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
    )
    assert unavailable.reference_date is None


def test_value_and_reference_date_availability_are_independent() -> None:
    reference = ReferencePrice(
        period=ComparisonPeriod.WEEK,
        value_status=ValueStatus.UNAVAILABLE,
        value=None,
        unavailable_reason="SOURCE_VALUE_UNAVAILABLE",
        reference_date_status=ReferenceDateStatus.PROVIDED,
        reference_date=date(2026, 8, 20),
    )

    assert reference.value is None
    assert reference.reference_date == date(2026, 8, 20)


@pytest.mark.parametrize(
    "invalid_value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("1.0"),
        Decimal("1E+3"),
        Decimal("NaN"),
        Decimal("Infinity"),
        cast(Decimal, "10000"),
        cast(Decimal, 10000.0),
    ],
)
def test_snapshot_rejects_invalid_current_value(invalid_value: Decimal) -> None:
    with pytest.raises(PriceValidationError):
        PriceSnapshot(
            current_value=invalid_value,
            references=(
                available_reference(ComparisonPeriod.WEEK, Decimal("10000")),
                available_reference(ComparisonPeriod.MONTH, Decimal("10000")),
                available_reference(ComparisonPeriod.YEAR, Decimal("10000")),
            ),
        )


@pytest.mark.parametrize(
    "invalid_value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("1.0"),
        Decimal("NaN"),
        Decimal("-Infinity"),
        cast(Decimal, "10000"),
    ],
)
def test_available_reference_rejects_invalid_value(invalid_value: Decimal) -> None:
    with pytest.raises(PriceValidationError):
        available_reference(ComparisonPeriod.WEEK, invalid_value)


@pytest.mark.parametrize(
    ("value_status", "value", "reason"),
    [
        (ValueStatus.AVAILABLE, None, None),
        (ValueStatus.AVAILABLE, Decimal("10000"), "NOT_ALLOWED"),
        (ValueStatus.UNAVAILABLE, Decimal("10000"), "NOT_ALLOWED"),
        (ValueStatus.UNAVAILABLE, None, None),
        (ValueStatus.UNAVAILABLE, None, ""),
        (ValueStatus.UNAVAILABLE, None, "   "),
    ],
)
def test_reference_enforces_value_reason_xor(
    value_status: ValueStatus,
    value: Decimal | None,
    reason: str | None,
) -> None:
    with pytest.raises(PriceValidationError):
        ReferencePrice(
            period=ComparisonPeriod.WEEK,
            value_status=value_status,
            value=value,
            unavailable_reason=reason,
            reference_date_status=ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE,
            reference_date=None,
        )


@pytest.mark.parametrize(
    ("date_status", "reference_date"),
    [
        (ReferenceDateStatus.PROVIDED, None),
        (ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE, date(2026, 8, 20)),
        (ReferenceDateStatus.PROVIDED, cast(date, datetime(2026, 8, 20, 12, 0))),
    ],
)
def test_reference_enforces_reference_date_status_xor(
    date_status: ReferenceDateStatus,
    reference_date: date | None,
) -> None:
    with pytest.raises(PriceValidationError):
        ReferencePrice(
            period=ComparisonPeriod.WEEK,
            value_status=ValueStatus.AVAILABLE,
            value=Decimal("10000"),
            unavailable_reason=None,
            reference_date_status=date_status,
            reference_date=reference_date,
        )


def test_snapshot_rejects_missing_period() -> None:
    with pytest.raises(PriceValidationError):
        PriceSnapshot(
            current_value=Decimal("10000"),
            references=(
                available_reference(ComparisonPeriod.WEEK, Decimal("10000")),
                available_reference(ComparisonPeriod.MONTH, Decimal("10000")),
            ),
        )


def test_snapshot_rejects_duplicate_period() -> None:
    with pytest.raises(PriceValidationError):
        PriceSnapshot(
            current_value=Decimal("10000"),
            references=(
                available_reference(ComparisonPeriod.WEEK, Decimal("10000")),
                available_reference(ComparisonPeriod.WEEK, Decimal("10000")),
                available_reference(ComparisonPeriod.YEAR, Decimal("10000")),
            ),
        )


def test_runtime_enum_and_collection_types_are_enforced() -> None:
    with pytest.raises(PriceValidationError):
        ReferencePrice(
            period=cast(ComparisonPeriod, "WEEK"),
            value_status=ValueStatus.AVAILABLE,
            value=Decimal("10000"),
            unavailable_reason=None,
            reference_date_status=ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE,
            reference_date=None,
        )

    with pytest.raises(PriceValidationError):
        ReferencePrice(
            period=ComparisonPeriod.WEEK,
            value_status=cast(ValueStatus, "AVAILABLE"),
            value=Decimal("10000"),
            unavailable_reason=None,
            reference_date_status=ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE,
            reference_date=None,
        )

    with pytest.raises(PriceValidationError):
        ReferencePrice(
            period=ComparisonPeriod.WEEK,
            value_status=ValueStatus.AVAILABLE,
            value=Decimal("10000"),
            unavailable_reason=None,
            reference_date_status=cast(
                ReferenceDateStatus,
                "SOURCE_REFERENCE_DATE_UNAVAILABLE",
            ),
            reference_date=None,
        )

    with pytest.raises(PriceValidationError):
        PriceSnapshot(
            current_value=Decimal("10000"),
            references=cast(tuple[ReferencePrice, ...], []),
        )

    with pytest.raises(PriceValidationError):
        PriceSnapshot(
            current_value=Decimal("10000"),
            references=cast(tuple[ReferencePrice, ...], ("not-a-reference",) * 3),
        )
