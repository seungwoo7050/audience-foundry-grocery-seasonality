"""Focused tests for reviewed historical dimensions and source month typing."""

import pytest

from grocery.source.historical_dimensions import HistoricalDimensionRegistry, YearMonth
from grocery.source.kamis import KamisParseError
from grocery.source.registry import INITIAL_RETAIL_IDENTITY_REGISTRY


def test_dimension_registry_is_immutable_and_exact() -> None:
    regions = {"11000": "서울"}
    markets = {("11000", "110001"): "합성시장"}
    registry = HistoricalDimensionRegistry(
        identity_registry=INITIAL_RETAIL_IDENTITY_REGISTRY,
        region_names=regions,
        market_names=markets,
        dimension_evidence_revision="synthetic-reviewed-v1",
    )
    regions["26000"] = "부산"
    markets[("11000", "110002")] = "다른시장"

    assert registry.region_names == {"11000": "서울"}
    assert registry.market_names == {("11000", "110001"): "합성시장"}
    with pytest.raises(TypeError):
        registry.region_names["26000"] = "부산"  # type: ignore[index]


@pytest.mark.parametrize(
    ("regions", "markets"),
    [
        ({}, {}),
        ({"unsafe": "서울"}, {}),
        ({"11000": "서울\n"}, {}),
        ({"11000": "서울"}, {("99999", "1"): "시장"}),
    ],
)
def test_invalid_reviewed_dimension_contract_is_rejected(
    regions: dict[str, str],
    markets: dict[tuple[str, str], str],
) -> None:
    with pytest.raises(ValueError):
        HistoricalDimensionRegistry(
            identity_registry=INITIAL_RETAIL_IDENTITY_REGISTRY,
            region_names=regions,
            market_names=markets,
            dimension_evidence_revision="synthetic-reviewed-v1",
        )


def test_year_month_is_typed_without_inventing_a_day() -> None:
    value = YearMonth.from_source("202602", row_index=0)

    assert (value.year, value.month, value.source_text()) == (2026, 2, "202602")
    assert not hasattr(value, "day")


@pytest.mark.parametrize("value", ["202613", "2026-02", 202602])
def test_invalid_source_month_is_redacted(value: object) -> None:
    with pytest.raises(KamisParseError, match=r"invalid_source_month \(row=3, field=exmn_ym\)"):
        YearMonth.from_source(value, row_index=3)
