from django.template.loader import render_to_string

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


def test_region_and_market_pages_keep_provider_facts_distinct() -> None:
    regions = historical_base("regions_state")
    regions.update(
        {
            "date_options": [{"value": "2026-08-29", "label": "2026년 8월 29일", "selected": True}],
            "selected_date": {"label": "2026년 8월 29일"},
            "regional_rows": [
                {
                    "region_label": "서울",
                    "mean_machine": "8000",
                    "mean_label": "8,000원",
                    "minimum_label": "7,000원",
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
            "selected_date": {"label": "2026년 8월 29일"},
            "date_options": [{"value": "2026-08-29", "label": "2026년 8월 29일", "selected": True}],
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
    assert "각 값은 시장별 관측이며 지역 평균이 아닙니다." in market_html
    assert "시장별 소매 조사값입니다" in market_html


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
