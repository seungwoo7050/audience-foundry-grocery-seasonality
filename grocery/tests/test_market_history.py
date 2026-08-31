"""Synthetic strict parser tests for public-data API 15156065."""

from decimal import Decimal

import pytest

from grocery.source.kamis import KamisParseError
from grocery.source.market_history import KAMIS_MARKET_PRICE_FIELDS, parse_market_price_rows
from grocery.tests.historical_fixtures import historical_registry, market_row


def test_market_row_preserves_observed_values_without_market_inference() -> None:
    result = parse_market_price_rows([market_row()], registry=historical_registry())

    assert len(KAMIS_MARKET_PRICE_FIELDS) == 20
    row = result.rows[0]
    assert row.source_effective_date.isoformat() == "2026-08-31"
    assert (row.region.code, row.market.code) == ("11000", "110001")
    assert row.raw_observed_price == Decimal("1000.50")
    assert row.converted_observed_price == Decimal("77.25")
    assert row.source_recorded_at_raw == "2026-08-31 12:00:00"
    assert not {"market_type", "computed_average", "trend"} & row.canonical_data().keys()
    assert len(result.result_hash) == len(row.source_row_hash) == 64


@pytest.mark.parametrize("missing_field", sorted(KAMIS_MARKET_PRICE_FIELDS))
def test_every_missing_market_field_fails_closed(missing_field: str) -> None:
    row = market_row()
    del row[missing_field]

    with pytest.raises(KamisParseError, match=rf"missing_field .*field={missing_field}"):
        parse_market_price_rows([row], registry=historical_registry())


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("exmn_ymd", "20260832", "invalid_source_date"),
        ("sgg_nm", "다른지역", "region_code_name_drift"),
        ("mrkt_nm", "다른시장", "market_code_name_drift"),
        ("mrkt_cd", "260001", "market_code_name_drift"),
        ("exmn_dd_prc", "0", "invalid_positive_decimal"),
        ("exmn_dd_cnvs_prc", "+77", "invalid_decimal"),
        ("orgnl_reg_dt", "2026-08-31\u0000", "invalid_source_text"),
    ],
)
def test_market_dimension_date_decimal_and_text_drift_fails(
    field: str,
    value: str,
    error_code: str,
) -> None:
    row = market_row()
    row[field] = value

    with pytest.raises(KamisParseError, match=error_code):
        parse_market_price_rows([row], registry=historical_registry())


def test_market_result_is_order_stable_and_duplicate_safe() -> None:
    busan = market_row()
    busan.update(
        {
            "sgg_cd": "26000",
            "sgg_nm": "부산",
            "mrkt_cd": "260001",
            "mrkt_nm": "합성부산시장",
        }
    )
    left = parse_market_price_rows([market_row(), busan], registry=historical_registry())
    right = parse_market_price_rows(
        [dict(reversed(tuple(busan.items()))), market_row()],
        registry=historical_registry(),
    )
    assert left == right

    changed = market_row()
    changed["exmn_dd_prc"] = "1001"
    with pytest.raises(KamisParseError, match="duplicate_semantic_identity"):
        parse_market_price_rows(
            [market_row(), changed],
            registry=historical_registry(),
        )
