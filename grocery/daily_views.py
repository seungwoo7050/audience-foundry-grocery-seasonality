"""SSR routes for regional ranges and market observations."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Final, cast
from urllib.parse import urlencode

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from grocery.forms import MarketsForm, RegionsForm
from grocery.historical_daily_read import markets_context, regions_context
from grocery.historical_public_read import (
    PublicParameterError,
    PublicReadIntegrityError,
    historical_publication_context,
    historical_series_context,
    historical_series_for_recent,
    load_active_historical_publication,
)
from grocery.historical_view_helpers import (
    active_recent_entry,
    fixed_parameter_error,
    historical_base_context,
    historical_server_error,
    market_recovery_context,
    section_navigation,
    validation_errors,
    with_publication_headers,
)
from grocery.observability import log_event

_LOGGER: Final = logging.getLogger("grocery.audit")


@require_safe
def regions(request: HttpRequest, series_id: uuid.UUID) -> HttpResponse:
    recent = None
    historical = None
    try:
        recent, entry = active_recent_entry(series_id)
        form = RegionsForm(request.GET if request.GET else None)
        form_invalid = form.is_bound and not form.is_valid()
        historical = load_active_historical_publication()
        context = historical_base_context(entry, series_id, current="regions")
        if historical is None:
            if form_invalid:
                return fixed_parameter_error(request, recent, None)
            context["regions_state"] = "unavailable"
            return with_publication_headers(
                render(request, "grocery/regions.html", context), recent, None
            )
        historical_series = historical_series_for_recent(historical, series_id)
        if historical_series is None:
            if form_invalid:
                return fixed_parameter_error(request, recent, historical)
            context["regions_state"] = "unavailable"
            return with_publication_headers(
                render(request, "grocery/regions.html", context), recent, historical
            )
        context.update(
            {
                "series": historical_series_context(historical_series),
                "section_nav": section_navigation(series_id, current="regions", historical=True),
                "historical_publication": historical_publication_context(historical),
                "regions_form_action": reverse("grocery:regions", kwargs={"series_id": series_id}),
            }
        )
        if form_invalid:
            context.update(regions_context(historical, historical_series, selected_date=None))
            context.update(
                {
                    "regions_state": "validation",
                    "date_error": "date" in form.errors,
                    "validation_errors": validation_errors(form, {"date": "regions-date"}),
                }
            )
            return with_publication_headers(
                render(request, "grocery/regions.html", context, status=400), recent, historical
            )
        selected_date = form.cleaned_data.get("date") if form.is_bound else None
        try:
            context.update(
                regions_context(historical, historical_series, selected_date=selected_date)
            )
        except PublicParameterError:
            context.update(regions_context(historical, historical_series, selected_date=None))
            context.update(
                {
                    "regions_state": "validation",
                    "date_error": True,
                    "validation_errors": [
                        {"message": "조사일을 확인하세요.", "target": "regions-date"}
                    ],
                }
            )
            return with_publication_headers(
                render(request, "grocery/regions.html", context, status=400), recent, historical
            )
        regional_rows = cast(list[dict[str, Any]], context["regional_rows"])
        selected_date_context = cast(dict[str, str], context["selected_date"])
        for row in regional_rows:
            row["markets_url"] = (
                _url(
                    reverse(
                        "grocery:markets",
                        kwargs={"series_id": series_id, "region_id": row["region_id"]},
                    ),
                    {"date": selected_date_context["iso"]},
                )
                if row.pop("market_available")
                else ""
            )
        context["regions_state"] = "stale" if historical.stale_message else "ready"
        return with_publication_headers(
            render(request, "grocery/regions.html", context), recent, historical
        )
    except Http404:
        raise
    except DatabaseError, ObjectDoesNotExist, PublicReadIntegrityError, ValidationError:
        log_event(_LOGGER, "ERROR", "public.regions.unavailable")
        return historical_server_error(
            request,
            template="grocery/regions.html",
            state_name="regions_state",
            recent=recent,
            historical=historical,
        )


@require_safe
def markets(request: HttpRequest, series_id: uuid.UUID, region_id: uuid.UUID) -> HttpResponse:
    recent = None
    historical = None
    try:
        recent, entry = active_recent_entry(series_id)
        form = MarketsForm(request.GET if request.GET else None)
        form_invalid = form.is_bound and not form.is_valid()
        historical = load_active_historical_publication()
        context = historical_base_context(entry, series_id, current="regions")
        context["regions_url"] = reverse("grocery:regions", kwargs={"series_id": series_id})
        if historical is None:
            if form_invalid:
                return fixed_parameter_error(request, recent, None)
            context["markets_state"] = "unavailable"
            return with_publication_headers(
                render(request, "grocery/markets.html", context), recent, None
            )
        historical_series = historical_series_for_recent(historical, series_id)
        if historical_series is None:
            if form_invalid:
                return fixed_parameter_error(request, recent, historical)
            context["markets_state"] = "unavailable"
            return with_publication_headers(
                render(request, "grocery/markets.html", context), recent, historical
            )
        context.update(
            {
                "series": historical_series_context(historical_series),
                "section_nav": section_navigation(series_id, current="regions", historical=True),
                "historical_publication": historical_publication_context(historical),
            }
        )
        if form_invalid:
            safe_context, recovery_region_id, _region_available = _market_recovery(
                historical, historical_series, region_id
            )
            context.update(safe_context)
            context["markets_form_action"] = _market_url(series_id, recovery_region_id)
            context.update(
                {
                    "markets_state": "validation",
                    "date_error": "date" in form.errors,
                    "validation_errors": validation_errors(form, {"date": "markets-date"}),
                }
            )
            return with_publication_headers(
                render(request, "grocery/markets.html", context, status=400), recent, historical
            )
        cleaned = form.cleaned_data if form.is_bound else {}
        selected_date = cleaned.get("date")
        page = int(cleaned.get("page", 1))
        try:
            context.update(
                markets_context(
                    historical,
                    historical_series,
                    region_id=region_id,
                    selected_date=selected_date,
                    page=page,
                )
            )
            context["markets_form_action"] = _market_url(series_id, region_id)
        except PublicParameterError:
            safe_context, recovery_region_id, region_available = _market_recovery(
                historical, historical_series, region_id
            )
            context.update(safe_context)
            context["markets_form_action"] = _market_url(series_id, recovery_region_id)
            date_options = cast(list[dict[str, object]], safe_context["date_options"])
            available_dates = {str(option["value"]) for option in date_options}
            date_error = (
                region_available
                and selected_date is not None
                and selected_date.isoformat() not in available_dates
            )
            context.update(
                {
                    "markets_state": "validation",
                    "date_error": date_error,
                    "validation_errors": [
                        {
                            "message": (
                                "조사일을 확인하세요."
                                if date_error
                                else "지역·조사일·페이지를 확인하세요."
                            ),
                            "target": "markets-date" if date_error else "",
                        }
                    ],
                }
            )
            return with_publication_headers(
                render(request, "grocery/markets.html", context, status=400), recent, historical
            )
        selected_date_value = cast(Any, context["selected_date_value"])
        current_page = cast(int, context["page"])
        total_pages = cast(int, context["total_pages"])
        context.update(
            {
                "markets_state": "stale" if historical.stale_message else "ready",
                "result_count_label": f"공개 시장 {context['total_count']}곳",
                "pagination": _pagination_context(
                    base_url=cast(str, context["markets_form_action"]),
                    page=current_page,
                    has_next=current_page < total_pages,
                    parameters={"date": selected_date_value.isoformat()},
                ),
            }
        )
        return with_publication_headers(
            render(request, "grocery/markets.html", context), recent, historical
        )
    except Http404:
        raise
    except DatabaseError, ObjectDoesNotExist, PublicReadIntegrityError, ValidationError:
        log_event(_LOGGER, "ERROR", "public.markets.unavailable")
        return historical_server_error(
            request,
            template="grocery/markets.html",
            state_name="markets_state",
            recent=recent,
            historical=historical,
        )


def _market_url(series_id: uuid.UUID, region_id: uuid.UUID) -> str:
    return reverse("grocery:markets", kwargs={"series_id": series_id, "region_id": region_id})


def _market_recovery(
    historical: Any, historical_series: Any, region_id: uuid.UUID
) -> tuple[dict[str, object], uuid.UUID, bool]:
    try:
        return (
            markets_context(
                historical,
                historical_series,
                region_id=region_id,
                selected_date=None,
                page=1,
            ),
            region_id,
            True,
        )
    except PublicParameterError:
        context, recovery_region_id = market_recovery_context(historical, historical_series)
        return context, recovery_region_id, False


def _url(base_url: str, parameters: dict[str, object]) -> str:
    return f"{base_url}?{urlencode(parameters)}" if parameters else base_url


def _pagination_context(
    *, base_url: str, page: int, has_next: bool, parameters: dict[str, object]
) -> dict[str, object]:
    def page_url(value: int) -> str:
        values = dict(parameters)
        if value != 1:
            values["page"] = value
        return _url(base_url, values)

    return {
        "has_multiple_pages": page > 1 or has_next,
        "previous_url": page_url(page - 1) if page > 1 else "",
        "next_url": page_url(page + 1) if has_next else "",
        "page_label": f"{page}페이지",
    }
