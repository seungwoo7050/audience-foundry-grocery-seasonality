from typing import cast

from django.template.loader import render_to_string

from grocery.tests.test_public_templates import catalog_context

SERIES = {
    "item_name": "배추",
    "category_label": "채소류",
    "variety_name": "봄",
    "grade_name": "상품",
    "unit_label": "포기 × 1",
}
PUBLICATION = {
    "checked_at_iso": "2026-08-31T09:00:00+09:00",
    "checked_at_display": "2026년 8월 31일 09:00",
    "freshness_state": "current",
    "freshness_label": "KAMIS 자료 확인 완료",
}
NAV = [
    {"label": "최근 조사값", "url": "/series/1/", "current": False, "available": True},
    {"label": "월별 기록", "url": "/series/1/history/", "current": True, "available": True},
    {"label": "지역별 조사값", "url": "/series/1/regions/", "current": False, "available": True},
]


def historical_base(state_key: str, state: str = "ready") -> dict[str, object]:
    return {
        "home_url": "/",
        "catalog_url": "/",
        "series": SERIES,
        "section_nav": NAV,
        "historical_publication": PUBLICATION,
        state_key: state,
        "retry_url": "/safe-retry/",
    }


def test_history_renders_supplementary_chart_and_exact_month_ledger() -> None:
    context = historical_base("history_state")
    context.update(
        {
            "history_form_action": "/series/1/history/",
            "region_options": [{"value": "r1", "label": "서울", "selected": True}],
            "range_options": [{"value": "36", "label": "36개월", "selected": True}],
            "selected_region": {"label": "서울"},
            "selected_range": {"label": "36개월"},
            "history_chart": {
                "view_box": "0 0 100 50",
                "y_ticks": [],
                "x_ticks": [],
                "range_segments": [{"points": "0,30 100,20 100,40 0,45"}],
                "mean_segments": [{"points": "0,35 100,25"}],
                "points": [{"x": "100", "y": "25"}],
            },
            "monthly_points": [
                {
                    "available": True,
                    "period_iso": "2026-07",
                    "period_label": "2026년 7월",
                    "mean_machine": "8000",
                    "mean_label": "8,000원",
                    "minimum_machine": "7000",
                    "minimum_label": "7,000원",
                    "maximum_machine": "9000",
                    "maximum_label": "9,000원",
                },
                {
                    "available": False,
                    "period_iso": "2026-08",
                    "period_label": "2026년 8월",
                    "unavailable_label": "KAMIS가 이 달의 값을 제공하지 않았습니다.",
                },
            ],
        }
    )

    html = render_to_string("grocery/history.html", context)

    assert html.count("<h1>") == 1
    assert 'class="history-chart__svg"' in html and 'aria-hidden="true"' in html
    assert 'points="0,35 100,25"' in html
    assert all(value in html for value in ("8,000원", "7,000원", "9,000원"))
    assert "KAMIS가 이 달의 값을 제공하지 않았습니다." in html
    assert 'style="' not in html


def test_history_leads_with_summary_and_opens_only_the_latest_year() -> None:
    latest = {
        "available": True,
        "period_iso": "2026-07",
        "period_label": "2026년 7월",
        "mean_machine": "8000",
        "mean_label": "8,000원",
        "minimum_machine": "7000",
        "minimum_label": "7,000원",
        "maximum_machine": "9000",
        "maximum_label": "9,000원",
    }
    older = {
        **latest,
        "period_iso": "2025-12",
        "period_label": "2025년 12월",
        "mean_machine": "7600",
        "mean_label": "7,600원",
    }
    context = historical_base("history_state")
    context.update(
        {
            "selected_region": {"label": "서울"},
            "selected_range": {"label": "36개월"},
            "monthly_points": [older, latest],
            "history_summary": {
                "latest": latest,
                "lowest": older,
                "highest": latest,
            },
            "history_year_groups": [
                {
                    "year": "2026",
                    "label": "2026년",
                    "is_latest": True,
                    "open": True,
                    "points": [latest],
                },
                {
                    "year": "2025",
                    "label": "2025년",
                    "is_latest": False,
                    "open": False,
                    "points": [older],
                },
            ],
        }
    )

    html = render_to_string("grocery/history.html", context)

    assert 'class="history-summary"' in html
    assert "최근 월평균" in html and "가장 낮은 월평균" in html
    assert '<details class="history-year history-year--latest" open>' in html
    assert html.index("2026년") < html.index("2025년")
    assert html.count("<details") == 2
    assert html.count(" open>") == 1
    assert 'class="history-year-groups" aria-label=' not in html


