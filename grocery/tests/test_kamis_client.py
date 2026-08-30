"""Synthetic transport tests; no request in this module reaches the network."""

from __future__ import annotations

import json
import ssl
from collections.abc import Mapping
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from grocery.source.client import (
    CONNECT_READ_TIMEOUT_SECONDS,
    KAMIS_ENDPOINT,
    MAX_ATTEMPTS_PER_PAGE,
    MAX_PAGE_BYTES,
    REDACTED_REQUEST_SHAPE,
    JsonObject,
    JsonValue,
    KamisHttpClient,
    KamisTransportError,
)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str | None = "application/json; charset=utf-8",
        include_length: bool = True,
    ) -> None:
        self.body = body
        self.status = status
        headers: dict[str, str] = {}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if include_length:
            headers["Content-Length"] = str(len(body))
        self.headers: Mapping[str, str] = headers
        self.read_amounts: list[int | None] = []
        self.closed = False

    def read(self, amount: int | None = None) -> bytes:
        self.read_amounts.append(amount)
        return self.body if amount is None else self.body[:amount]

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, scripted: list[FakeResponse | Exception]) -> None:
        self.scripted = list(scripted)
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self.scripted:
            raise AssertionError("unexpected synthetic request")
        result = self.scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class LeakyNetworkOpener:
    """Simulates a dependency exception that contains the complete secret URL."""

    def __init__(self) -> None:
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> FakeResponse:
        del timeout
        self.requests.append(request)
        raise URLError(request.full_url)

    def __repr__(self) -> str:
        return "LeakyNetworkOpener(secret=synthetic-secret-material)"


def _item(identity: int) -> JsonObject:
    return {"synthetic_id": identity}


def _page_bytes(
    *,
    page_number: int = 1,
    page_size: int = 100,
    total_count: int = 1,
    items: list[JsonObject] | None = None,
    result_code: str = "0",
    result_message: str = "NORMAL_SERVICE",
) -> bytes:
    item_values: list[JsonValue] = list(items if items is not None else [_item(1)])
    payload: JsonObject = {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": result_message},
            "body": {
                "dataType": "JSON",
                "items": {"item": item_values},
                "numOfRows": page_size,
                "pageNo": page_number,
                "totalCount": total_count,
            },
        }
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _http_error(status: int) -> HTTPError:
    return HTTPError(
        "https://redacted.invalid/provider",
        status,
        "synthetic",
        Message(),
        None,
    )


def test_success_uses_only_fixed_https_parameters_and_encodes_decoding_key_once() -> None:
    encoded_key = "synthetic%2Bkey%2Fsegment%3D"
    decoded_key = "synthetic+key/segment="
    response = FakeResponse(_page_bytes(items=[_item(7)]))
    opener = FakeOpener([response])
    client = KamisHttpClient(open_url=opener, sleep=lambda _: None)

    result = client.fetch_recent_prices(encoded_key)

    assert result.rows == (_item(7),)
    assert result.call_count == 1
    assert opener.timeouts == [CONNECT_READ_TIMEOUT_SECONDS]
    assert response.read_amounts == [MAX_PAGE_BYTES + 1]
    assert response.closed is True

    request = opener.requests[0]
    split = urlsplit(request.full_url)
    endpoint = urlsplit(KAMIS_ENDPOINT)
    assert request.get_method() == "GET"
    assert split.scheme == "https"
    assert split.netloc == endpoint.netloc
    assert split.path == endpoint.path
    assert request.get_header("Accept") == "application/json"
    parameters = parse_qs(split.query, strict_parsing=True)
    assert set(parameters) == {"serviceKey", "returnType", "pageNo", "numOfRows"}
    assert parameters == {
        "serviceKey": [decoded_key],
        "returnType": ["json"],
        "pageNo": ["1"],
        "numOfRows": ["100"],
    }
    assert "serviceKey=synthetic%2Bkey%2Fsegment%3D" in split.query
    assert "%252B" not in split.query


