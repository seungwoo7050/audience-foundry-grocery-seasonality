from django.template.loader import render_to_string

from grocery.tests.test_public_templates import comparison
from grocery.tests.test_vnext_public_templates import PUBLICATION, SERIES


def test_selection_renders_url_order_partial_and_empty_recovery() -> None:
    item = {
        **SERIES,
        "series_value": "11111111-1111-1111-1111-111111111111",
        "detail_url": "/series/1/",
        "remove_url": "/selection/",
        "current_price_machine": "8000",
        "current_price_label": "8,000원",
        "source_date_iso": "2026-08-29",
        "source_date_label": "2026년 8월 29일",
        "comparison": comparison("1주 전 제공값"),
    }
    base = {"home_url": "/", "catalog_url": "/", "publication": PUBLICATION}
    partial = render_to_string(
        "grocery/selection.html",
        {**base, "selection_state": "partial", "items": [item], "excluded_count": 1},
    )
    empty = render_to_string(
        "grocery/selection.html", {**base, "selection_state": "ready", "items": []}
    )

    assert "일부 품목을 제외했습니다" in partial
    assert "1주 전 제공값보다 2,000원 낮음 (-20.0%)" in " ".join(partial.split())
    assert "선택한 품목이 없습니다" in empty and "품목 둘러보기" in empty


def test_selection_add_form_appends_to_canonical_url_order() -> None:
    items = [
        {
            **SERIES,
            "item_name": name,
            "series_value": value,
            "detail_url": f"/series/{value}/",
            "remove_url": "/selection/",
            "current_price_label": "8,000원",
            "source_date_iso": "2026-08-29",
            "source_date_label": "2026년 8월 29일",
            "comparison": comparison("1주 전 제공값"),
        }
        for name, value in (
            ("배추", "11111111-1111-1111-1111-111111111111"),
            ("사과", "22222222-2222-2222-2222-222222222222"),
        )
    ]
    html = render_to_string(
        "grocery/selection.html",
        {
            "selection_state": "ready",
            "items": items,
            "publication": PUBLICATION,
            "selection_form_action": "/selection/",
            "can_add_selection": True,
            "selection_candidates": [
                {
                    "value": "33333333-3333-3333-3333-333333333333",
                    "label": "양파 · 일반 · 상품 · 1kg",
                }
            ],
        },
    )

    first = html.index('name="series" value="11111111-1111-1111-1111-111111111111"')
    second = html.index('name="series" value="22222222-2222-2222-2222-222222222222"')
    candidate = html.index('id="selection-add-item"')
    assert first < second < candidate
    assert 'class="selection-add__form" action="/selection/" method="get"' in html
    assert 'name="series" required aria-describedby="selection-add-hint"' in html
    assert "양파 · 일반 · 상품 · 1kg" in html


def test_selection_add_form_has_limit_and_candidate_empty_states() -> None:
    base = {"selection_state": "ready", "items": [], "publication": PUBLICATION}
    limit = render_to_string(
        "grocery/selection.html",
        {**base, "selection_limit_reached": True, "can_add_selection": False},
    )
    empty = render_to_string(
        "grocery/selection.html",
        {**base, "selection_limit_reached": False, "can_add_selection": False},
    )

    assert "다섯 품목을 모두 선택했습니다." in limit
    assert "현재 목록에 더 추가할 수 있는 공개 품목이 없습니다." in empty
    assert 'class="selection-add__form"' not in limit
    assert 'class="selection-add__form"' not in empty


def test_selection_renders_stale_publication_and_partial_exclusion_together() -> None:
    html = render_to_string(
        "grocery/selection.html",
        {
            "selection_state": "partial",
            "selection_is_stale": True,
            "excluded_count": 2,
            "items": [],
            "publication": {**PUBLICATION, "freshness_state": "stale"},
            "selection_limit_reached": False,
            "can_add_selection": False,
        },
    )

    assert "마지막 공개 자료를 표시합니다" in html
    assert "일부 품목을 제외했습니다" in html
    assert "현재 공개 목록에 없는 품목 2개는 표시하지 않았습니다." in html
