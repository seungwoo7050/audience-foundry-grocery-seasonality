from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.template.loader import render_to_string

FORBIDDEN_PUBLIC_PHRASES = (
    "제철",
    "평년",
    "저렴",
    "비싸",
    "가성비",
    "사세요",
    "추천",
    "최저가",
    "시장 최저",
    "전국 평균",
    "마트 가격",
    "실시간",
    "예측",
    "절약액",
    "품절",
    "판매 종료",
    "비제철",
)


def render(template_name: str, context: Mapping[str, Any] | None = None) -> str:
    return render_to_string(template_name, context or {})


def catalog_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "home_url": "/",
        "form_action": "/",
        "catalog_state": "ready",
        "query": "배추",
        "selected_category": "vegetable",
        "categories": [
            {"label": "전체", "url": "/", "selected": False},
            {"label": "채소류", "url": "/?category=vegetable", "selected": True},
            {"label": "과일류", "url": "/?category=fruit", "selected": False},
        ],
        "result_count_label": "공개 항목 1개",
        "results": [
            {
                "url": "/series/200-212-00-04/",
                "category_label": "채소류",
                "item_name": "아주긴한국어공식품목명이줄바꿈되어야하는배추",
                "variety_name": "아주긴한국어공식품종명이잘리지않아야하는품종",
                "grade_name": "상품",
                "unit_label": "아주긴원문판매단위표시 포기 × 1",
                "current_price_label": "8,000원",
                "source_date_iso": "2026-08-29",
                "source_date_label": "2026년 8월 29일",
                "freshness_state": "current",
                "freshness_label": "공개 조사일 확인됨",
            }
        ],
    }
    context.update(overrides)
    return context


def detail_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "home_url": "/",
        "catalog_url": "/?category=vegetable",
        "detail_state": "ready",
        "series": {
            "category_label": "채소류",
            "item_name": "배추",
            "variety_name": "봄",
            "grade_name": "상품",
            "unit_label": "포기 × 1",
            "current_price_machine": "8000",
            "current_price_label": "8,000원",
        },
        "comparisons": [
            {
                "period_label": "1주 전 제공값",
                "available": True,
                "reference_value_label": "10,000원",
                "difference_label": "2,000원",
                "percentage_label": "-20.0%",
                "direction_code": "LOWER",
                "direction_label": "낮음",
                "reference_date_available": False,
            },
            {
                "period_label": "1개월 전 제공값",
                "available": True,
                "reference_value_label": "10,000원",
                "difference_label": "2,000원",
                "percentage_label": "-20.0%",
                "direction_code": "LOWER",
                "direction_label": "낮음",
                "reference_date_available": False,
            },
            {
                "period_label": "1년 전 제공값",
                "available": False,
                "unavailable_reason_label": "source 응답에 비교값이 없습니다.",
                "reference_date_available": False,
            },
        ],
        "provenance": {
            "source_name": "한국농수산식품유통공사 KAMIS 최근일자 도·소매가격정보",
            "source_url": "https://www.data.go.kr/data/15156063/openapi.do",
            "dataset_id": "15156063",
            "source_date_iso": "2026-08-29",
            "source_date_label": "2026년 8월 29일",
            "coverage_label": "KAMIS 소매 조사 22개 도시 지역 전체 집계",
            "checked_at_label": "2026년 8월 30일 09:00",
            "checked_at_iso": "2026-08-30T09:00:00+09:00",
            "reviewed_at_label": "2026년 8월 30일 09:30",
            "reviewed_at_iso": "2026-08-30T09:30:00+09:00",
            "freshness_state": "current",
            "freshness_label": "공개 조사일 확인됨",
        },
    }
    context.update(overrides)
    return context


def test_catalog_renders_semantic_search_and_long_identity() -> None:
    html = render("grocery/catalog.html", catalog_context())

    assert '<html lang="ko">' in html
    assert 'href="#main-content"' in html
    assert '<main id="main-content"' in html
    assert 'role="search"' in html
    assert '<label for="catalog-query">공식 품목명</label>' in html
    assert 'name="q"' in html
    assert 'aria-current="page"' in html
    assert '<span class="chip__selected-mark" aria-hidden="true">✓</span>' in html
    assert "아주긴한국어공식품목명이줄바꿈되어야하는배추" in html
    assert "아주긴원문판매단위표시 포기 × 1" in html
    assert "공개 조사일 확인됨" in html


