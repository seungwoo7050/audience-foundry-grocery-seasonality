from django.template.loader import render_to_string

from grocery.tests.test_public_templates import comparison
from grocery.tests.test_vnext_public_templates import PUBLICATION, SERIES


def test_selection_renders_url_order_partial_and_empty_recovery() -> None:
    item = {
        **SERIES,
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
