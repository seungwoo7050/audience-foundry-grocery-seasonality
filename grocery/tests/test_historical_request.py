"""Unit tests for fixed historical endpoint and query contracts."""

from urllib.parse import parse_qs, urlsplit

import pytest

from grocery.source.historical_client import (
    is_safe_historical_request_shape,
    prepare_historical_request,
)
from grocery.source.historical_contract import (
    KAMIS_HISTORICAL_ENDPOINTS,
    HistoricalDataset,
    HistoricalPriceQuery,
)


@pytest.mark.parametrize(
    ("dataset", "query", "expected_parameters"),
    [
        (
            HistoricalDataset.MONTHLY,
            HistoricalPriceQuery(
                start="202401",
                end="202412",
                category_code="200",
                item_code="212",
                variety_code="00",
                grade_code="04",
                region_code="11000",
            ),
            {
                "cond[exmn_ym::GTE]": ["202401"],
                "cond[exmn_ym::LTE]": ["202412"],
                "cond[se_cd::EQ]": ["01"],
                "cond[ctgry_cd::EQ]": ["200"],
                "cond[item_cd::EQ]": ["212"],
                "cond[vrty_cd::EQ]": ["00"],
                "cond[grd_cd::EQ]": ["04"],
                "cond[sgg_cd::EQ]": ["11000"],
            },
        ),
        (
            HistoricalDataset.REGIONAL,
            HistoricalPriceQuery(
                start="20260801",
                end="20260831",
                category_code="400",
                region_code="11000",
            ),
            {
                "cond[exmn_ymd::GTE]": ["20260801"],
                "cond[exmn_ymd::LTE]": ["20260831"],
                "cond[se_cd::EQ]": ["01"],
                "cond[ctgry_cd::EQ]": ["400"],
                "cond[sgg_cd::EQ]": ["11000"],
            },
        ),
        (
            HistoricalDataset.MARKET,
            HistoricalPriceQuery(
                start="20260815",
                end="20260831",
                category_code="200",
                region_code="11000",
                market_code="110001",
            ),
            {
                "cond[exmn_ymd::GTE]": ["20260815"],
                "cond[exmn_ymd::LTE]": ["20260831"],
                "cond[ctgry_cd::EQ]": ["200"],
                "cond[sgg_cd::EQ]": ["11000"],
                "cond[mrkt_cd::EQ]": ["110001"],
            },
        ),
    ],
)
def test_approved_endpoint_and_query_allowlist_are_exact(
    dataset: HistoricalDataset,
    query: HistoricalPriceQuery,
    expected_parameters: dict[str, list[str]],
) -> None:
    prepared = prepare_historical_request(dataset, query)
    request = prepared.build("synthetic+key/segment=", 1, 100)

    actual_endpoint = urlsplit(request.full_url)
    approved_endpoint = urlsplit(KAMIS_HISTORICAL_ENDPOINTS[dataset])
    assert (actual_endpoint.scheme, actual_endpoint.netloc, actual_endpoint.path) == (
        "https",
        approved_endpoint.netloc,
        approved_endpoint.path,
    )
    assert parse_qs(actual_endpoint.query, strict_parsing=True) == {
        "serviceKey": ["synthetic+key/segment="],
        "returnType": ["JSON"],
        "pageNo": ["1"],
        "numOfRows": ["100"],
        **expected_parameters,
    }
    assert "selectable" not in actual_endpoint.query
    assert is_safe_historical_request_shape(prepared.request_shape)
    assert "synthetic" not in prepared.request_shape
    assert query.start not in prepared.request_shape