def test_plain_decoding_key_is_encoded_by_the_http_client_once() -> None:
    opener = FakeOpener([FakeResponse(_page_bytes())])

    KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
        "synthetic+key/segment="
    )

    query = urlsplit(opener.requests[0].full_url).query
    assert "serviceKey=synthetic%2Bkey%2Fsegment%3D" in query
    assert "%252B" not in query


def test_pagination_reconciles_order_counts_and_stable_manifest() -> None:
    bodies = [
        _page_bytes(page_number=1, page_size=2, total_count=5, items=[_item(1), _item(2)]),
        _page_bytes(page_number=2, page_size=2, total_count=5, items=[_item(3), _item(4)]),
        _page_bytes(page_number=3, page_size=2, total_count=5, items=[_item(5)]),
    ]

    first = KamisHttpClient(
        open_url=FakeOpener([FakeResponse(body) for body in bodies]), sleep=lambda _: None
    ).fetch_recent_prices("synthetic-key", page_size=2)
    second = KamisHttpClient(
        open_url=FakeOpener([FakeResponse(body) for body in bodies]), sleep=lambda _: None
    ).fetch_recent_prices("synthetic-key", page_size=2)

    assert [row["synthetic_id"] for row in first.rows] == [1, 2, 3, 4, 5]
    assert [receipt.ordinal for receipt in first.page_receipts] == [1, 2, 3]
    assert [receipt.requested_page_number for receipt in first.page_receipts] == [1, 2, 3]
    assert [receipt.row_count for receipt in first.page_receipts] == [2, 2, 1]
    assert all(receipt.declared_total_count == 5 for receipt in first.page_receipts)
    assert first.call_count == 3
    assert first.page_receipts == second.page_receipts
    assert first.ordered_manifest_sha256 == second.ordered_manifest_sha256
    assert len(first.ordered_manifest_sha256) == 64


def test_zero_total_is_one_empty_reconciled_page() -> None:
    opener = FakeOpener([FakeResponse(_page_bytes(total_count=0, items=[], page_size=100))])

    result = KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
        "synthetic-key"
    )

    assert result.rows == ()
    assert len(result.page_receipts) == 1
    assert result.page_receipts[0].row_count == 0


def test_declared_page_budget_is_enforced_before_a_second_call() -> None:
    opener = FakeOpener([FakeResponse(_page_bytes(total_count=13, page_size=1, items=[_item(1)]))])

    with pytest.raises(KamisTransportError, match="page_budget_exceeded"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
            "synthetic-key", page_size=1
        )

    assert len(opener.requests) == 1


def test_total_network_call_budget_includes_retries() -> None:
    scripted: list[FakeResponse | Exception] = []
    for page_number in range(1, 7):
        scripted.extend(
            [
                _http_error(429),
                FakeResponse(
                    _page_bytes(
                        page_number=page_number,
                        page_size=1,
                        total_count=7,
                        items=[_item(page_number)],
                    )
                ),
            ]
        )
    opener = FakeOpener(scripted)

    with pytest.raises(KamisTransportError, match="call_budget_exceeded"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
            "synthetic-key", page_size=1
        )

    assert len(opener.requests) == 12


def test_http_429_retries_with_bounded_backoff_then_succeeds() -> None:
    opener = FakeOpener([_http_error(429), FakeResponse(_page_bytes())])
    sleeps: list[float] = []

    result = KamisHttpClient(open_url=opener, sleep=sleeps.append).fetch_recent_prices(
        "synthetic-key"
    )

    assert result.call_count == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize("status", [500, 503])
def test_retryable_http_status_exhausts_after_fixed_attempts(status: int) -> None:
    opener = FakeOpener([_http_error(status) for _ in range(MAX_ATTEMPTS_PER_PAGE)])
    sleeps: list[float] = []

    with pytest.raises(KamisTransportError, match="retry_exhausted") as raised:
        KamisHttpClient(open_url=opener, sleep=sleeps.append).fetch_recent_prices("synthetic-key")

    assert raised.value.http_status == status
    assert len(opener.requests) == MAX_ATTEMPTS_PER_PAGE
    assert sleeps == [0.25, 1.0]


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_is_terminal_and_never_followed(status: int) -> None:
    opener = FakeOpener([_http_error(status)])

    with pytest.raises(KamisTransportError, match="redirect_not_allowed") as raised:
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert raised.value.http_status == status
    assert len(opener.requests) == 1


