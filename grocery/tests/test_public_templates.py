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


def comparison(
    period_label: str,
    *,
    direction_code: str = "LOWER",
    direction_label: str = "낮음",
    percentage_display: str = "-20.0%",
    available: bool = True,
) -> dict[str, object]:
    if not available:
        return {
            "period_label": period_label,
            "available": False,
            "reference_price_display": "",
            "difference_display": "",
            "percentage_display": "",
            "direction_code": "UNAVAILABLE",
            "direction_label": "",
            "reference_date_display": "",
            "reference_date_unavailable": True,
            "unavailable_reason": "KAMIS가 이 기간의 비교값을 제공하지 않았습니다.",
            "microbar": None,
        }
    is_equal = direction_code == "EQUAL"
    return {
        "period_label": period_label,
        "available": True,
        "reference_price_display": "10,000원",
        "difference_display": "2,000원",
        "percentage_display": percentage_display,
        "direction_code": direction_code,
        "direction_label": direction_label,
        "reference_date_display": "",
        "reference_date_unavailable": True,
        "unavailable_reason": "",
        "microbar": {
            "x": 50 if is_equal else 30,
            "width": 0 if is_equal else 20,
            "capped": False,
            "cap_x": 50 if is_equal else 30,
            "is_equal": is_equal,
        },
    }


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
        "result_count_label": "공개 항목 2개",
        "publication": {
            "checked_at_iso": "2026-08-30T09:00:00+09:00",
            "checked_at_display": "2026년 8월 30일 09:00",
            "freshness_state": "current",
            "freshness_label": "KAMIS 자료 확인 완료",
        },
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
                "week_comparison": comparison("1주 전 제공값"),
            },
            {
                "url": "/series/400-411-00-01/",
                "category_label": "과일류",
                "item_name": "사과",
                "variety_name": "후지",
                "grade_name": "상품",
                "unit_label": "10개",
                "current_price_label": "25,000원",
                "source_date_iso": "2026-08-29",
                "source_date_label": "2026년 8월 29일",
                "week_comparison": comparison("1주 전 제공값", available=False),
            },
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
            comparison("1주 전 제공값"),
            comparison(
                "1개월 전 제공값",
                direction_code="EQUAL",
                direction_label="같음",
                percentage_display="0.0%",
            ),
            comparison("1년 전 제공값", available=False),
        ],
        "provenance": {
            "source_name": "한국농수산식품유통공사 KAMIS 최근일자 도·소매가격정보",
            "source_url": "https://www.data.go.kr/data/15156063/openapi.do",
            "dataset_id": "15156063",
            "source_date_iso": "2026-08-29",
            "source_date_label": "2026년 8월 29일",
            "coverage_label": "KAMIS 소매 조사 22개 도시 지역 전체 집계",
            "checked_at_display": "2026년 8월 30일 09:00",
            "checked_at_iso": "2026-08-30T09:00:00+09:00",
            "reviewed_at_label": "2026년 8월 30일 09:30",
            "reviewed_at_iso": "2026-08-30T09:30:00+09:00",
            "freshness_state": "current",
            "freshness_label": "KAMIS 자료 확인 완료",
        },
    }
    context.update(overrides)
    return context


def test_catalog_renders_brand_semantic_search_and_grouped_ledger() -> None:
    html = render("grocery/catalog.html", catalog_context())

    assert '<html lang="ko">' in html
    assert 'href="#main-content"' in html
    assert '<main id="main-content"' in html
    assert 'src="/static/grocery/brand-mark.svg"' in html
    assert '<span class="brand__name">초록장부</span>' in html
    assert '<span class="brand__description">채소·과일 소매 조사값</span>' in html
    assert 'role="search"' in html
    assert '<label for="catalog-query">품목명</label>' in html
    assert 'name="q"' in html
    assert 'aria-current="page"' in html
    assert '<span class="segment__selected-mark" aria-hidden="true">✓</span>' in html
    assert html.count('class="catalog-group"') == 2
    assert "품목·조건" in html
    assert "소매 조사 평균" in html
    assert "1주 비교" in html
    assert "조사일" in html
    assert "이동" in html
    assert "아주긴한국어공식품목명이줄바꿈되어야하는배추" in html
    assert "아주긴원문판매단위표시 포기 × 1" in html
    assert 'class="ledger-entry__link"' in html
    assert 'aria-label="아주긴한국어공식품목명이줄바꿈되어야하는배추 상세 보기"' in html
    assert '<span class="ledger-entry__action" aria-hidden="true">→</span>' in html
    assert "KAMIS 자료 확인 완료" in html


