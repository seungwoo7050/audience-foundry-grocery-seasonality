from decimal import Decimal
from typing import cast

import pytest
from django.core.exceptions import ValidationError
from django.http import QueryDict

from grocery.forms import CatalogForm, HistoryForm, MarketsForm, RegionsForm, parse_selection_query
from grocery.security import SECURITY_HEADERS
from grocery.vnext_presentation import (
    MonthlyChartDatum,
    build_history_chart,
    build_history_summary,
    build_history_year_groups,
    build_market_summary,
    range_meter,
)


@pytest.mark.parametrize(
    "query_string",
    [
        "unknown=private-marker",
        "period=week&period=month",
        "q=private-marker&page=2",
        "page=01",
    ],
)
def test_catalog_query_rejects_noncanonical_state_without_reflecting_values(
    query_string: str,
) -> None:
    marker = "private-marker"
    form = CatalogForm(QueryDict(query_string))

    assert not form.is_valid()
    assert marker not in str(form.errors)


@pytest.mark.parametrize(
    ("form_type", "query_string"),
    [
        (HistoryForm, "region=AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        (HistoryForm, "region=aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"),
        (RegionsForm, "date=2026-8-01"),
        (RegionsForm, "date=2026-02-30"),
        (MarketsForm, "date=2026-08-01&page=001"),
    ],
)
def test_historical_forms_require_canonical_uuid_date_and_page(
    form_type: type, query_string: str
) -> None:
    assert not form_type(QueryDict(query_string)).is_valid()


def test_selection_preserves_first_seen_order_and_rejects_pre_deduplication_overflow() -> None:
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    parsed = parse_selection_query(QueryDict(f"series={first}&series={second}&series={first}"))

    assert tuple(map(str, parsed.series_ids)) == (first, second)
    with pytest.raises(ValidationError):
        parse_selection_query(QueryDict("&".join(f"series={first}" for _ in range(6))))


def test_monthly_chart_never_connects_across_a_missing_month() -> None:
    data = [
        MonthlyChartDatum("202601", Decimal("100"), Decimal("90"), Decimal("110")),
        MonthlyChartDatum("202602", Decimal("110"), Decimal("100"), Decimal("120")),
        MonthlyChartDatum("202604", Decimal("130"), Decimal("120"), Decimal("140")),
    ]

    chart = build_history_chart(data)
    mean_segments = cast(list[dict[str, str]], chart["mean_segments"])
    range_segments = cast(list[dict[str, str]], chart["range_segments"])
    points = cast(list[dict[str, str]], chart["points"])

    assert len(mean_segments) == 1
    assert len(range_segments) == 1
    assert len(points) == 3
    isolated_x = points[2]["x"]
    assert isolated_x not in mean_segments[0]["points"]
    assert isolated_x not in range_segments[0]["points"]
    assert chart["gap_markers"] == [{"x": "490.67", "label": "2026.03"}]
    assert chart["points"] == [
        {"x": "64", "y": "195.2"},
        {"x": "277.33", "y": "150.4"},
        {"x": "704", "y": "60.8"},
    ]


def test_regional_meter_uses_one_server_side_decimal_scale() -> None:
    assert range_meter(
        minimum=Decimal("900"),
        mean=Decimal("1000"),
        maximum=Decimal("1100"),
        scale_minimum=Decimal("800"),
        scale_maximum=Decimal("1200"),
    ) == {"minimum_x": "25", "mean_x": "50", "maximum_x": "75"}


def test_monthly_summaries_and_year_groups_are_server_prepared_and_deterministic() -> None:
    data = [
        MonthlyChartDatum("202412", Decimal("100"), Decimal("90"), Decimal("110")),
        MonthlyChartDatum("202501", Decimal("100"), Decimal("80"), Decimal("120")),
        MonthlyChartDatum("202502", Decimal("130"), Decimal("120"), Decimal("140")),
    ]

    assert build_history_summary(data) == {
        "latest": {
            "period_iso": "2025-02",
            "period_label": "2025년 2월",
            "mean_machine": "130",
            "mean_label": "130원",
        },
        "lowest": {
            "period_iso": "2024-12",
            "period_label": "2024년 12월",
            "mean_machine": "100",
            "mean_label": "100원",
        },
        "highest": {
            "period_iso": "2025-02",
            "period_label": "2025년 2월",
            "mean_machine": "130",
            "mean_label": "130원",
        },
    }
    groups = build_history_year_groups(data)
    assert [(group["year"], group["is_latest"], group["open"]) for group in groups] == [
        ("2025", True, True),
        ("2024", False, False),
    ]
    newest_points = cast(list[dict[str, object]], groups[0]["points"])
    assert [point["period_iso"] for point in newest_points] == [
        "2025-01",
        "2025-02",
    ]


def test_market_summary_uses_all_exact_provider_observations() -> None:
    assert build_market_summary(
        [Decimal("1250.50"), Decimal("900"), Decimal("900.00"), Decimal("1800")]
    ) == {
        "total_count": 4,
        "total_count_label": "4곳",
        "minimum_machine": "900",
        "minimum_label": "900원",
        "maximum_machine": "1800",
        "maximum_label": "1,800원",
    }


def test_presentation_summaries_fail_closed_for_missing_or_noncanonical_facts() -> None:
    with pytest.raises(ValueError):
        build_history_summary([])
    with pytest.raises(ValueError):
        build_history_year_groups(
            [
                MonthlyChartDatum("202501", Decimal("100"), Decimal("90"), Decimal("110")),
                MonthlyChartDatum("202501", Decimal("100"), Decimal("90"), Decimal("110")),
            ]
        )
    with pytest.raises(ValueError):
        build_market_summary([Decimal("NaN")])


def test_public_referrer_policy_sends_no_query_state() -> None:
    assert SECURITY_HEADERS["Referrer-Policy"] == "no-referrer"