def test_retryable_provider_error_retries_without_using_provider_message() -> None:
    provider_failure = _page_bytes(
        result_code="-10",
        result_message="synthetic-sensitive-message-must-not-appear",
    )
    opener = FakeOpener([FakeResponse(provider_failure), FakeResponse(_page_bytes())])

    result = KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
        "synthetic-key"
    )

    assert result.call_count == 2


@pytest.mark.parametrize("provider_code", ["-1", "-5", "-10", "22", "23"])
def test_every_documented_transient_provider_code_is_bounded(provider_code: str) -> None:
    opener = FakeOpener(
        [FakeResponse(_page_bytes(result_code=provider_code)) for _ in range(MAX_ATTEMPTS_PER_PAGE)]
    )

    with pytest.raises(KamisTransportError, match="retry_exhausted") as raised:
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert raised.value.provider_result_code == provider_code
    assert len(opener.requests) == MAX_ATTEMPTS_PER_PAGE


def test_terminal_provider_error_is_not_retried_or_echoed() -> None:
    provider_message = "synthetic-sensitive-message-must-not-appear"
    opener = FakeOpener(
        [FakeResponse(_page_bytes(result_code="-3", result_message=provider_message))]
    )

    with pytest.raises(KamisTransportError, match="terminal_provider_error") as raised:
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert raised.value.provider_result_code == "-3"
    assert provider_message not in str(raised.value)
    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    ("content_type", "error_code"),
    [
        ("text/html; charset=utf-8", "unexpected_content_type"),
        ("application/json", "missing_charset"),
        ("application/json; charset=euc-kr", "unexpected_charset"),
        (None, "missing_content_type"),
    ],
)
def test_content_type_and_utf8_are_strict(
    content_type: str | None,
    error_code: str,
) -> None:
    opener = FakeOpener([FakeResponse(_page_bytes(), content_type=content_type)])

    with pytest.raises(KamisTransportError, match=error_code):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")


def test_stream_larger_than_four_mib_is_rejected_without_parsing() -> None:
    response = FakeResponse(b"x" * (MAX_PAGE_BYTES + 1), include_length=False)
    opener = FakeOpener([response])

    with pytest.raises(KamisTransportError, match="page_too_large"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert response.read_amounts == [MAX_PAGE_BYTES + 1]


def test_declared_oversize_is_rejected_before_reading() -> None:
    response = FakeResponse(b"{}")
    response.headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(MAX_PAGE_BYTES + 1),
    }
    opener = FakeOpener([response])

    with pytest.raises(KamisTransportError, match="page_too_large"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert response.read_amounts == []


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_top_key",
        "extra_body_key",
        "wrong_data_type",
        "items_not_array",
        "wrong_declared_page",
        "wrong_declared_size",
    ],
)
def test_success_envelope_and_pagination_schema_are_exact(mutation: str) -> None:
    payload = json.loads(_page_bytes())
    assert isinstance(payload, dict)
    response = payload["response"]
    assert isinstance(response, dict)
    body = response["body"]
    assert isinstance(body, dict)
    if mutation == "extra_top_key":
        payload["unexpected"] = True
    elif mutation == "extra_body_key":
        body["unexpected"] = True
    elif mutation == "wrong_data_type":
        body["dataType"] = "XML"
    elif mutation == "items_not_array":
        body["items"] = {"item": {}}
    elif mutation == "wrong_declared_page":
        body["pageNo"] = 2
    elif mutation == "wrong_declared_size":
        body["numOfRows"] = 99
    raw = json.dumps(payload, separators=(",", ":")).encode()

    with pytest.raises(KamisTransportError):
        KamisHttpClient(
            open_url=FakeOpener([FakeResponse(raw)]), sleep=lambda _: None
        ).fetch_recent_prices("synthetic-key")


