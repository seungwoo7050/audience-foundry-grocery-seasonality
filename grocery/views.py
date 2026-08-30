"""Server-rendered public views over the active publication only."""

from __future__ import annotations

import logging
import uuid
from typing import Final
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from grocery.forms import QUERY_MAX_LENGTH, SearchForm
from grocery.observability import log_event
from grocery.public_read import (
    catalog_item,
    detail_context,
    load_active_publication,
    publication_entries,
)

_LOGGER: Final = logging.getLogger("grocery.audit")
_QA_STATES: Final = frozenset({"loading", "empty", "unavailable", "stale", "server_error"})
_QA_DETAIL_STATES: Final = frozenset({"loading", "unavailable", "stale", "server_error"})


def catalog(request: HttpRequest) -> HttpResponse:
    form = SearchForm(request.GET if request.GET else None)
    query = ""
    category = ""
    query_error = ""
    if form.is_bound and not form.is_valid():
        raw_query = request.GET.get("q", "")
        query = raw_query[: QUERY_MAX_LENGTH + 1]
        query_error = str(next(iter(form.errors.values()))[0])
        context = _catalog_base_context(query=query, category="")
        context.update(
            {
                "catalog_state": "empty",
                "query_error": query_error,
                "results": [],
                "status_message": "입력을 확인하고 다시 검색해 주세요.",
            }
        )
        return render(request, "grocery/catalog.html", context, status=400)
    if form.is_valid():
        query = form.cleaned_data["q"]
        category = form.cleaned_data["category"]

    try:
        active = load_active_publication()
        context = _catalog_base_context(query=query, category=category)
        if active is None:
            context.update(
                {
                    "catalog_state": "unavailable",
                    "results": [],
                    "status_message": "현재 공개할 수 있는 검토 완료 자료가 없습니다.",
                }
            )
            return render(request, "grocery/catalog.html", context)

        entries = list(publication_entries(active, query=query, category=category))
        results = [
            catalog_item(
                entry,
                active,
                url=reverse("grocery:detail", kwargs={"series_id": entry.snapshot.series_id}),
            )
            for entry in entries
        ]
        context.update(
            {
                "catalog_state": active.freshness_state if active.stale_message else "ready",
                "status_message": (
                    active.stale_message
                    if active.stale_message
                    else "검색 조건에 맞는 공개 항목이 없습니다."
                ),
                "results": results,
                "result_count_label": f"공개 항목 {len(results)}개",
            }
        )
        response = render(request, "grocery/catalog.html", context)
        return _publication_response(response, active.revision.typed_fact_set_sha256)
    except DatabaseError, ValidationError:
        log_event(_LOGGER, "ERROR", "public.catalog.unavailable")
        context = _catalog_base_context(query=query, category=category)
        context.update(
            {
                "catalog_state": "server_error",
                "results": [],
                "status_message": "잠시 후 다시 시도해 주세요.",
                "retry_url": reverse("grocery:catalog"),
            }
        )
        return render(request, "grocery/catalog.html", context, status=503)


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
        context = {
            "home_url": reverse("grocery:catalog"),
            "catalog_url": reverse("grocery:catalog"),
            "detail_state": active.freshness_state if active.stale_message else "ready",
            "status_message": active.stale_message,
            **detail_context(entry, active),
        }
        response = render(request, "grocery/detail.html", context)
        return _publication_response(response, active.revision.typed_fact_set_sha256)
    except Http404:
        raise
    except DatabaseError, ObjectDoesNotExist, ValidationError:
        log_event(_LOGGER, "ERROR", "public.detail.unavailable")
        context = {
            "home_url": reverse("grocery:catalog"),
            "catalog_url": reverse("grocery:catalog"),
            "detail_state": "server_error",
            "status_message": "잠시 후 다시 시도해 주세요.",
            "retry_url": request.path,
        }
        return render(request, "grocery/detail.html", context, status=503)


def qa_catalog_state(request: HttpRequest, state: str) -> HttpResponse:
    if not settings.QA_STATE_PREVIEWS_ENABLED or state not in _QA_STATES:
        raise Http404
    context = _catalog_base_context(query="아주긴한국어공식품목명", category="vegetable")
    context.update(
        {
            "qa_preview": True,
            "catalog_state": state,
            "status_message": "화면 상태와 긴 한국어 표시를 검수하는 로컬 전용 자료입니다.",
            "retry_url": request.path,
            "results": _qa_results() if state == "stale" else [],
            "result_count_label": "공개 항목 1개" if state == "stale" else "공개 항목 0개",
        }
    )
    return render(
        request, "grocery/catalog.html", context, status=503 if state == "server_error" else 200
    )


def qa_detail_state(request: HttpRequest, state: str) -> HttpResponse:
    if not settings.QA_STATE_PREVIEWS_ENABLED or state not in _QA_DETAIL_STATES:
        raise Http404
    context: dict[str, object] = {
        "qa_preview": True,
        "home_url": reverse("grocery:catalog"),
        "catalog_url": reverse("grocery:catalog"),
        "detail_state": state,
        "status_message": "화면 상태와 긴 한국어 표시를 검수하는 로컬 전용 자료입니다.",
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


def _catalog_base_context(*, query: str, category: str) -> dict[str, object]:
    catalog_url = reverse("grocery:catalog")
    return {
        "home_url": catalog_url,
        "form_action": catalog_url,
        "query": query,
        "selected_category": category,
        "categories": [
            {
                "label": label,
                "url": _category_url(catalog_url, query=query, category=value),
                "selected": category == value,
            }
            for value, label in (("", "전체"), ("vegetable", "채소류"), ("fruit", "과일류"))
        ],
    }


def _category_url(base_url: str, *, query: str, category: str) -> str:
    parameters = {}
    if query:
        parameters["q"] = query
    if category:
        parameters["category"] = category
    return f"{base_url}?{urlencode(parameters)}" if parameters else base_url


def _publication_response(response: HttpResponse, fact_set_sha256: str) -> HttpResponse:
    response.headers["X-Publication-Fact-Set"] = fact_set_sha256
    response.headers["Cache-Control"] = "public, max-age=60, stale-if-error=3600"
    return response


def _qa_results() -> list[dict[str, str]]:
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
            "freshness_label": "마지막 검토 자료 · 새 확인 필요",
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
                "reference_value_label": "125,456원",
                "difference_label": "2,000원",
                "percentage_label": "-1.6%",
                "direction_code": "LOWER",
                "direction_label": "낮음",
                "reference_date_available": False,
            },
            {
                "period_label": "1개월 전 제공값",
                "available": False,
                "unavailable_reason_label": "source 응답에 비교 제공값이 없습니다.",
                "reference_date_available": False,
            },
            {
                "period_label": "1년 전 제공값",
                "available": True,
                "reference_value_label": "123,456원",
                "difference_label": "0원",
                "percentage_label": "0.0%",
                "direction_code": "EQUAL",
                "direction_label": "같음",
                "reference_date_available": False,
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
            "checked_at_label": "2026년 8월 30일 12:00",
            "reviewed_at_iso": "2026-08-30T12:30:00+09:00",
            "reviewed_at_label": "2026년 8월 30일 12:30",
            "freshness_state": "stale",
            "freshness_label": "마지막 검토 자료 · 새 확인 필요",
        },
    }
