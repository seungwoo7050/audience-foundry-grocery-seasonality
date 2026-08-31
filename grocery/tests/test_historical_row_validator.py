"""Focused contract tests for the shared historical row validator."""

from decimal import Decimal

import pytest

from grocery.source.historical_dimensions import HistoricalDimensionRegistry
from grocery.source.historical_parser import HistoricalRowValidator
from grocery.source.kamis import KamisParseError
from grocery.source.registry import INITIAL_RETAIL_IDENTITY_REGISTRY


def _registry() -> HistoricalDimensionRegistry:
    return HistoricalDimensionRegistry(
        identity_registry=INITIAL_RETAIL_IDENTITY_REGISTRY,
        region_names={"11000": "서울"},
        market_names={("11000", "110001"): "합성시장"},
        dimension_evidence_revision="synthetic-reviewed-v1",
    )


def _row() -> dict[str, str]:
    return {
        "se_cd": "01",
        "se_nm": "소매",
        "ctgry_cd": "200",
        "ctgry_nm": "채소류",
        "item_cd": "212",
        "item_nm": "양배추",
        "vrty_cd": "00",
        "vrty_nm": "양배추",
        "grd_cd": "04",
        "grd_nm": "상품",
        "unit": "포기",
        "unit_sz": "1",
        "sgg_cd": "11000",
        "sgg_nm": "서울",
        "exmn_ymd": "20260831",
        "value": "1000.50",
    }


def _validator(row: object) -> HistoricalRowValidator:
    return HistoricalRowValidator(
        row,
        row_index=2,
        expected_fields=frozenset(_row()),
        registry=_registry(),
    )


def test_exact_row_produces_reviewed_typed_values() -> None:
    validator = _validator(_row())

    assert validator.identity().item_name == "양배추"
    assert validator.region().name == "서울"
    assert validator.day().isoformat() == "2026-08-31"
    assert validator.positive_decimal("value") == Decimal("1000.50")


@pytest.mark.parametrize("mutation", ["missing", "unknown", "non_string"])
def test_shape_and_string_type_drift_fail_without_values(mutation: str) -> None:
    row: dict[str, object] = {field: value for field, value in _row().items()}
    if mutation == "missing":
        del row["value"]
    elif mutation == "unknown":
        row["secret-field"] = "secret-value"
    else:
        row["value"] = 1000

    with pytest.raises(KamisParseError) as raised:
        _validator(row)

    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value", "method", "error_code"),
    [
        ("sgg_nm", "서울\n", "region", "invalid_source_text"),
        ("exmn_ymd", "20260230", "day", "invalid_source_date"),
        ("value", "1e3", "decimal", "invalid_decimal"),
        ("value", "0", "decimal", "invalid_positive_decimal"),
    ],
)
def test_text_date_and_decimal_grammar_is_strict(
    field: str,
    value: str,
    method: str,
    error_code: str,
) -> None:
    row = _row()
    row[field] = value
    validator = _validator(row)

    with pytest.raises(KamisParseError, match=error_code):
        if method == "region":
            validator.region()
        elif method == "day":
            validator.day()
        else:
            validator.positive_decimal("value")
