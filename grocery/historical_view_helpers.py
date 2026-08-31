"""Shared HTTP/context glue for the three historical SSR route families."""

from __future__ import annotations

import uuid
from typing import Any, cast

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from grocery.historical_daily_read import markets_context, regions_context
from grocery.historical_public_read import (
    ActiveHistoricalPublication,
    PublicReadIntegrityError,
)
from grocery.presentation import format_unit
from grocery.public_read import load_active_publication


def active_recent_entry(series_id: uuid.UUID) -> tuple[Any, Any]:
    active = load_active_publication()
    if active is None:
        raise Http404
    entry = (
        active.revision.entries.select_related("snapshot__series")
        .filter(snapshot__series_id=series_id)
        .first()
    )
    if entry is None:
        raise Http404
    return active, entry


def historical_base_context(entry: Any, series_id: uuid.UUID, *, current: str) -> dict[str, object]:
    series = entry.snapshot.series
    return {
        "home_url": reverse("grocery:catalog"),
        "catalog_url": reverse("grocery:catalog"),
        "selection_url": reverse("grocery:selection"),
        "series": {
            "category_label": series.category_name,
            "item_name": series.item_name,
            "variety_name": series.variety_name,
            "grade_name": series.grade_name,
            "unit_label": format_unit(series.raw_unit, series.raw_unit_size),
        },
        "section_nav": section_navigation(series_id, current=current, historical=False),
        "retry_url": entry_url(current, series_id),
    }


def market_recovery_context(
    historical: ActiveHistoricalPublication, historical_series: Any
) -> tuple[dict[str, object], uuid.UUID]:
    regional = regions_context(historical, historical_series, selected_date=None)
    rows = cast(list[dict[str, Any]], regional["regional_rows"])
    recovery = next((row for row in rows if row["market_available"]), None)
    if recovery is None:
        raise PublicReadIntegrityError("No published market recovery region is available.")
    region_id = cast(uuid.UUID, recovery["region_id"])
    return (
        markets_context(
            historical,
            historical_series,
            region_id=region_id,
            selected_date=None,
            page=1,
        ),
        region_id,
    )


def entry_url(current: str, series_id: uuid.UUID) -> str:
    route = {"history": "history", "regions": "regions"}.get(current, "detail")
    return reverse(f"grocery:{route}", kwargs={"series_id": series_id})


def section_navigation(
    series_id: uuid.UUID, *, current: str, historical: bool
) -> list[dict[str, object]]:
    links = [
        {
            "label": "최근 조사값",
            "url": reverse("grocery:detail", kwargs={"series_id": series_id}),
            "current": current == "detail",
            "available": True,
        }
    ]
    if historical:
        links.extend(
            [
                {
                    "label": "월별 기록",
                    "url": reverse("grocery:history", kwargs={"series_id": series_id}),
                    "current": current == "history",
                    "available": True,
                },
                {
                    "label": "지역별 조사값",
                    "url": reverse("grocery:regions", kwargs={"series_id": series_id}),
                    "current": current == "regions",
                    "available": True,
                },
            ]
        )
    return links


def validation_errors(form: Any, targets: dict[str, str]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for field_name, field_errors in form.errors.as_data().items():
        target = targets.get(field_name, "")
        for error in field_errors:
            message = str(error.message)
            if "%" in message and error.params:
                message %= error.params
            errors.append({"message": message, "target": target})
    return errors or [{"message": "요청 내용을 확인하세요.", "target": ""}]


def with_publication_headers(
    response: HttpResponse,
    recent: Any,
    historical: ActiveHistoricalPublication | None,
) -> HttpResponse:
    response.headers["X-Publication-Fact-Set"] = recent.revision.typed_fact_set_sha256
    if historical is not None:
        response.headers["X-Historical-Publication-Fact-Set"] = (
            historical.revision.typed_fact_set_sha256
        )
    return response


def fixed_parameter_error(
    request: HttpRequest,
    recent: Any,
    historical: ActiveHistoricalPublication | None,
) -> HttpResponse:
    """Return a non-reflecting 400 when route controls cannot be rendered safely."""

    response = render(
        request,
        "400.html",
        {"home_url": reverse("grocery:catalog")},
        status=400,
    )
    return with_publication_headers(response, recent, historical)


def historical_server_error(
    request: HttpRequest,
    *,
    template: str,
    state_name: str,
    recent: Any,
    historical: ActiveHistoricalPublication | None,
) -> HttpResponse:
    response = render(
        request,
        template,
        {
            "home_url": reverse("grocery:catalog"),
            "catalog_url": reverse("grocery:catalog"),
            "selection_url": reverse("grocery:selection"),
            state_name: "server_error",
            "retry_url": request.path,
        },
        status=503,
    )
    return (
        with_publication_headers(response, recent, historical) if recent is not None else response
    )
