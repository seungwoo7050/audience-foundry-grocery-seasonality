"""Template-safe presentation primitives for the historical public ledger."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

_CHART_WIDTH: Final = Decimal("720")
_CHART_HEIGHT: Final = Decimal("280")
_PLOT_LEFT: Final = Decimal("64")
_PLOT_RIGHT: Final = Decimal("704")
_PLOT_TOP: Final = Decimal("16")
_PLOT_BOTTOM: Final = Decimal("240")


@dataclass(frozen=True, slots=True)
class MonthlyChartDatum:
    year_month: str
    provider_mean: Decimal
    provider_low: Decimal
    provider_high: Decimal


def decimal_machine(value: Decimal) -> str:
    """Return one finite, non-exponential decimal for a ``data`` attribute."""

    if not value.is_finite():
        raise ValueError("Public decimal values must be finite")
    return format(value, "f")


def format_provider_krw(value: Decimal) -> str:
    """Display an exact provider value while preserving up to two decimals."""

    if not value.is_finite() or value <= 0:
        raise ValueError("Provider KRW values must be finite and positive")
    normalized = format(value, ",.2f").rstrip("0").rstrip(".")
    return f"{normalized}원"


def format_year_month(value: str) -> tuple[str, str]:
    if len(value) != 6 or not value.isdecimal():
        raise ValueError("Historical month must use YYYYMM")
    year = int(value[:4])
    month = int(value[4:])
    if year < 1 or month < 1 or month > 12:
        raise ValueError("Historical month is out of range")
    return f"{year:04d}-{month:02d}", f"{year}년 {month}월"


def range_meter(
    *,
    minimum: Decimal,
    mean: Decimal,
    maximum: Decimal,
    scale_minimum: Decimal,
    scale_maximum: Decimal,
) -> dict[str, str]:
    """Map one provider range onto a shared neutral 0–100 scale."""

    values = (minimum, mean, maximum, scale_minimum, scale_maximum)
    if any(not value.is_finite() for value in values):
        raise ValueError("Range-meter values must be finite")
    if not (scale_minimum <= minimum <= mean <= maximum <= scale_maximum):
        raise ValueError("Range-meter values are not ordered")
    span = scale_maximum - scale_minimum
    positions: tuple[Decimal, ...]
    if span == 0:
        positions = (Decimal("50"),) * 3
    else:
        positions = tuple(
            (value - scale_minimum) * Decimal("100") / span
            for value in (minimum, mean, maximum)
        )
    return {
        "minimum_x": _svg_number(positions[0]),
        "mean_x": _svg_number(positions[1]),
        "maximum_x": _svg_number(positions[2]),
    }


def build_history_chart(data: Sequence[MonthlyChartDatum]) -> dict[str, object]:
    """Build a supplementary mean line and provider low/high band."""

    if len(data) < 2:
        raise ValueError("A monthly chart requires at least two points")
    for datum in data:
        values = (datum.provider_low, datum.provider_mean, datum.provider_high)
        if any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError("Monthly chart values must be finite and positive")
        if not datum.provider_low <= datum.provider_mean <= datum.provider_high:
            raise ValueError("Monthly chart ranges are not ordered")

    scale_minimum = min(datum.provider_low for datum in data)
    scale_maximum = max(datum.provider_high for datum in data)
    horizontal_span = _PLOT_RIGHT - _PLOT_LEFT
    vertical_span = _PLOT_BOTTOM - _PLOT_TOP

    def x_position(index: int) -> Decimal:
        return _PLOT_LEFT + horizontal_span * Decimal(index) / Decimal(len(data) - 1)

    def y_position(value: Decimal) -> Decimal:
        if scale_maximum == scale_minimum:
            return (_PLOT_TOP + _PLOT_BOTTOM) / Decimal("2")
        return _PLOT_BOTTOM - (
            (value - scale_minimum) * vertical_span / (scale_maximum - scale_minimum)
        )

    indexed_runs: list[list[tuple[int, MonthlyChartDatum]]] = []
    for index, datum in enumerate(data):
        if not indexed_runs or _month_number(datum.year_month) != (
            _month_number(indexed_runs[-1][-1][1].year_month) + 1
        ):
            indexed_runs.append([])
        indexed_runs[-1].append((index, datum))

    mean_segments: list[dict[str, str]] = []
    range_segments: list[dict[str, str]] = []
    for run in indexed_runs:
        if len(run) < 2:
            continue
        mean_points = [
            f"{_svg_number(x_position(index))},{_svg_number(y_position(datum.provider_mean))}"
            for index, datum in run
        ]
        upper_points = [
            f"{_svg_number(x_position(index))},{_svg_number(y_position(datum.provider_high))}"
            for index, datum in run
        ]
        lower_points = [
            f"{_svg_number(x_position(index))},{_svg_number(y_position(datum.provider_low))}"
            for index, datum in reversed(run)
        ]
        mean_segments.append({"points": " ".join(mean_points)})
        range_segments.append({"points": " ".join((*upper_points, *lower_points))})
    point_context = [
        {
            "x": _svg_number(x_position(index)),
            "y": _svg_number(y_position(datum.provider_mean)),
        }
        for index, datum in enumerate(data)
    ]

    tick_values = (
        scale_maximum,
        (scale_minimum + scale_maximum) / Decimal("2"),
        scale_minimum,
    )
    y_ticks = [
        {
            "x1": _svg_number(_PLOT_LEFT),
            "x2": _svg_number(_PLOT_RIGHT),
            "y": _svg_number(y_position(value)),
            "label_x": "4",
            "label_y": _svg_number(y_position(value) + Decimal("4")),
            "label": format_provider_krw(value),
        }
        for value in tick_values
    ]
    tick_indexes = sorted({0, len(data) // 2, len(data) - 1})
    x_ticks = [
        {
            "x": _svg_number(x_position(index)),
            "y": "268",
            "label": f"{data[index].year_month[:4]}.{data[index].year_month[4:]}",
        }
        for index in tick_indexes
    ]
    return {
        "view_box": f"0 0 {_svg_number(_CHART_WIDTH)} {_svg_number(_CHART_HEIGHT)}",
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
        "range_segments": range_segments,
        "mean_segments": mean_segments,
        "points": point_context,
    }


def _svg_number(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".") or "0"


def _month_number(value: str) -> int:
    iso, _label = format_year_month(value)
    return int(iso[:4]) * 12 + int(iso[5:]) - 1