def test_duplicate_json_keys_are_terminal_schema_failure() -> None:
    raw = (
        b'{"response":{"header":{"resultCode":"0","resultCode":"0",'
        b'"resultMsg":"NORMAL_SERVICE"},"body":{}}}'
    )

    with pytest.raises(KamisTransportError, match="invalid_json"):
        KamisHttpClient(
            open_url=FakeOpener([FakeResponse(raw)]), sleep=lambda _: None
        ).fetch_recent_prices("synthetic-key")


def test_declared_total_change_is_terminal() -> None:
    opener = FakeOpener(
        [
            FakeResponse(_page_bytes(page_number=1, page_size=1, total_count=2, items=[_item(1)])),
            FakeResponse(_page_bytes(page_number=2, page_size=1, total_count=3, items=[_item(2)])),
        ]
    )

    with pytest.raises(KamisTransportError, match="declared_total_changed"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
            "synthetic-key", page_size=1
        )


def test_short_page_is_terminal() -> None:
    opener = FakeOpener([FakeResponse(_page_bytes(page_size=2, total_count=2, items=[_item(1)]))])

    with pytest.raises(KamisTransportError, match="page_row_count_mismatch"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
            "synthetic-key", page_size=2
        )


def test_network_dependency_cannot_leak_key_url_or_opener_repr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    encoded_key = "synthetic%2Bsecret%2Fmaterial%3D"
    decoded_key = "synthetic+secret/material="
    opener = LeakyNetworkOpener()
    client = KamisHttpClient(open_url=opener, sleep=lambda _: None)

    with pytest.raises(KamisTransportError, match="retry_exhausted") as raised:
        client.fetch_recent_prices(encoded_key)

    full_url = opener.requests[0].full_url
    visible_error = f"{raised.value!s} {raised.value!r}"
    assert encoded_key not in visible_error
    assert decoded_key not in visible_error
    assert full_url not in visible_error
    assert "synthetic-secret-material" not in repr(client)
    assert raised.value.request_shape == REDACTED_REQUEST_SHAPE
    assert "<redacted>" in visible_error
    assert raised.value.__context__ is None
    assert caplog.records == []


def test_tls_failure_is_terminal_and_not_retried() -> None:
    opener = FakeOpener([ssl.SSLError("synthetic TLS failure")])

    with pytest.raises(KamisTransportError, match="tls_error"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert len(opener.requests) == 1


@pytest.mark.parametrize("service_key", ["", None])
def test_missing_key_is_rejected_before_any_request(service_key: str | None) -> None:
    opener = FakeOpener([])

    with pytest.raises(KamisTransportError, match="service_key_missing"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
            service_key  # type: ignore[arg-type]
        )

    assert opener.requests == []


@pytest.mark.parametrize("page_size", [0, -1, 1001, True])
def test_invalid_page_size_is_terminal_before_any_request(page_size: int) -> None:
    opener = FakeOpener([])

    with pytest.raises(KamisTransportError, match="invalid_page_size"):
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
            "synthetic-key", page_size=page_size
        )

    assert opener.requests == []


def test_non_retryable_http_auth_failure_is_redacted_and_terminal() -> None:
    opener = FakeOpener([_http_error(401)])

    with pytest.raises(KamisTransportError, match="terminal_http_status") as raised:
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert raised.value.http_status == 401
    assert len(opener.requests) == 1


def test_unsafe_provider_code_is_schema_error_not_echoed() -> None:
    unsafe_code = "synthetic-key-material"
    opener = FakeOpener([FakeResponse(_page_bytes(result_code=unsafe_code))])

    with pytest.raises(KamisTransportError, match="invalid_provider_header") as raised:
        KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices("synthetic-key")

    assert unsafe_code not in str(raised.value)


def test_json_values_are_kept_in_memory_without_raw_response_artifact() -> None:
    nested: JsonValue = {"name": "합성 품목", "missing": None, "flags": [True, 1]}
    item: JsonObject = {"nested": nested}
    body = _page_bytes(items=[item])
    opener = FakeOpener([FakeResponse(body)])

    result = KamisHttpClient(open_url=opener, sleep=lambda _: None).fetch_recent_prices(
        "synthetic-key"
    )

    assert result.rows == (item,)
    assert not hasattr(result, "raw_body")
    assert not hasattr(result.page_receipts[0], "raw_body")