def test_catalog_shows_one_week_comparison_preview_without_longer_periods() -> None:
    html = render("grocery/catalog.html", catalog_context())

    assert "1주 전 제공값보다 2,000원 낮음 (-20.0%)" in " ".join(html.split())
    assert "1주 전 비교값 없음" in html
    assert "1개월 전" not in html
    assert "1년 전" not in html
    assert "10,000원" not in html
    assert "reference_price_display" not in html
    assert "comparison-meter" not in html


def test_catalog_validation_error_is_associated_with_blank_input() -> None:
    private_query = "잘못된 입력"
    html = render(
        "grocery/catalog.html",
        catalog_context(
            catalog_state="validation",
            query=private_query,
            query_error="품목명은 80자 이하로 입력하세요.",
        ),
    )

    assert 'id="search-error"' in html
    assert 'role="alert"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="catalog-query-hint search-error"' in html
    assert 'href="#catalog-query">품목명 입력으로 이동</a>' in html
    assert "입력 내용을 확인하세요" in html
    assert "품목명은 80자 이하로 입력하세요." in html
    assert private_query not in html
    assert "검색 결과가 없습니다" not in html


@pytest.mark.parametrize(
    ("state", "expected_copy", "expected_role"),
    [
        ("loading", "조사 자료를 불러오고 있습니다", 'role="status"'),
        ("empty", "검색 결과가 없습니다", 'role="status"'),
        ("unavailable", "아직 공개된 조사 자료가 없습니다", 'role="status"'),
        ("stale", "마지막 공개 자료를 표시합니다", 'role="status"'),
        ("server_error", "조사 자료를 불러오지 못했습니다", 'role="alert"'),
    ],
)
def test_catalog_state_has_plain_language_and_semantic_role(
    state: str,
    expected_copy: str,
    expected_role: str,
) -> None:
    context = catalog_context(catalog_state=state, retry_url="/")
    if state != "stale":
        context["results"] = []
    html = render("grocery/catalog.html", context)

    assert expected_copy in html
    assert expected_role in html


def test_blocking_catalog_states_do_not_render_search_or_false_results() -> None:
    for state in ("loading", "unavailable", "server_error"):
        html = render(
            "grocery/catalog.html",
            catalog_context(catalog_state=state, results=[], retry_url="/"),
        )

        assert 'role="search"' not in html
        assert 'class="segment-list"' not in html
        assert 'class="catalog-ledger"' not in html


def test_detail_renders_identity_ruled_comparisons_and_provenance() -> None:
    html = render("grocery/detail.html", detail_context())
    compact_html = " ".join(html.split())

    assert 'class="detail-intro"' in html
    assert 'class="current-price"' in html
    assert "개별 판매처의 판매 금액이 아닌 KAMIS 소매 조사 평균입니다." in html
    assert "품종" in html and "봄" in html
    assert "등급" in html and "상품" in html
    assert "판매 단위" in html and "포기 × 1" in html
    assert "조사 범위" in html and "22개 도시 지역 전체 집계" in html
    assert 'class="comparison-ledger"' in html
    assert 'class="comparison-column-head"' in html
    assert html.count('<li class="comparison-row') == 3
    for class_name in ("period", "reference", "difference", "date"):
        assert f"comparison-field--{class_name}" in html
    assert "조사일 평균이 비교값보다 2,000원 낮음 (-20.0%)" in compact_html
    assert "조사일 평균이 비교값과 같음 (0.0%)" in compact_html
    assert "KAMIS가 이 기간의 비교값을 제공하지 않았습니다." in html
    assert "<dt>비교 기준일</dt>" in html
    assert "KAMIS에서 제공하지 않음" in html
    assert "비교 기준일: KAMIS에서 제공하지 않음" not in html
    assert "데이터셋 15156063" in html
    assert "공개 검토 일시" in html
    assert '<time datetime="2026-08-29">' in html
    assert '<time datetime="2026-08-30T09:00:00+09:00">' in html
    assert 'x="30"' in html and 'width="20"' in html and 'cx="30"' in html
    assert 'style="' not in html


