"""Synthetic strict parser tests for public-data API 15156060."""

from decimal import Decimal

import pytest

from grocery.source.kamis import KamisParseError
from grocery.source.monthly_history import (
    KAMIS_MONTHLY_PRICE_FIELDS,
    parse_monthly_price_rows,
)
from grocery.tests.historical_fixtures import historical_registry, monthly_row


def test_monthly_row_parses_all_source_facts_without_derivation() -> None:
    result = parse_monthly_price_rows([monthly_row()], registry=historical_registry())

    assert len(KAMIS_MONTHLY_PRICE_FIELDS) == 28
    assert result.input_row_count == 1
    row = result.rows[0]
    assert row.source_effective_month.source_text() == "202602"
    assert row.region.code == "11000"
    assert row.pmm_avgprc == Decimal("1000.50")
    assert row.pmm_stddvtn == Decimal("100.25")
    assert row.pyy_lwprc == Decimal("700")
    assert row.source_recorded_at_raw == "2026-08-31 12:00:00"
    assert len(row.source_row_hash) == len(result.result_hash) == 64
    assert not {
        "trend",
        "seasonality",
        "market_type",
        "computed_average",
    } & row.canonical_data().keys()


def test_monthly_result_is_deterministic_across_input_and_mapping_order() -> None:
    first = monthly_row()
    second = monthly_row()
    second["exmn_ym"] = "202603"
    reversed_second = dict(reversed(tuple(second.items())))

    left = parse_monthly_price_rows([first, reversed_second], registry=historical_registry())
    right = parse_monthly_price_rows([second, first], registry=historical_registry())

    assert left == right
    assert [row.source_effective_month.source_text() for row in left.rows] == [
        "202602",
        "202603",
    ]


@pytest.mark.parametrize("missing_field", sorted(KAMIS_MONTHLY_PRICE_FIELDS))
def test_every_missing_monthly_field_fails_closed(missing_field: str) -> None:
    row = monthly_row()
    del row[missing_field]

    with pytest.raises(KamisParseError, match=rf"missing_field .*field={missing_field}"):
        parse_monthly_price_rows([row], registry=historical_registry())


def test_unknown_or_non_string_monthly_fields_do_not_echo_values() -> None:
    unknown = monthly_row()
    unknown["synthetic-secret-field"] = "synthetic-secret-value"
    with pytest.raises(KamisParseError, match="unknown_field") as raised:
        parse_monthly_price_rows([unknown], registry=historical_registry())
    assert "synthetic-secret" not in str(raised.value)

    wrong_type: dict[str, object] = {
        field: value for field, value in monthly_row().items()
    }
    wrong_type["pmm_avgprc"] = 1000
    with pytest.raises(KamisParseError, match="field_type_drift"):
        parse_monthly_price_rows([wrong_type], registry=historical_registry())


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("se_cd", "02", "unsupported_product_class"),
        ("ctgry_nm", "과일류", "category_name_drift"),
        ("unit", "kg", "unit_identity_drift"),
        ("sgg_nm", "다른지역", "region_code_name_drift"),
        ("exmn_ym", "202613", "invalid_source_month"),
        ("orgnl_reg_dt", "2026-08-31\n", "invalid_source_text"),
        ("pmm_avgprc", "1e3", "invalid_decimal"),
        ("pmm_avgprc", "0", "invalid_positive_decimal"),
        ("pmm_stddvtn", "-1", "invalid_decimal"),
    ],
)
def test_monthly_identity_date_text_and_decimal_drift_fails(
    field: str,
    value: str,
    error_code: str,
) -> None:
    row = monthly_row()
    row[field] = value

    with pytest.raises(KamisParseError, match=error_code):
        parse_monthly_price_rows([row], registry=historical_registry())


@pytest.mark.parametrize(
    ("field", "value"),
    [("pmm_lwprc", "1100"), ("pmm_hgprc", "900"), ("pyy_avgprc", "1200")],
)
def test_monthly_source_range_inversion_fails(field: str, value: str) -> None:
    row = monthly_row()
    row[field] = value

    with pytest.raises(KamisParseError, match="invalid_price_range"):
        parse_monthly_price_rows([row], registry=historical_registry())


def test_duplicate_monthly_semantic_identity_fails_even_if_values_change() -> None:
    changed = monthly_row()
    changed["pmm_avgprc"] = "1001"

    with pytest.raises(KamisParseError, match="duplicate_semantic_identity"):
        parse_monthly_price_rows(
            [monthly_row(), changed],
            registry=historical_registry(),
        )