def test_catalog_validation_error_is_associated_with_input() -> None:
    html = render(
        "grocery/catalog.html",
        catalog_context(query="잘못된 입력", query_error="검색어는 80자 이하여야 합니다."),
    )

    assert 'id="search-error"' in html
    assert 'role="alert"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="catalog-query-hint search-error"' in html
    assert 'href="#catalog-query">검색어 입력으로 이동</a>' in html
    assert "검색어는 80자 이하여야 합니다." in html


@pytest.mark.parametrize(
    ("state", "expected_copy", "expected_role"),
    [
        ("loading", "자료를 불러오는 중", 'role="status"'),
        ("empty", "조건에 맞는 항목 없음", 'role="status"'),
        ("unavailable", "공개 조사값 없음", 'role="status"'),
        ("stale", "마지막 검토 자료 표시 중", 'role="status"'),
        ("server_error", "자료를 표시하지 못함", 'role="alert"'),
    ],
)
def test_catalog_state_has_text_and_semantic_role(
    state: str,
    expected_copy: str,
    expected_role: str,
) -> None:
    context = catalog_context(catalog_state=state, retry_url="/")
    if state != "stale":
        context["results"] = []
    html = render(
        "grocery/catalog.html",
        context,
    )

    assert expected_copy in html
    assert expected_role in html


def test_detail_renders_exact_identity_comparisons_and_provenance() -> None:
    html = render("grocery/detail.html", detail_context())

    assert "비교 대상의 정확한 조건" in html
    assert "품종" in html and "봄" in html
    assert "등급" in html and "상품" in html
    assert "판매 단위" in html and "포기 × 1" in html
    assert "조사범위" in html and "22개 도시 지역 전체 집계" in html
    assert "2,000원 낮음" in html
    assert "(-20.0%)" in html
    assert "비교 정보 없음" in html
    assert "source가 비교 기준일을 별도로 제공하지 않음" in html
    assert "데이터셋 15156063" in html
    assert "공개 검토일" in html
    assert '<time datetime="2026-08-29">' in html
    assert '<time datetime="2026-08-30T09:00:00+09:00">' in html


def test_detail_direction_is_not_conveyed_by_symbol_or_color_alone() -> None:
    html = render("grocery/detail.html", detail_context())

    assert '<span class="direction__symbol" aria-hidden="true">' in html
    assert "낮음" in html
    assert "비교 정보 없음" in html


@pytest.mark.parametrize(
    ("template_name", "heading"),
    [
        ("400.html", "요청을 확인해 주세요"),
        ("403.html", "이 페이지에 접근할 수 없습니다"),
        ("404.html", "페이지를 찾을 수 없습니다"),
        ("500.html", "지금은 페이지를 표시할 수 없습니다"),
    ],
)
def test_error_templates_render_recovery_link(template_name: str, heading: str) -> None:
    html = render(template_name, {"home_url": "/"})

    assert heading in html
    assert '<main id="main-content"' in html
    assert 'href="/"' in html


def test_public_templates_do_not_contain_forbidden_claims() -> None:
    templates = [
        *Path(settings.BASE_DIR, "grocery", "templates").rglob("*.html"),
        *Path(settings.BASE_DIR, "templates").rglob("*.html"),
    ]
    rendered_templates = "\n".join(path.read_text(encoding="utf-8") for path in templates)

    for phrase in FORBIDDEN_PUBLIC_PHRASES:
        assert phrase not in rendered_templates


def test_styles_define_small_viewport_safety_focus_and_touch_targets() -> None:
    css = Path(settings.BASE_DIR, "grocery", "static", "grocery", "app.css").read_text(
        encoding="utf-8"
    )

    assert "box-sizing: border-box" in css
    assert "min-width: 0" in css
    assert "overflow-wrap: anywhere" in css
    assert ":focus-visible" in css
    assert "min-height: 2.75rem" in css
    assert "@media (min-width: 40rem)" in css
    assert "@media (min-width: 64rem)" in css
    assert "@media (forced-colors: active)" in css
