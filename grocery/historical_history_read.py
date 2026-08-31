"""Monthly history context from one active historical publication."""

from __future__ import annotations

import uuid
from collections import defaultdict

from grocery.historical_identity_models import HistoricalRetailSeriesKey, RetailRegionKey
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.historical_public_read import (
    ActiveHistoricalPublication,
    PublicParameterError,
    PublicReadIntegrityError,
)
from grocery.vnext_presentation import (
    MonthlyChartDatum,
    build_history_chart,
    build_history_summary,
    build_history_year_groups,
    format_year_month,
    monthly_display_point,
)


def history_context(
    active: ActiveHistoricalPublication,
    series: HistoricalRetailSeriesKey,
    *,
    selected_region_id: uuid.UUID | None,
    selected_range: int,
) -> dict[str, object]:
    facts = list(
        MonthlyRegionalRetailPrice.objects.filter(
            collection=active.monthly_collection,
            series=series,
        )
        .select_related("region")
        .order_by("region__region_name", "region__region_code", "year_month", "id")
    )
    by_region: dict[uuid.UUID, list[MonthlyRegionalRetailPrice]] = defaultdict(list)
    regions: dict[uuid.UUID, RetailRegionKey] = {}
    for fact in facts:
        _validate_monthly_fact(fact)
        by_region[fact.region_id].append(fact)
        regions[fact.region_id] = fact.region

    complete_36 = {
        region_id
        for region_id, rows in by_region.items()
        if _has_complete_months(rows, active.revision.month_max, 36)
    }
    if not complete_36:
        raise PublicReadIntegrityError("The published series has no complete 36-month region.")
    ordered_region_ids = sorted(
        complete_36,
        key=lambda region_id: (
            regions[region_id].region_name,
            regions[region_id].region_code,
            str(region_id),
        ),
    )
    region_options = [
        {
            "value": str(region_id),
            "label": regions[region_id].region_name,
            "selected": region_id == selected_region_id,
        }
        for region_id in ordered_region_ids
    ]
    base: dict[str, object] = {
        "region_options": region_options,
        "selected_region": None,
        "selected_range": {"value": str(selected_range), "label": f"{selected_range}개월"},
        "monthly_points": [],
        "history_chart": None,
    }
    if selected_region_id is None:
        if selected_range != 36:
            raise PublicParameterError("Select a region before changing the historical range.")
        base["range_options"] = _range_options(selected_range, allow_60=False)
        return base
    if selected_region_id not in complete_36:
        raise PublicParameterError("The selected region is not available for this series.")
    rows = by_region[selected_region_id]
    allow_60 = _has_complete_months(rows, active.revision.month_max, 60)
    if selected_range not in {12, 36} and not (selected_range == 60 and allow_60):
        raise PublicParameterError("The selected range is not available for this region.")
    selected_rows = _select_complete_months(rows, active.revision.month_max, selected_range)
    region = regions[selected_region_id]
    presentation_data = [
        MonthlyChartDatum(
            row.year_month,
            row.provider_mean,
            row.provider_low,
            row.provider_high,
        )
        for row in selected_rows
    ]
    try:
        monthly_points = [monthly_display_point(datum) for datum in presentation_data]
        history_summary = build_history_summary(presentation_data)
        history_year_groups = build_history_year_groups(presentation_data)
        history_chart = build_history_chart(presentation_data)
    except ValueError as exc:
        raise PublicReadIntegrityError(
            "Published monthly presentation facts are malformed."
        ) from exc
    base.update(
        {
            "selected_region": {"value": str(selected_region_id), "label": region.region_name},
            "range_options": _range_options(selected_range, allow_60=allow_60),
            "monthly_points": monthly_points,
            "history_summary": history_summary,
            "history_year_groups": history_year_groups,
            "history_chart": history_chart,
        }
    )
    return base


def _validate_monthly_fact(row: MonthlyRegionalRetailPrice) -> None:
    format_year_month(row.year_month)
    values = (row.provider_low, row.provider_mean, row.provider_high)
    if any(not value.is_finite() or value <= 0 for value in values):
        raise PublicReadIntegrityError("Published provider prices are malformed.")
    if not row.provider_low <= row.provider_mean <= row.provider_high:
        raise PublicReadIntegrityError("Published provider ranges are malformed.")


def _month_number(value: str) -> int:
    format_year_month(value)
    return int(value[:4]) * 12 + int(value[4:]) - 1


def _month_value(number: int) -> str:
    year, month = divmod(number, 12)
    return f"{year:04d}{month + 1:02d}"


def _expected_months(month_max: str, count: int) -> list[str]:
    maximum = _month_number(month_max)
    return [_month_value(value) for value in range(maximum - count + 1, maximum + 1)]


def _has_complete_months(
    rows: list[MonthlyRegionalRetailPrice], month_max: str, count: int
) -> bool:
    values = [row.year_month for row in rows]
    expected = set(_expected_months(month_max, count))
    return len(values) == len(set(values)) and expected <= set(values)


def _select_complete_months(
    rows: list[MonthlyRegionalRetailPrice], month_max: str, count: int
) -> list[MonthlyRegionalRetailPrice]:
    by_month = {row.year_month: row for row in rows}
    expected = _expected_months(month_max, count)
    if any(month not in by_month for month in expected):
        raise PublicReadIntegrityError("The selected published monthly range is incomplete.")
    return [by_month[month] for month in expected]


def _range_options(selected_range: int, *, allow_60: bool) -> list[dict[str, object]]:
    values = (12, 36, 60) if allow_60 else (12, 36)
    return [
        {"value": str(value), "label": f"{value}개월", "selected": value == selected_range}
        for value in values
    ]
