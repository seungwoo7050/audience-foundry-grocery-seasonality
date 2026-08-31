"""Focused tests for historical dataset, filter, and inclusive range validation."""

import pytest

from grocery.source.historical_contract import (
    HISTORICAL_ENDPOINT_CONTRACTS,
    HistoricalContractError,
    HistoricalDataset,
    HistoricalPriceQuery,
    validate_historical_query,
)


def test_approved_dataset_paths_and_retail_scope_are_fixed() -> None:
    assert {
        dataset.value: contract.path
        for dataset, contract in HISTORICAL_ENDPOINT_CONTRACTS.items()
    } == {
        "15156060": "/B552845/perYearMonth/price",
        "15156062": "/B552845/perRegion/price",
        "15156065": "/B552845/periodRetail/price",
    }
    monthly = validate_historical_query(
        HistoricalDataset.MONTHLY,
        HistoricalPriceQuery(start="202401", end="202412", category_code="200"),
    )
    assert monthly.conditions == {
        "cond[exmn_ym::GTE]": "202401",
        "cond[exmn_ym::LTE]": "202412",
        "cond[ctgry_cd::EQ]": "200",
        "cond[se_cd::EQ]": "01",
    }


@pytest.mark.parametrize(
    ("dataset", "query", "error_code"),
    [
        (
            HistoricalDataset.MONTHLY,
            HistoricalPriceQuery(start="202613", end="202613", category_code="200"),
            "invalid_historical_date",
        ),
        (
            HistoricalDataset.MONTHLY,
            HistoricalPriceQuery(start="202001", end="202501", category_code="200"),
            "invalid_historical_range",
        ),
        (
            HistoricalDataset.REGIONAL,
            HistoricalPriceQuery(
                start="20260731", end="20260831", category_code="200", region_code="11000"
            ),
            "invalid_historical_range",
        ),
        (
            HistoricalDataset.REGIONAL,
            HistoricalPriceQuery(start="20260801", end="20260831", category_code="200"),
            "missing_historical_region",
        ),
        (
            HistoricalDataset.MARKET,
            HistoricalPriceQuery(start="20260801", end="20260831", category_code="999"),
            "invalid_historical_filter",
        ),
        (
            HistoricalDataset.MARKET,
            HistoricalPriceQuery(
                start="20260801", end="20260831", category_code="200", variety_code="00"
            ),
            "invalid_historical_filter",
        ),
        (
            HistoricalDataset.REGIONAL,
            HistoricalPriceQuery(
                start="20260801",
                end="20260831",
                category_code="200",
                region_code="11000",
                market_code="110001",
            ),
            "invalid_historical_filter",
        ),
    ],
)
def test_invalid_ranges_and_filters_fail_closed(
    dataset: HistoricalDataset,
    query: HistoricalPriceQuery,
    error_code: str,
) -> None:
    with pytest.raises(HistoricalContractError, match=error_code):
        validate_historical_query(dataset, query)
