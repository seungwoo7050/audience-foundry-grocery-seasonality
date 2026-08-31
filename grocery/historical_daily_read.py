"""Regional range and market observation contexts from one active bundle."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_identity_models import HistoricalRetailSeriesKey
from grocery.historical_public_read import (
    ActiveHistoricalPublication,
    PublicParameterError,
    PublicReadIntegrityError,
)
from grocery.presentation import format_korean_date
from grocery.vnext_presentation import (
    build_market_summary,
    decimal_machine,
    format_provider_krw,
    range_meter,
)

HISTORICAL_PAGE_SIZE: Final = 30


def regions_context(
    active: ActiveHistoricalPublication,
    series: HistoricalRetailSeriesKey,
    *,
    selected_date: date | None,
) -> dict[str, object]:
    lower_date = active.revision.date_max - timedelta(days=30)
    regional = list(
        DailyRegionalRetailPrice.objects.filter(
            collection=active.regional_collection,
            series=series,
            survey_date__range=(lower_date, active.revision.date_max),
        )
        .select_related("region")
        .order_by("survey_date", "region__region_name", "region__region_code", "id")
    )
    market_keys = set(
        DailyMarketRetailPrice.objects.filter(
            collection=active.market_collection,
            series=series,
            survey_date__range=(lower_date, active.revision.date_max),
        ).values_list("region_id", "survey_date")
    )
    shared_dates = sorted(
        {row.survey_date for row in regional if (row.region_id, row.survey_date) in market_keys},
        reverse=True,
    )
    if not shared_dates:
        raise PublicReadIntegrityError("The published series has no shared daily survey date.")
    effective_date = selected_date or shared_dates[0]
    if effective_date not in shared_dates:
        raise PublicParameterError("The selected survey date is not available.")
    rows = [row for row in regional if row.survey_date == effective_date]
    if not rows:
        raise PublicReadIntegrityError("The selected published date has no regional facts.")
    for row in rows:
        _validate_range(row.provider_low, row.provider_mean, row.provider_high)
    scale_minimum = min(row.provider_low for row in rows)
    scale_maximum = max(row.provider_high for row in rows)
    return {
        "date_options": [_date_option(value, value == effective_date) for value in shared_dates],
        "selected_date": _date_value(effective_date),
        "regional_rows": [
            {
                "region_id": row.region_id,
                "region_label": row.region.region_name,
                "mean_machine": decimal_machine(row.provider_mean),
                "mean_label": format_provider_krw(row.provider_mean),
                "minimum_machine": decimal_machine(row.provider_low),
                "minimum_label": format_provider_krw(row.provider_low),
                "maximum_machine": decimal_machine(row.provider_high),
                "maximum_label": format_provider_krw(row.provider_high),
                "meter": range_meter(
                    minimum=row.provider_low,
                    mean=row.provider_mean,
                    maximum=row.provider_high,
                    scale_minimum=scale_minimum,
                    scale_maximum=scale_maximum,
                ),
                "market_available": (row.region_id, effective_date) in market_keys,
            }
            for row in rows
        ],
    }


def markets_context(
    active: ActiveHistoricalPublication,
    series: HistoricalRetailSeriesKey,
    *,
    region_id: uuid.UUID,
    selected_date: date | None,
    page: int,
) -> dict[str, object]:
    lower_date = active.revision.date_max - timedelta(days=30)
    regional_dates = set(
        DailyRegionalRetailPrice.objects.filter(
            collection=active.regional_collection,
            series=series,
            region_id=region_id,
            survey_date__range=(lower_date, active.revision.date_max),
        ).values_list("survey_date", flat=True)
    )
    market_facts = list(
        DailyMarketRetailPrice.objects.filter(
            collection=active.market_collection,
            series=series,
            region_id=region_id,
            survey_date__range=(lower_date, active.revision.date_max),
        )
        .select_related("region", "market")
        .order_by("survey_date", "market__market_name", "market__market_code", "market_id")
    )
    shared_dates = sorted(regional_dates & {row.survey_date for row in market_facts}, reverse=True)
    if not shared_dates:
        raise PublicParameterError("The selected region is not available for this series.")
    effective_date = selected_date or shared_dates[0]
    if effective_date not in shared_dates:
        raise PublicParameterError("The selected survey date is not available.")
    rows = [row for row in market_facts if row.survey_date == effective_date]
    for row in rows:
        if (
            not row.provider_price.is_finite()
            or row.provider_price <= 0
            or row.market.region_id != region_id
        ):
            raise PublicReadIntegrityError("Published market prices are malformed.")
    try:
        market_summary = build_market_summary([row.provider_price for row in rows])
    except ValueError as exc:
        raise PublicReadIntegrityError("Published market summary facts are malformed.") from exc
    total = len(rows)
    total_pages = max(1, (total + HISTORICAL_PAGE_SIZE - 1) // HISTORICAL_PAGE_SIZE)
    if page > total_pages:
        raise PublicParameterError("The selected page is not available.")
    start = (page - 1) * HISTORICAL_PAGE_SIZE
    selected_rows = rows[start : start + HISTORICAL_PAGE_SIZE]
    region = selected_rows[0].region if selected_rows else market_facts[0].region
    return {
        "selected_region": {"value": str(region_id), "label": region.region_name},
        "date_options": [_date_option(value, value == effective_date) for value in shared_dates],
        "selected_date": _date_value(effective_date),
        "market_rows": [
            {
                "market_name": row.market.market_name,
                "price_machine": decimal_machine(row.provider_price),
                "price_label": format_provider_krw(row.provider_price),
                "survey_date_iso": row.survey_date.isoformat(),
                "survey_date_label": format_korean_date(row.survey_date),
            }
            for row in selected_rows
        ],
        "market_summary": market_summary,
        "total_count": total,
        "page": page,
        "total_pages": total_pages,
        "selected_date_value": effective_date,
    }


def _validate_range(minimum: Decimal, mean: Decimal, maximum: Decimal) -> None:
    if any(not value.is_finite() or value <= 0 for value in (minimum, mean, maximum)):
        raise PublicReadIntegrityError("Published provider prices are malformed.")
    if not minimum <= mean <= maximum:
        raise PublicReadIntegrityError("Published provider ranges are malformed.")


def _date_option(value: date, selected: bool) -> dict[str, object]:
    return {"value": value.isoformat(), "label": format_korean_date(value), "selected": selected}


def _date_value(value: date) -> dict[str, str]:
    return {"iso": value.isoformat(), "label": format_korean_date(value)}
