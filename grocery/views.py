"""Server-rendered public views over the active publication only."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from grocery.forms import (
    CATEGORY_CHOICES,
    DIRECTION_CHOICES,
    PERIOD_CHOICES,
    QUERY_MAX_LENGTH,
    SORT_CHOICES,
    CatalogForm,
)
from grocery.historical_public_read import (
    historical_series_for_recent,
    load_active_historical_publication,
)
from grocery.observability import log_event
from grocery.presentation import comparison_microbar
from grocery.public_read import (
    PUBLIC_PAGE_SIZE,
    catalog_item,
    detail_context,
    load_active_publication,
    publication_context,
    publication_entries,
)

_LOGGER: Final = logging.getLogger("grocery.audit")
_QA_STATES: Final = frozenset({"loading", "empty", "unavailable", "stale", "server_error"})
_QA_DETAIL_STATES: Final = frozenset({"loading", "unavailable", "stale", "server_error"})
_QA_ERROR_STATES: Final = {
    "error_400": ("400.html", 400),
    "error_403": ("403.html", 403),
    "error_404": ("404.html", 404),
    "error_500": ("500.html", 500),
}
_QUERY_ERROR_MESSAGES: Final = {
    "max_length": f"품목명은 {QUERY_MAX_LENGTH}자 이하로 입력하세요.",
    "unsafe": "품목명은 한 줄로 입력하세요.",
}
_QA_STATE_MESSAGES: Final = {
    "loading": "공개된 자료를 확인하는 동안 잠시 기다려 주세요.",
    "empty": "품목명을 바꾸거나 다른 부류를 선택하세요.",
    "unavailable": "검토를 마친 자료가 공개되면 이곳에 표시됩니다.",
    "stale": "최근 자료 확인이 필요합니다. 마지막으로 검토를 마친 조사값을 표시합니다.",
    "server_error": "잠시 후 다시 불러오세요.",
}


@require_safe
def catalog(request: HttpRequest) -> HttpResponse:
    form = CatalogForm(request.GET if request.GET else None)
    if form.is_bound and not form.is_valid():
        error_fields = set(form.errors)
        needs_generic_summary = not error_fields.issubset({"q", "category"})
        context = _catalog_base_context(category="", period="week", direction="all", sort="name")
        context.update(
            {
                "catalog_state": "validation",
                "query_error": _query_error(form),
                "category_error": (
                    "부류 선택을 확인해 주세요." if "category" in form.errors else ""
                ),
                "period_error": "period" in form.errors,
                "direction_error": "direction" in form.errors,
                "sort_error": "sort" in form.errors,
                "filters_open": True,
                "validation_errors": (
                    _validation_errors(
                        form,
                        {
                            "q": "catalog-query",
                            "period": "catalog-period",
                            "direction": "catalog-direction",
                            "sort": "catalog-sort",
                        },
                    )
                    if needs_generic_summary
                    else []
                ),
                "results": [],
            }
        )
        return render(request, "grocery/catalog.html", context, status=400)
    cleaned = form.cleaned_data if form.is_bound else {}
    query = str(cleaned.get("q", ""))
    category = str(cleaned.get("category", ""))
    period = str(cleaned.get("period", "week"))
    direction = str(cleaned.get("direction", "all"))
    sort = str(cleaned.get("sort", "name"))
    page = int(cleaned.get("page", 1))

    try:
        active = load_active_publication()
        context = _catalog_base_context(
            category=category,
            period=period,
            direction=direction,
            sort=sort,
        )
        if active is None:
            context.update(
                {
                    "catalog_state": "unavailable",
                    "results": [],
                    "status_message": "검토를 마친 자료가 공개되면 이곳에 표시됩니다.",
                }
            )
            return render(request, "grocery/catalog.html", context)

        filtered_entries = publication_entries(
            active,
            query=query,
            category=category,
            period=period,
            direction=direction,
            sort=sort,
        )
        start = (page - 1) * PUBLIC_PAGE_SIZE
        entries = (
            filtered_entries[:PUBLIC_PAGE_SIZE]
            if query
            else filtered_entries[start : start + PUBLIC_PAGE_SIZE]
        )
        has_next = not query and len(filtered_entries) > start + PUBLIC_PAGE_SIZE
        results = [
            catalog_item(
                entry,
                active,
                url=reverse("grocery:detail", kwargs={"series_id": entry.snapshot.series_id}),
            )
            for entry in entries
        ]
        for item, entry in zip(results, entries, strict=True):
            item["selection_url"] = _selection_url((entry.snapshot.series_id,))
        context.update(
            {
                "catalog_state": active.freshness_state if active.stale_message else "ready",
                "status_message": (
                    active.stale_message
                    if active.stale_message
                    else "품목명을 바꾸거나 다른 부류를 선택하세요."
                ),
                "results": results,
                "result_count_label": f"현재 페이지 {len(results)}개",
                "publication": publication_context(active),
                "pagination": _pagination_context(
                    base_url=reverse("grocery:catalog"),
                    page=page,
                    has_next=has_next,
                    parameters=_catalog_parameters(
                        category=category,
                        period=period,
                        direction=direction,
                        sort=sort,
                    ),
                )
                if not query
                else None,
                "selection_url": reverse("grocery:selection"),
            }
        )
        response = render(request, "grocery/catalog.html", context)
        return _publication_response(response, active.revision.typed_fact_set_sha256)
    except (DatabaseError, ValidationError):  # fmt: skip
        log_event(_LOGGER, "ERROR", "public.catalog.unavailable")
        context = _catalog_base_context(category=category)
        context.update(
            {
                "catalog_state": "server_error",
                "results": [],
                "status_message": "잠시 후 다시 불러오세요.",
                "retry_url": reverse("grocery:catalog"),
            }
        )
        return render(request, "grocery/catalog.html", context, status=503)


@require_safe
def detail(request: HttpRequest, series_id: uuid.UUID) -> HttpResponse:
    try:
        active = load_active_publication()
        if active is None:
            raise Http404
        entry = (
            active.revision.entries.select_related(
                "snapshot__series",
            )
            .prefetch_related("snapshot__reference_prices__change_fact")
            .filter(snapshot__series_id=series_id)
            .first()
        )
        if entry is None:
            raise Http404
        historical = None
        historical_series = None
        try:
            historical = load_active_historical_publication()
            historical_series = (
                historical_series_for_recent(historical, series_id)
                if historical is not None
                else None
            )
        except DatabaseError, ObjectDoesNotExist, ValidationError:
            log_event(_LOGGER, "ERROR", "public.detail.history_hidden")
            historical = None
        links = _historical_links(series_id) if historical_series is not None else {}
        context = {
            "home_url": reverse("grocery:catalog"),
            "catalog_url": reverse("grocery:catalog"),
            "selection_url": reverse("grocery:selection"),
            "selection_add_url": _selection_url((series_id,)),
            "detail_state": active.freshness_state if active.stale_message else "ready",
            "status_message": active.stale_message,
            "publication": publication_context(active),
            "historical_links": links,
            "section_nav": _section_navigation(series_id, current="detail", historical=bool(links)),
            **detail_context(entry, active),
        }
        response = render(request, "grocery/detail.html", context)
        response = _publication_response(response, active.revision.typed_fact_set_sha256)
        if historical is not None and historical_series is not None:
            response = _historical_publication_response(
                response, historical.revision.typed_fact_set_sha256
            )
        return response
    except Http404:
        raise
    except (DatabaseError, ObjectDoesNotExist, ValidationError):  # fmt: skip
        log_event(_LOGGER, "ERROR", "public.detail.unavailable")
        context = {
            "home_url": reverse("grocery:catalog"),
            "catalog_url": reverse("grocery:catalog"),
            "selection_url": reverse("grocery:selection"),
            "detail_state": "server_error",
            "status_message": "잠시 후 다시 불러오세요.",
            "retry_url": request.path,
        }
        return render(request, "grocery/detail.html", context, status=503)


@require_safe
def qa_catalog_state(request: HttpRequest, state: str) -> HttpResponse:
    if not settings.QA_STATE_PREVIEWS_ENABLED:
        raise Http404
    error_preview = _QA_ERROR_STATES.get(state)
    if error_preview is not None:
        template_name, status = error_preview
        return render(
            request,
            template_name,
            {"home_url": reverse("grocery:catalog"), "qa_preview": True},
            status=status,
        )
    if state not in _QA_STATES:
        raise Http404
    context = _catalog_base_context(category="vegetable")
    context.update(
        {
            "qa_preview": True,
            "catalog_state": state,
            "status_message": _QA_STATE_MESSAGES[state],
            "retry_url": request.path,
            "results": _qa_results() if state == "stale" else [],
            "result_count_label": "공개 항목 1개" if state == "stale" else "공개 항목 0개",
            "publication": _qa_publication_context() if state == "stale" else {},
        }
    )
    return render(
        request, "grocery/catalog.html", context, status=503 if state == "server_error" else 200
    )


@require_safe
def qa_detail_state(request: HttpRequest, state: str) -> HttpResponse:
    if not settings.QA_STATE_PREVIEWS_ENABLED or state not in _QA_DETAIL_STATES:
        raise Http404
    context: dict[str, object] = {
        "qa_preview": True,
        "home_url": reverse("grocery:catalog"),
        "catalog_url": reverse("grocery:catalog"),
        "detail_state": state,
        "status_message": _QA_STATE_MESSAGES[state],
        "retry_url": request.path,
        "series": {
            "category_label": "채소류",
            "item_name": "아주긴한국어공식품목명이작은화면에서도잘려서는안되는품목",
            "variety_name": "아주긴한국어공식품종표시와세부구분",
            "grade_name": "공식등급표시",
            "unit_label": "아주긴원문판매단위표시 포기 × 100",
        },
    }
    if state == "stale":
        context.update(_qa_detail_ready_context())
    return render(
        request, "grocery/detail.html", context, status=503 if state == "server_error" else 200
    )


def _catalog_base_context(
    *, category: str, period: str = "week", direction: str = "all", sort: str = "name"
) -> dict[str, object]:
    catalog_url = reverse("grocery:catalog")
    parameters = _catalog_parameters(category="", period=period, direction=direction, sort=sort)
    period_labels = dict(PERIOD_CHOICES)
    sort_labels = dict(SORT_CHOICES)
    return {
        "home_url": catalog_url,
        "form_action": catalog_url,
        "selection_url": reverse("grocery:selection"),
        "selected_category": category,
        "selected_period": period,
        "selected_direction": direction,
        "selected_sort": sort,
        "selected_period_label": period_labels[period],
        "selected_period_heading": period_labels[period],
        "selected_period_missing_label": f"{period_labels[period]}값 없음",
        "selected_sort_label": sort_labels[sort],
        "filters_open": period != "week" or direction != "all" or sort != "name",
        "period_options": _choice_options(PERIOD_CHOICES, period),
        "direction_options": _choice_options(DIRECTION_CHOICES, direction),
        "sort_options": _choice_options(SORT_CHOICES, sort),
        "categories": [
            {
                "label": label,
                "url": _url(
                    catalog_url,
                    {**parameters, **({"category": value} if value else {})},
                ),
                "selected": category == value,
            }
            for value, label in CATEGORY_CHOICES
        ],
    }


def _choice_options(choices: tuple[tuple[str, str], ...], selected: str) -> list[dict[str, object]]:
    return [
        {"value": value, "label": label, "selected": value == selected} for value, label in choices
    ]


def _catalog_parameters(*, category: str, period: str, direction: str, sort: str) -> dict[str, str]:
    parameters = {}
    if category:
        parameters["category"] = category
    if period != "week":
        parameters["period"] = period
    if direction != "all":
        parameters["direction"] = direction
    if sort != "name":
        parameters["sort"] = sort
    return parameters


def _url(base_url: str, parameters: dict[str, object]) -> str:
    return f"{base_url}?{urlencode(parameters, doseq=True)}" if parameters else base_url


def _selection_url(series_ids: tuple[uuid.UUID, ...]) -> str:
    base = reverse("grocery:selection")
    return _url(base, {"series": [str(series_id) for series_id in series_ids]})


def _pagination_context(
    *,
    base_url: str,
    page: int,
    has_next: bool,
    parameters: Mapping[str, object],
) -> dict[str, object]:
    def page_url(value: int) -> str:
        values: dict[str, object] = dict(parameters)
        if value != 1:
            values["page"] = value
        return _url(base_url, values)

    return {
        "has_multiple_pages": page > 1 or has_next,
        "previous_url": page_url(page - 1) if page > 1 else "",
        "next_url": page_url(page + 1) if has_next else "",
        "page_label": f"{page}페이지",
    }


def _historical_links(series_id: uuid.UUID) -> dict[str, str]:
    return {
        "history_url": reverse("grocery:history", kwargs={"series_id": series_id}),
        "regions_url": reverse("grocery:regions", kwargs={"series_id": series_id}),
    }


def _section_navigation(
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


def _validation_errors(form: Any, targets: dict[str, str]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for field_name, field_errors in form.errors.as_data().items():
        target = targets.get(field_name, "")
        for error in field_errors:
            message = str(error.message)
            if "%" in message and error.params:
                message %= error.params
            errors.append({"message": message, "target": target})
    return errors or [{"message": "요청 내용을 확인하세요.", "target": ""}]


def _publication_response(response: HttpResponse, fact_set_sha256: str) -> HttpResponse:
    response.headers["X-Publication-Fact-Set"] = fact_set_sha256
    return response


def _historical_publication_response(response: HttpResponse, fact_set_sha256: str) -> HttpResponse:
    response.headers["X-Historical-Publication-Fact-Set"] = fact_set_sha256
    return response


def _query_error(form: CatalogForm) -> str:
    errors = form.errors.as_data().get("q", [])
    if not errors:
        return ""
    return _QUERY_ERROR_MESSAGES.get(errors[0].code or "", "품목명을 확인하세요.")


def _qa_results() -> list[dict[str, object]]:
    return [
        {
            "url": reverse("grocery:qa_detail_state", kwargs={"state": "stale"}),
            "category_label": "채소류",
            "item_name": "아주긴한국어공식품목명이작은화면에서도잘려서는안되는품목",
            "variety_name": "아주긴한국어공식품종표시와세부구분",
            "grade_name": "공식등급표시",
            "unit_label": "아주긴원문판매단위표시 포기 × 100",
            "current_price_label": "123,456원",
            "source_date_iso": "2026-08-29",
            "source_date_label": "2026년 8월 29일",
            "freshness_state": "stale",
            "freshness_label": "마지막 공개 자료 · 최근 확인 필요",
            "week_comparison": {
                "period_label": "1주 전 제공값",
                "available": True,
                "reference_price_display": "125,456원",
                "difference_display": "2,000원",
                "percentage_display": "-1.6%",
                "direction_code": "LOWER",
                "direction_label": "낮음",
                "reference_date_display": "",
                "reference_date_unavailable": True,
                "unavailable_reason": "",
                "microbar": comparison_microbar(Decimal("-1.6"), "LOWER"),
            },
        }
    ]


def _qa_detail_ready_context() -> dict[str, object]:
    return {
        "series": {
            "category_label": "채소류",
            "item_name": "아주긴한국어공식품목명이작은화면에서도잘려서는안되는품목",
            "variety_name": "아주긴한국어공식품종표시와세부구분",
            "grade_name": "공식등급표시",
            "unit_label": "아주긴원문판매단위표시 포기 × 100",
            "current_price_machine": "123456",
            "current_price_label": "123,456원",
        },
        "comparisons": [
            {
                "period_label": "1주 전 제공값",
                "available": True,
                "reference_price_display": "125,456원",
                "difference_display": "2,000원",
                "percentage_display": "-1.6%",
                "direction_code": "LOWER",
                "direction_label": "낮음",
                "reference_date_display": "2026년 8월 22일",
                "reference_date_unavailable": False,
                "unavailable_reason": "",
                "microbar": comparison_microbar(Decimal("-1.6"), "LOWER"),
            },
            {
                "period_label": "1개월 전 제공값",
                "available": False,
                "reference_price_display": "",
                "difference_display": "",
                "percentage_display": "",
                "direction_code": "UNAVAILABLE",
                "direction_label": "비교 정보 없음",
                "reference_date_display": "",
                "reference_date_unavailable": True,
                "unavailable_reason": "KAMIS가 이 기간의 비교값을 제공하지 않았습니다.",
                "microbar": None,
            },
            {
                "period_label": "1년 전 제공값",
                "available": True,
                "reference_price_display": "123,456원",
                "difference_display": "0원",
                "percentage_display": "0.0%",
                "direction_code": "EQUAL",
                "direction_label": "같음",
                "reference_date_display": "",
                "reference_date_unavailable": True,
                "unavailable_reason": "",
                "microbar": comparison_microbar(Decimal("0.0"), "EQUAL"),
            },
        ],
        "provenance": {
            "source_name": (
                "한국농수산식품유통공사 KAMIS 최근일자 도·소매가격정보 긴 출처 표시 검수"
            ),
            "source_url": "https://www.data.go.kr/data/15156063/openapi.do",
            "dataset_id": "15156063",
            "source_date_iso": "2026-08-29",
            "source_date_label": "2026년 8월 29일",
            "coverage_label": "KAMIS 소매 조사 22개 도시 지역 전체 집계",
            "checked_at_iso": "2026-08-30T12:00:00+09:00",
            "checked_at_display": "2026년 8월 30일 12:00",
            "reviewed_at_iso": "2026-08-30T12:30:00+09:00",
            "reviewed_at_label": "2026년 8월 30일 12:30",
            "freshness_state": "stale",
            "freshness_label": "마지막 공개 자료 · 최근 확인 필요",
        },
        "publication": _qa_publication_context(),
    }


def _qa_publication_context() -> dict[str, str]:
    return {
        "checked_at_iso": "2026-08-30T12:00:00+09:00",
        "checked_at_display": "2026년 8월 30일 12:00",
        "freshness_state": "stale",
        "freshness_label": "마지막 공개 자료 · 최근 확인 필요",
    }