def test_detail_direction_is_not_conveyed_by_symbol_color_or_chart_alone() -> None:
    html = render("grocery/detail.html", detail_context())

    assert '<span class="direction__symbol" aria-hidden="true">' in html
    assert "낮음" in html
    assert "같음" in html
    assert "비교값 없음" in html
    assert 'class="comparison-meter' in html
    assert 'aria-hidden="true"' in html


@pytest.mark.parametrize(
    ("template_name", "heading"),
    [
        ("400.html", "요청 내용을 확인하세요"),
        ("403.html", "이 페이지를 볼 수 없습니다"),
        ("404.html", "페이지를 찾을 수 없습니다"),
        ("500.html", "페이지를 표시하지 못했습니다"),
    ],
)
def test_error_templates_render_plain_recovery_without_technical_details(
    template_name: str, heading: str
) -> None:
    html = render(template_name, {"home_url": "/"})

    assert heading in html
    assert '<main id="main-content"' in html
    assert 'href="/"' in html
    assert "<pre" not in html
    assert "<code" not in html
    assert "Traceback" not in html


def test_public_templates_keep_ssr_security_and_local_visual_contract() -> None:
    templates = [
        *Path(settings.BASE_DIR, "grocery", "templates").rglob("*.html"),
        *Path(settings.BASE_DIR, "templates").rglob("*.html"),
    ]
    template_source = "\n".join(path.read_text(encoding="utf-8") for path in templates)

    assert "<script" not in template_source.lower()
    assert "javascript:" not in template_source.lower()
    assert "<picture" not in template_source.lower()
    assert "style=" not in template_source.lower()
    assert "http://" not in template_source.lower()
    assert "source가" not in template_source
    assert "source 응답" not in template_source


def test_public_templates_do_not_contain_forbidden_claims() -> None:
    templates = [
        *Path(settings.BASE_DIR, "grocery", "templates").rglob("*.html"),
        *Path(settings.BASE_DIR, "templates").rglob("*.html"),
    ]
    template_source = "\n".join(path.read_text(encoding="utf-8") for path in templates)

    for phrase in FORBIDDEN_PUBLIC_PHRASES:
        assert phrase not in template_source


def test_styles_define_ledger_tokens_responsive_interaction_and_user_preferences() -> None:
    css = Path(settings.BASE_DIR, "grocery", "static", "grocery", "app.css").read_text(
        encoding="utf-8"
    )

    assert "@font-face" in css
    assert 'url("fonts/gowun-batang-bold.woff2")' in css
    assert "--page-width: 72rem" in css
    assert "box-sizing: border-box" in css
    assert "min-width: 0" in css
    assert "overflow-wrap: anywhere" in css
    assert ":focus-visible" in css
    assert "outline: 3px solid var(--color-focus)" in css
    assert ".ledger-entry__link" in css
    assert "min-height: 2.75rem" in css
    assert ".button:active" in css
    assert "@media (hover: hover)" in css
    assert "@media (min-width: 40rem)" in css
    assert "@media (min-width: 64rem)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (forced-colors: active)" in css
    assert "linear-gradient(" not in css
    assert "radial-gradient(" not in css
    assert "box-shadow" not in css
    assert "--color-lower: #245b73" in css
    assert "--color-higher: #245b73" in css
