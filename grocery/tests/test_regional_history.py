"""Synthetic strict parser tests for public-data API 15156062."""

from decimal import Decimal

import pytest

from grocery.source.kamis import KamisParseError
from grocery.source.regional_history import (
    KAMIS_REGIONAL_PRICE_FIELDS,
    parse_regional_price_rows,
)
from grocery.tests.historical_fixtures import historical_registry, regional_row


def test_regional_row_preserves_raw_and_converted_provider_ranges() -> None:
    result = parse_regional_price_rows([regional_row()], registry=historical_registry())

    assert len(KAMIS_REGIONAL_PRICE_FIELDS) == 21
    row = result.rows[0]
    assert row.source_effective_date.isoformat() == "2026-08-31"
    assert (row.raw_min_price, row.raw_average_price, row.raw_max_price) == (
        Decimal("800"),
        Decimal("1000"),
        Decimal("1200"),
    )
    assert (
        row.converted_min_price,
        row.converted_average_price,
        row.converted_max_price,
    ) == (Decimal("80.5"), Decimal("100.25"), Decimal("120.75"))
    assert len(result.result_hash) == len(row.source_row_hash) == 64


@pytest.mark.parametrize("missing_field", sorted(KAMIS_REGIONAL_PRICE_FIELDS))
def test_every_missing_regional_field_fails_closed(missing_field: str) -> None:
    row = regional_row()
    del row[missing_field]

    with pytest.raises(KamisParseError, match=rf"missing_field .*field={missing_field}"):
        parse_regional_price_rows([row], registry=historical_registry())


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("exmn_ymd", "20260230", "invalid_source_date"),
        ("sgg_nm", "다른지역", "region_code_name_drift"),
        ("item_nm", "다른품목", "item_code_name_drift"),
        ("exmn_dd_avg_prc", "0", "invalid_positive_decimal"),
        ("exmn_dd_cnvs_avg_prc", "1,000", "invalid_decimal"),
    ],
)
def test_regional_identity_date_and_decimal_drift_fails(
    field: str,
    value: str,
    error_code: str,
) -> None:
    row = regional_row()
    row[field] = value

    with pytest.raises(KamisParseError, match=error_code):
        parse_regional_price_rows([row], registry=historical_registry())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exmn_dd_min_prc", "1100"),
        ("exmn_dd_max_prc", "900"),
        ("exmn_dd_cnvs_avg_prc", "130"),
    ],
)
def test_raw_and_converted_range_inversions_fail_independently(field: str, value: str) -> None:
    row = regional_row()
    row[field] = value

    with pytest.raises(KamisParseError, match="invalid_price_range"):
        parse_regional_price_rows([row], registry=historical_registry())


def test_regional_result_is_order_stable_and_duplicate_safe() -> None:
    next_day = regional_row()
    next_day["exmn_ymd"] = "20260830"
    left = parse_regional_price_rows(
        [regional_row(), next_day], registry=historical_registry()
    )
    right = parse_regional_price_rows(
        [dict(reversed(tuple(next_day.items()))), regional_row()],
        registry=historical_registry(),
    )
    assert left == right

    changed = regional_row()
    changed["exmn_dd_avg_prc"] = "1001"
    with pytest.raises(KamisParseError, match="duplicate_semantic_identity"):
        parse_regional_price_rows(
            [regional_row(), changed],
            registry=historical_registry(),
        )
