from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

import pytest

from grocery.presentation import (
    MICROBAR_PERCENT_CAP,
    MICROBAR_ZERO_X,
    comparison_microbar,
    direction_label,
    direction_symbol,
    format_absolute_krw,
    format_korean_date,
    format_korean_datetime,
    format_krw,
    format_signed_percentage,
    format_unit,
)


class DecimalFormatter(Protocol):
    def __call__(self, value: Decimal) -> str: ...


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("8000"), "8,000원"),
        (Decimal("0"), "0원"),
        (Decimal("-2000"), "-2,000원"),
    ],
)
def test_scale_zero_krw_format(value: Decimal, expected: str) -> None:
    assert format_krw(value) == expected


def test_difference_label_uses_absolute_amount_next_to_direction() -> None:
    assert format_absolute_krw(Decimal("-2000")) == "2,000원"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("-20.0"), "-20.0%"),
        (Decimal("25.0"), "+25.0%"),
        (Decimal("0.0"), "0.0%"),
    ],
)
def test_signed_percentage_format(value: Decimal, expected: str) -> None:
    assert format_signed_percentage(value) == expected


@pytest.mark.parametrize(
    ("value", "direction", "expected"),
    [
        (
            Decimal("-20.0"),
            "LOWER",
            {
                "x": Decimal("30.0"),
                "width": Decimal("20.0"),
                "capped": False,
                "cap_x": Decimal("30.0"),
                "is_equal": False,
            },
        ),
        (
            Decimal("25.0"),
            "HIGHER",
            {
                "x": Decimal("50.0"),
                "width": Decimal("25.0"),
                "capped": False,
                "cap_x": Decimal("75.0"),
                "is_equal": False,
            },
        ),
        (
            Decimal("0.0"),
            "EQUAL",
            {
                "x": Decimal("50.0"),
                "width": Decimal("0.0"),
                "capped": False,
                "cap_x": Decimal("50.0"),
                "is_equal": True,
            },
        ),
    ],
)
def test_comparison_microbar_uses_decimal_geometry_around_fixed_zero(
    value: Decimal,
    direction: str,
    expected: dict[str, Decimal | bool],
) -> None:
    assert MICROBAR_PERCENT_CAP == Decimal("50.0")
    assert MICROBAR_ZERO_X == Decimal("50.0")
    assert comparison_microbar(value, direction) == expected


@pytest.mark.parametrize(
    ("value", "direction", "expected_x", "expected_cap_x"),
    [
        (Decimal("-80.0"), "LOWER", Decimal("0.0"), Decimal("0.0")),
        (Decimal("75.0"), "HIGHER", Decimal("50.0"), Decimal("100.0")),
    ],
)
def test_comparison_microbar_caps_large_values_at_fifty_percent(
    value: Decimal,
    direction: str,
    expected_x: Decimal,
    expected_cap_x: Decimal,
) -> None:
    geometry = comparison_microbar(value, direction)

    assert geometry == {
        "x": expected_x,
        "width": Decimal("50.0"),
        "capped": True,
        "cap_x": expected_cap_x,
        "is_equal": False,
    }


@pytest.mark.parametrize(
    ("value", "direction"),
    [
        (Decimal("1.0"), "LOWER"),
        (Decimal("-1.0"), "HIGHER"),
        (Decimal("0.0"), "UNAVAILABLE"),
        (Decimal("NaN"), "EQUAL"),
        (Decimal("1.25"), "HIGHER"),
    ],
)
def test_comparison_microbar_rejects_malformed_numeric_or_direction_pairs(
    value: Decimal,
    direction: str,
) -> None:
    with pytest.raises(ValueError):
        comparison_microbar(value, direction)


@pytest.mark.parametrize(
    ("direction", "label", "symbol"),
    [
        ("LOWER", "낮음", "↓"),
        ("HIGHER", "높음", "↑"),
        ("EQUAL", "같음", "="),
        ("UNAVAILABLE", "비교 정보 없음", "○"),
    ],
)
def test_direction_has_text_and_non_color_symbol(
    direction: str,
    label: str,
    symbol: str,
) -> None:
    assert direction_label(direction) == label
    assert direction_symbol(direction) == symbol


def test_identity_and_audit_dates_keep_exact_source_values() -> None:
    assert format_unit("포기", "1") == "포기 × 1"
    assert format_korean_date(date(2026, 8, 29)) == "2026년 8월 29일"
    assert (
        format_korean_datetime(datetime(2026, 8, 30, 0, 30, tzinfo=UTC)) == "2026년 8월 30일 09:30"
    )


@pytest.mark.parametrize(
    ("formatter", "value"),
    [
        (format_krw, Decimal("1.5")),
        (format_krw, Decimal("NaN")),
        (format_signed_percentage, Decimal("1.25")),
    ],
)
def test_display_helpers_reject_unapproved_numeric_shapes(
    formatter: DecimalFormatter, value: Decimal
) -> None:
    with pytest.raises(ValueError):
        formatter(value)


def test_unknown_direction_and_incomplete_unit_fail_closed() -> None:
    with pytest.raises(ValueError):
        direction_label("UNLISTED")
    with pytest.raises(ValueError):
        direction_symbol("UNLISTED")
    with pytest.raises(ValueError):
        format_unit("", "1")
    with pytest.raises(ValueError):
        format_korean_datetime(datetime(2026, 8, 30, 9, 30))
