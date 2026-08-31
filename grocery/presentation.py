"""Korean labels for the fixed recent-retail public contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from django.utils import timezone

from grocery.pricing import Direction

MICROBAR_PERCENT_CAP: Final = Decimal("50.0")
MICROBAR_ZERO_X: Final = Decimal("50.0")

_DIRECTION_LABELS: Final = MappingProxyType(
    {
        Direction.LOWER.value: "낮음",
        Direction.EQUAL.value: "같음",
        Direction.HIGHER.value: "높음",
        Direction.UNAVAILABLE.value: "비교 정보 없음",
    }
)
_DIRECTION_SYMBOLS: Final = MappingProxyType(
    {
        Direction.LOWER.value: "↓",
        Direction.EQUAL.value: "=",
        Direction.HIGHER.value: "↑",
        Direction.UNAVAILABLE.value: "○",
    }
)


def format_krw(value: Decimal) -> str:
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError("KRW display values must be finite scale-zero decimals")
    return f"{value:,.0f}원"


def format_absolute_krw(value: Decimal) -> str:
    return format_krw(abs(value))


def format_signed_percentage(value: Decimal) -> str:
    exponent = value.as_tuple().exponent
    if not value.is_finite() or not isinstance(exponent, int) or exponent < -1:
        raise ValueError("Percentage display values must have at most one decimal place")
    if value > 0:
        return f"+{value:.1f}%"
    return f"{value:.1f}%"


def comparison_microbar(value: Decimal, direction: str) -> dict[str, Decimal | bool]:
    """Return bounded, template-safe SVG geometry for one signed comparison."""

    # Reuse the public percentage contract before deriving any geometry.
    format_signed_percentage(value)
    expected_direction = (
        Direction.LOWER.value
        if value < 0
        else Direction.HIGHER.value
        if value > 0
        else Direction.EQUAL.value
    )
    if direction != expected_direction:
        raise ValueError("Comparison direction does not match the signed percentage")

    magnitude = min(abs(value), MICROBAR_PERCENT_CAP)
    capped = abs(value) > MICROBAR_PERCENT_CAP
    if direction == Direction.LOWER.value:
        x = MICROBAR_ZERO_X - magnitude
        cap_x = x
    elif direction == Direction.HIGHER.value:
        x = MICROBAR_ZERO_X
        cap_x = MICROBAR_ZERO_X + magnitude
    else:
        x = MICROBAR_ZERO_X
        cap_x = MICROBAR_ZERO_X

    return {
        "x": x,
        "width": magnitude,
        "capped": capped,
        "cap_x": cap_x,
        "is_equal": direction == Direction.EQUAL.value,
    }


def direction_label(direction: str) -> str:
    try:
        return _DIRECTION_LABELS[direction]
    except KeyError as error:
        raise ValueError("Unknown comparison direction") from error


def direction_symbol(direction: str) -> str:
    try:
        return _DIRECTION_SYMBOLS[direction]
    except KeyError as error:
        raise ValueError("Unknown comparison direction") from error


def format_unit(raw_unit: str, raw_unit_size: str) -> str:
    if not raw_unit or not raw_unit_size:
        raise ValueError("Unit identity cannot be empty")
    return f"{raw_unit} × {raw_unit_size}"


def format_korean_date(value: date) -> str:
    return f"{value.year}년 {value.month}월 {value.day}일"


def format_korean_datetime(value: datetime) -> str:
    if timezone.is_naive(value):
        raise ValueError("Public audit timestamps must be timezone-aware")
    local_value = timezone.localtime(value)
    return (
        f"{local_value.year}년 {local_value.month}월 {local_value.day}일 "
        f"{local_value.hour:02d}:{local_value.minute:02d}"
    )
