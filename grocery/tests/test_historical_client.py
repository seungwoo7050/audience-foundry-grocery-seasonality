"""Synthetic historical transport tests; these tests never call a source API."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.error import URLError
from urllib.request import Request

import pytest

from grocery.source.client import JsonObject, KamisHttpClient, KamisTransportError
from grocery.source.historical_client import prepare_historical_request
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self.body = body
        self.headers: Mapping[str, str] = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
        }
        self.closed = False

    def read(self, amount: int | None = None) -> bytes:
        return self.body if amount is None else self.body[:amount]

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, scripted: list[FakeResponse | Exception]) -> None:
        self.scripted = list(scripted)
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> FakeResponse:
        del timeout
        self.requests.append(request)
        if not self.scripted:
            raise AssertionError("unexpected synthetic request")
        result = self.scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _page_bytes(
    *,
    page_number: int = 1,
    page_size: int = 100,
    total_count: int = 1,
    items: list[JsonObject] | None = None,
    result_code: object = "0",
) -> bytes:
    payload = {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "dataType": "JSON",
                "items": {"item": items if items is not None else [{"synthetic": "row"}]},
                "numOfRows": page_size,
                "pageNo": page_number,
                "totalCount": total_count,
            },
        }
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _regional_query() -> HistoricalPriceQuery:
    return HistoricalPriceQuery(
        start="20260801",
        end="20260831",
        category_code="200",
        region_code="11000",
    )


def test_historical_fetch_reuses_bounded_ordered_pagination() -> None:
    opener = FakeOpener(
        [
            FakeResponse(
                _page_bytes(
                    page_number=1,
                    page_size=1,
                    total_count=2,
                    items=[{"synthetic": "first"}],
                )
            ),
            FakeResponse(
                _page_bytes(
                    page_number=2,
                    page_size=1,
                    total_count=2,
                    items=[{"synthetic": "second"}],
                )
            ),
        ]
    )

    result = KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_historical_prices(
        HistoricalDataset.REGIONAL,
        "synthetic-key",
        query=_regional_query(),
        page_size=1,
    )

    assert [row["synthetic"] for row in result.rows] == ["first", "second"]
    assert [receipt.requested_page_number for receipt in result.page_receipts] == [1, 2]
    assert result.call_count == 2
    assert (
        result.request_scope_sha256
        == prepare_historical_request(HistoricalDataset.REGIONAL, _regional_query()).scope_sha256
    )


def test_invalid_query_is_translated_before_any_request() -> None:
    opener = FakeOpener([])

    with pytest.raises(KamisTransportError, match="missing_historical_region"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_historical_prices(
            HistoricalDataset.REGIONAL,
            "synthetic-key",
            query=HistoricalPriceQuery(start="20260801", end="20260831", category_code="200"),
        )

    assert opener.requests == []


@pytest.mark.parametrize("result_code", [0, "00", "-3"])
def test_success_code_must_be_the_exact_string_zero(result_code: object) -> None:
    opener = FakeOpener([FakeResponse(_page_bytes(result_code=result_code))])

    with pytest.raises(KamisTransportError):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_historical_prices(
            HistoricalDataset.REGIONAL,
            "synthetic-key",
            query=_regional_query(),
        )


def test_wrapperless_json_is_rejected() -> None:
    body = json.dumps({"header": {}, "body": {}}, separators=(",", ":")).encode()
    opener = FakeOpener([FakeResponse(body)])

    with pytest.raises(KamisTransportError, match="unexpected_envelope_keys"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_historical_prices(
            HistoricalDataset.MARKET,
            "synthetic-key",
            query=HistoricalPriceQuery(start="20260801", end="20260831", category_code="200"),
        )


def test_dependency_error_cannot_leak_url_key_or_query_values() -> None:
    class LeakyOpener:
        def __init__(self) -> None:
            self.requests: list[Request] = []

        def __call__(self, request: Request, timeout: float) -> FakeResponse:
            del timeout
            self.requests.append(request)
            raise URLError(request.full_url)

    opener = LeakyOpener()
    client = KamisHttpClient(open_url=opener, sleep=lambda _: None)

    with pytest.raises(KamisTransportError, match="retry_exhausted") as raised:
        client.fetch_historical_prices(
            HistoricalDataset.REGIONAL,
            "synthetic-secret-key",
            query=_regional_query(),
        )

    visible = f"{raised.value!s} {raised.value!r}"
    assert "synthetic-secret-key" not in visible
    assert "20260801" not in visible
    assert "11000" not in visible
    assert opener.requests[0].full_url not in visible
    assert "serviceKey:<redacted>" in visible
    assert raised.value.__context__ is None