def test_region_and_market_pages_keep_provider_facts_distinct() -> None:
    regions = historical_base("regions_state")
    regions.update(
        {
            "date_options": [{"value": "2026-08-29", "label": "2026년 8월 29일", "selected": True}],
            "selected_date": {"iso": "2026-08-29", "label": "2026년 8월 29일"},
            "regional_rows": [
                {
                    "region_label": "서울",
                    "mean_machine": "8000",
                    "mean_label": "8,000원",
                    "minimum_machine": "7000",
                    "minimum_label": "7,000원",
                    "maximum_machine": "9000",
                    "maximum_label": "9,000원",
                    "meter": {"minimum_x": "20", "maximum_x": "80", "mean_x": "50"},
                    "markets_url": "/markets/",
                }
            ],
        }
    )
    markets = historical_base("markets_state")
    markets.update(
        {
            "regions_url": "/regions/",
            "selected_region": {"label": "서울"},
            "selected_date": {"iso": "2026-08-29", "label": "2026년 8월 29일"},
            "date_options": [{"value": "2026-08-29", "label": "2026년 8월 29일", "selected": True}],
            "market_summary": {
                "total_count": 31,
                "total_count_label": "31곳",
                "minimum_machine": "7900",
                "minimum_label": "7,900원",
                "maximum_machine": "8600",
                "maximum_label": "8,600원",
            },
            "market_rows": [
                {
                    "market_name": "양곡시장",
                    "price_machine": "8100",
                    "price_label": "8,100원",
                    "survey_date_iso": "2026-08-29",
                    "survey_date_label": "2026년 8월 29일",
                }
            ],
        }
    )

    region_html = render_to_string("grocery/regions.html", regions)
    market_html = render_to_string("grocery/markets.html", markets)

    assert 'x1="20"' in region_html and 'cx="50"' in region_html
    assert "시장별 값 보기" in region_html
    assert "각 값은 개별 시장 조사값이며 지역 평균이 아닙니다." in market_html
    assert "시장별 소매 조사값입니다" in market_html
    assert 'class="market-summary"' in market_html
    assert all(value in market_html for value in ("31곳", "7,900원", "8,600원"))


def test_historical_blocking_states_hide_controls_and_fact_ledgers() -> None:
    for template, state_key in (
        ("grocery/history.html", "history_state"),
        ("grocery/regions.html", "regions_state"),
        ("grocery/markets.html", "markets_state"),
    ):
        html = render_to_string(template, historical_base(state_key, "unavailable"))
        assert "아직 공개된 조사 자료가 없습니다" in html
        assert 'class="scope-form' not in html
        assert "-ledger" not in html


def test_historical_server_errors_without_series_keep_truthful_recovery() -> None:
    cases = (
        ("grocery/history.html", "history_state", "월별 조사 기록"),
        ("grocery/regions.html", "regions_state", "지역별 소매 조사값"),
        ("grocery/markets.html", "markets_state", "시장별 소매 조사값"),
    )
    rendered: dict[str, str] = {}

    for template, state_key, heading in cases:
        html = render_to_string(
            template,
            {"home_url": "/", state_key: "server_error", "retry_url": "/safe-retry/"},
        )
        rendered[template] = html
        assert html.count("<h1>") == 1
        assert f"<h1>{heading}</h1>" in html
        assert "조사 자료를 불러오지 못했습니다" in html

    markets_html = rendered["grocery/markets.html"]
    assert '<a href="/">← 채소·과일 소매 조사값</a>' in markets_html
    assert "← 지역별 조사값" not in markets_html


def test_catalog_validation_reveals_and_associates_advanced_controls() -> None:
    context = catalog_context(
        catalog_state="validation",
        validation_errors=[
            {"message": "품목명은 한 줄로 입력하세요.", "target": "catalog-query"},
            {"message": "비교 기간을 확인하세요.", "target": "catalog-period"},
            {"message": "변화 방향을 확인하세요.", "target": "catalog-direction"},
            {"message": "표시 순서를 확인하세요.", "target": "catalog-sort"},
        ],
        period_options=[{"value": "week", "label": "1주 비교", "selected": True}],
        direction_options=[{"value": "all", "label": "전체", "selected": True}],
        sort_options=[{"value": "name", "label": "품목명 순", "selected": True}],
        query_error="품목명은 한 줄로 입력하세요.",
        period_error=True,
        direction_error=True,
        sort_error=True,
    )

    html = render_to_string("grocery/catalog.html", context)

    assert '<div class="form-error" role="alert" aria-labelledby="validation-title">' in html
    assert '<details class="catalog-options" open>' in html
    query_control = html[html.index('id="catalog-query"') :]
    assert (
        'aria-describedby="catalog-query-hint validation-title"' in query_control.split(">", 1)[0]
    )
    for target in ("catalog-period", "catalog-direction", "catalog-sort"):
        assert f'href="#{target}"' in html
        control = html[html.index(f'id="{target}"') :]
        assert 'aria-invalid="true"' in control.split(">", 1)[0]
        assert 'aria-describedby="validation-title"' in control.split(">", 1)[0]


def test_catalog_keeps_selection_action_in_the_compact_identity_row() -> None:
    context = catalog_context()
    results = cast(list[dict[str, object]], context["results"])
    results[0]["selection_url"] = "/selection/?series=first"

    html = render_to_string("grocery/catalog.html", context)

    top_start = html.index('<div class="ledger-entry__top">')
    top_end = html.index("</div>", html.index('class="ledger-entry__actions"', top_start))
    top_html = html[top_start:top_end]
    assert 'class="ledger-entry__heading"' in top_html
    assert 'href="/selection/?series=first"' in top_html
