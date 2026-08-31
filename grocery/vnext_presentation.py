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


def monthly_display_point(datum: MonthlyChartDatum) -> dict[str, object]:
    """Format one validated monthly provider fact for an SSR ledger."""

    _validate_monthly_data((datum,))
    period_iso, period_label = format_year_month(datum.year_month)
    return {
        "available": True,
        "period_iso": period_iso,
        "period_label": period_label,
        "mean_machine": decimal_machine(datum.provider_mean),
        "mean_label": format_provider_krw(datum.provider_mean),
        "minimum_machine": decimal_machine(datum.provider_low),
        "minimum_label": format_provider_krw(datum.provider_low),
        "maximum_machine": decimal_machine(datum.provider_high),
        "maximum_label": format_provider_krw(datum.provider_high),
        "gap_after": False,
    }


def build_history_summary(data: Sequence[MonthlyChartDatum]) -> dict[str, object]:
    """Prepare latest and extrema-of-mean facts without template arithmetic.

    Input must be unique and chronological. Stable ``min``/``max`` selection means
    that the earliest month represents an exact tie.
    """

    _validate_monthly_data(data, require_chronological=True)
    if not data:
        raise ValueError("A monthly summary requires at least one point")
    latest = data[-1]
    lowest = min(data, key=lambda datum: datum.provider_mean)
    highest = max(data, key=lambda datum: datum.provider_mean)
    return {
        "latest": _mean_summary_item(latest),
        "lowest": _mean_summary_item(lowest),
        "highest": _mean_summary_item(highest),
    }


def build_history_year_groups(data: Sequence[MonthlyChartDatum]) -> list[dict[str, object]]:
    """Group chronological monthly facts by year, newest year first."""

    _validate_monthly_data(data, require_chronological=True)
    if not data:
        raise ValueError("History year groups require at least one point")
    points_by_year: dict[str, list[dict[str, object]]] = {}
    for datum in data:
        year = datum.year_month[:4]
        points_by_year.setdefault(year, []).append(monthly_display_point(datum))
    latest_year = data[-1].year_month[:4]
    return [
        {
            "year": year,
            "label": f"{year}년",
            "is_latest": year == latest_year,
            "open": year == latest_year,
            "points": points_by_year[year],
        }
        for year in reversed(points_by_year)
    ]


def build_market_summary(values: Sequence[Decimal]) -> dict[str, object]:
    """Summarize the full validated market result set before pagination."""

    if not values:
        raise ValueError("A market summary requires at least one observation")
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("Market observations must be finite and positive")
    minimum = min(values)
    maximum = max(values)
    return {
        "total_count": len(values),
        "total_count_label": f"{len(values)}곳",
        "minimum_machine": decimal_machine(minimum),
        "minimum_label": format_provider_krw(minimum),
        "maximum_machine": decimal_machine(maximum),
        "maximum_label": format_provider_krw(maximum),
    }


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
            (value - scale_minimum) * Decimal("100") / span for value in (minimum, mean, maximum)
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

    month_numbers = [_month_number(datum.year_month) for datum in data]
    if any(
        current <= previous
        for previous, current in zip(month_numbers, month_numbers[1:], strict=False)
    ):
        raise ValueError("Monthly chart points must be unique and chronological")
    first_month = month_numbers[0]
    last_month = month_numbers[-1]
    month_span = last_month - first_month
    if month_span < 1:
        raise ValueError("A monthly chart requires at least two chronological slots")

    scale_minimum = min(datum.provider_low for datum in data)
    scale_maximum = max(datum.provider_high for datum in data)
    horizontal_span = _PLOT_RIGHT - _PLOT_LEFT
    vertical_span = _PLOT_BOTTOM - _PLOT_TOP

    def x_position(month_number: int) -> Decimal:
        return _PLOT_LEFT + (
            horizontal_span * Decimal(month_number - first_month) / Decimal(month_span)
        )

    def y_position(value: Decimal) -> Decimal:
        if scale_maximum == scale_minimum:
            return (_PLOT_TOP + _PLOT_BOTTOM) / Decimal("2")
        return _PLOT_BOTTOM - (
            (value - scale_minimum) * vertical_span / (scale_maximum - scale_minimum)
        )

    indexed_runs: list[list[tuple[int, MonthlyChartDatum]]] = []
    for month_number, datum in zip(month_numbers, data, strict=True):
        if not indexed_runs or month_number != indexed_runs[-1][-1][0] + 1:
            indexed_runs.append([])
        indexed_runs[-1].append((month_number, datum))

    mean_segments: list[dict[str, str]] = []
    range_segments: list[dict[str, str]] = []
    for run in indexed_runs:
        if len(run) < 2:
            continue
        mean_points = [
            f"{_svg_number(x_position(month_number))},{_svg_number(y_position(datum.provider_mean))}"
            for month_number, datum in run
        ]
        upper_points = [
            f"{_svg_number(x_position(month_number))},{_svg_number(y_position(datum.provider_high))}"
            for month_number, datum in run
        ]
        lower_points = [
            f"{_svg_number(x_position(month_number))},{_svg_number(y_position(datum.provider_low))}"
            for month_number, datum in reversed(run)
        ]
        mean_segments.append({"points": " ".join(mean_points)})
        range_segments.append({"points": " ".join((*upper_points, *lower_points))})
    point_context = [
        {
            "x": _svg_number(x_position(month_number)),
            "y": _svg_number(y_position(datum.provider_mean)),
        }
        for month_number, datum in zip(month_numbers, data, strict=True)
    ]
    present_months = set(month_numbers)
    gap_markers = [
        {
            "x": _svg_number(x_position(month_number)),
            "label": _month_label(month_number),
        }
        for month_number in range(first_month, last_month + 1)
        if month_number not in present_months
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
    tick_months = sorted({first_month, first_month + month_span // 2, last_month})
    x_ticks = [
        {
            "x": _svg_number(x_position(month_number)),
            "y": "268",
            "label": _month_label(month_number),
        }
        for month_number in tick_months
    ]
    return {
        "view_box": f"0 0 {_svg_number(_CHART_WIDTH)} {_svg_number(_CHART_HEIGHT)}",
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
        "range_segments": range_segments,
        "mean_segments": mean_segments,
        "points": point_context,
        "gap_markers": gap_markers,
    }


def _validate_monthly_data(
    data: Sequence[MonthlyChartDatum], *, require_chronological: bool = False
) -> None:
    month_numbers: list[int] = []
    for datum in data:
        values = (datum.provider_low, datum.provider_mean, datum.provider_high)
        if any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError("Monthly provider values must be finite and positive")
        if not datum.provider_low <= datum.provider_mean <= datum.provider_high:
            raise ValueError("Monthly provider ranges are not ordered")
        month_numbers.append(_month_number(datum.year_month))
    if require_chronological and any(
        current <= previous
        for previous, current in zip(month_numbers, month_numbers[1:], strict=False)
    ):
        raise ValueError("Monthly points must be unique and chronological")


def _mean_summary_item(datum: MonthlyChartDatum) -> dict[str, str]:
    period_iso, period_label = format_year_month(datum.year_month)
    return {
        "period_iso": period_iso,
        "period_label": period_label,
        "mean_machine": decimal_machine(datum.provider_mean),
        "mean_label": format_provider_krw(datum.provider_mean),
    }


def _svg_number(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".") or "0"


def _month_number(value: str) -> int:
    iso, _label = format_year_month(value)
    return int(iso[:4]) * 12 + int(iso[5:]) - 1


def _month_label(value: int) -> str:
    year, zero_based_month = divmod(value, 12)
    return f"{year:04d}.{zero_based_month + 1:02d}"
