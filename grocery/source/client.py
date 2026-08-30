"""Secret-safe HTTPS transport for the KAMIS recent-price endpoint.

The transport deliberately retains only normalized rows and redacted receipts. Raw
response bodies and request URLs exist only inside a single call and are never put in
exceptions, logs, return values, or object representations.
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from http.client import HTTPMessage
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    Request,
    build_opener,
)

KAMIS_ENDPOINT = "https://apis.data.go.kr/B552845/recent/price"
REDACTED_REQUEST_SHAPE = (
    "GET /B552845/recent/price parameters=[numOfRows,pageNo,returnType,serviceKey:<redacted>]"
)
CONNECT_READ_TIMEOUT_SECONDS = 10.0
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 12
MAX_CALLS = 12
MAX_ATTEMPTS_PER_PAGE = 3
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1_000

_REQUEST_PARAMETER_NAMES = frozenset({"serviceKey", "returnType", "pageNo", "numOfRows"})
_SUCCESS_TOP_LEVEL_KEYS = frozenset({"response"})
_SUCCESS_RESPONSE_KEYS = frozenset({"header", "body"})
_HEADER_KEYS = frozenset({"resultCode", "resultMsg"})
_BODY_KEYS = frozenset({"dataType", "items", "numOfRows", "pageNo", "totalCount"})
_ITEMS_KEYS = frozenset({"item"})
_SAFE_PROVIDER_CODE = re.compile(r"-?[0-9]{1,3}\Z")
_RETRYABLE_PROVIDER_CODES = frozenset({"-1", "-5", "-10", "22", "23"})
_RETRY_DELAYS_SECONDS = (0.25, 1.0)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type OpenUrl = Callable[[Request, float], ResponseLike]
type Sleep = Callable[[float], None]


class ResponseLike(Protocol):
    """Small response surface used by urllib and deterministic tests."""

    @property
    def status(self) -> int: ...

    @property
    def headers(self) -> HTTPMessage | Mapping[str, str]: ...

    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl
        return None


class KamisTransportError(RuntimeError):
    """A redacted, operationally safe transport failure."""

    def __init__(
        self,
        code: str,
        *,
        page_number: int | None = None,
        attempt: int | None = None,
        http_status: int | None = None,
        provider_result_code: str | None = None,
    ) -> None:
        self.code = code
        self.page_number = page_number
        self.attempt = attempt
        self.http_status = http_status
        self.provider_result_code = provider_result_code
        self.request_shape = REDACTED_REQUEST_SHAPE
        details = [f"code={code}"]
        if page_number is not None:
            details.append(f"page={page_number}")
        if attempt is not None:
            details.append(f"attempt={attempt}")
        if http_status is not None:
            details.append(f"http_status={http_status}")
        if provider_result_code is not None:
            details.append(f"provider_result_code={provider_result_code}")
        details.append(f"request={REDACTED_REQUEST_SHAPE}")
        super().__init__(" ".join(details))


@dataclass(frozen=True, slots=True)
class PageReceipt:
    """Raw-free evidence for one ordered source page."""

    ordinal: int
    requested_page_number: int
    declared_page_number: int
    declared_page_size: int
    declared_total_count: int
    row_count: int
    http_status: int
    provider_result_code: str
    byte_length: int
    body_sha256: str


@dataclass(frozen=True, slots=True)
class KamisFetchResult:
    """In-memory source rows plus their deterministic ordered manifest."""

    rows: tuple[JsonObject, ...]
    page_receipts: tuple[PageReceipt, ...]
    ordered_manifest_sha256: str
    call_count: int


@dataclass(frozen=True, slots=True)
class _DecodedPage:
    items: tuple[JsonObject, ...]
    declared_page_number: int
    declared_page_size: int
    declared_total_count: int
    provider_result_code: str
    byte_length: int
    body_sha256: str


@dataclass(frozen=True, slots=True)
class _RetrySignal:
    code: str
    http_status: int | None = None
    provider_result_code: str | None = None


class _CallBudget:
    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def consume(self, *, page_number: int, attempt: int) -> None:
        if self.count >= MAX_CALLS:
            raise KamisTransportError(
                "call_budget_exceeded", page_number=page_number, attempt=attempt
            )
        self.count += 1


class KamisHttpClient:
    """Fetch KAMIS pages without ever retaining a credential on the client."""

    __slots__ = ("_open_url", "_sleep")

    def __init__(
        self,
        *,
        open_url: OpenUrl | None = None,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._open_url = open_url if open_url is not None else _default_open_url()
        self._sleep = sleep

    def __repr__(self) -> str:
        return "KamisHttpClient(endpoint=/B552845/recent/price, credential=<not-retained>)"

    def fetch_recent_prices(
        self,
        service_key: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> KamisFetchResult:
        """Fetch every ordered page, keeping raw bytes only within this call."""

        normalized_key = _normalize_service_key(service_key)
        _validate_page_size(page_size)
        budget = _CallBudget()
        rows: list[JsonObject] = []
        receipts: list[PageReceipt] = []
        expected_total: int | None = None
        page_number = 1

        while True:
            page = self._fetch_page(
                normalized_key,
                page_number=page_number,
                page_size=page_size,
                budget=budget,
            )

            if expected_total is None:
                expected_total = page.declared_total_count
                required_pages = max(1, (expected_total + page_size - 1) // page_size)
                if required_pages > MAX_PAGES:
                    raise KamisTransportError("page_budget_exceeded", page_number=page_number)
            elif page.declared_total_count != expected_total:
                raise KamisTransportError("declared_total_changed", page_number=page_number)

            remaining = expected_total - len(rows)
            expected_row_count = min(page_size, max(0, remaining))
            if len(page.items) != expected_row_count:
                raise KamisTransportError("page_row_count_mismatch", page_number=page_number)

            rows.extend(page.items)
            receipts.append(
                PageReceipt(
                    ordinal=len(receipts) + 1,
                    requested_page_number=page_number,
                    declared_page_number=page.declared_page_number,
                    declared_page_size=page.declared_page_size,
                    declared_total_count=page.declared_total_count,
                    row_count=len(page.items),
                    http_status=200,
                    provider_result_code=page.provider_result_code,
                    byte_length=page.byte_length,
                    body_sha256=page.body_sha256,
                )
            )

            if len(rows) == expected_total:
                break
            if len(rows) > expected_total:
                raise KamisTransportError("row_total_exceeded", page_number=page_number)
            page_number += 1
            if page_number > MAX_PAGES:
                raise KamisTransportError("page_budget_exceeded", page_number=page_number)

        frozen_receipts = tuple(receipts)
        return KamisFetchResult(
            rows=tuple(rows),
            page_receipts=frozen_receipts,
            ordered_manifest_sha256=_ordered_manifest_sha256(frozen_receipts),
            call_count=budget.count,
        )

    def _fetch_page(
        self,
        normalized_key: str,
        *,
        page_number: int,
        page_size: int,
        budget: _CallBudget,
    ) -> _DecodedPage:
        last_retry: _RetrySignal | None = None

        for attempt in range(1, MAX_ATTEMPTS_PER_PAGE + 1):
            budget.consume(page_number=page_number, attempt=attempt)
            outcome = self._request_once(
                normalized_key,
                page_number=page_number,
                page_size=page_size,
            )
            if isinstance(outcome, _DecodedPage):
                return outcome

            last_retry = outcome
            if attempt < MAX_ATTEMPTS_PER_PAGE:
                self._sleep(_RETRY_DELAYS_SECONDS[attempt - 1])

        if last_retry is None:
            raise KamisTransportError("invalid_retry_state", page_number=page_number)
        raise KamisTransportError(
            "retry_exhausted",
            page_number=page_number,
            attempt=MAX_ATTEMPTS_PER_PAGE,
            http_status=last_retry.http_status,
            provider_result_code=last_retry.provider_result_code,
        )

    def _request_once(
        self,
        normalized_key: str,
        *,
        page_number: int,
        page_size: int,
    ) -> _DecodedPage | _RetrySignal:
        request = _build_request(
            normalized_key,
            page_number=page_number,
            page_size=page_size,
        )
        response: ResponseLike | None = None
        safe_error: KamisTransportError | None = None
        retry: _RetrySignal | None = None
        raw_body: bytes | None = None
        status: int | None = None
        content_type: str | None = None

        try:
            response = self._open_url(request, CONNECT_READ_TIMEOUT_SECONDS)
            status = response.status
            if not isinstance(status, int) or isinstance(status, bool):
                safe_error = KamisTransportError("invalid_http_status", page_number=page_number)
            elif 300 <= status < 400:
                safe_error = KamisTransportError(
                    "redirect_not_allowed", page_number=page_number, http_status=status
                )
            elif status == 429 or 500 <= status < 600:
                retry = _RetrySignal("retryable_http_status", http_status=status)
            elif status != 200:
                safe_error = KamisTransportError(
                    "terminal_http_status", page_number=page_number, http_status=status
                )
            else:
                content_type = _validated_content_type(response.headers)
                declared_length = _content_length(response.headers)
                if declared_length is not None and declared_length > MAX_PAGE_BYTES:
                    safe_error = KamisTransportError(
                        "page_too_large", page_number=page_number, http_status=status
                    )
                else:
                    candidate_body = response.read(MAX_PAGE_BYTES + 1)
                    if isinstance(candidate_body, bytes):
                        raw_body = candidate_body
                    else:
                        safe_error = KamisTransportError(
                            "response_body_not_bytes", page_number=page_number
                        )
        except HTTPError as error:
            status = error.code if isinstance(error.code, int) else None
            if status is not None and 300 <= status < 400:
                safe_error = KamisTransportError(
                    "redirect_not_allowed", page_number=page_number, http_status=status
                )
            elif status == 429 or (status is not None and 500 <= status < 600):
                retry = _RetrySignal("retryable_http_status", http_status=status)
            else:
                safe_error = KamisTransportError(
                    "terminal_http_status", page_number=page_number, http_status=status
                )
            try:
                error.close()
            except Exception:  # noqa: S110 - dependency errors can include request URLs.
                pass
        except KamisTransportError as error:
            safe_error = error
        except ssl.SSLCertVerificationError:
            safe_error = KamisTransportError("tls_verification_failed", page_number=page_number)
        except ssl.SSLError:
            safe_error = KamisTransportError("tls_error", page_number=page_number)
        except TimeoutError:
            retry = _RetrySignal("timeout")
        except URLError, ConnectionError, OSError:
            retry = _RetrySignal("network_error")
        except Exception:
            safe_error = KamisTransportError("transport_internal_error", page_number=page_number)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: S110 - dependency errors can include request URLs.
                    pass

        if safe_error is not None:
            raise safe_error
        if retry is not None:
            return retry
        if status != 200 or content_type != "application/json" or raw_body is None:
            raise KamisTransportError("invalid_response_state", page_number=page_number)
        if len(raw_body) > MAX_PAGE_BYTES:
            raise KamisTransportError("page_too_large", page_number=page_number, http_status=status)

        return _decode_page(
            raw_body,
            requested_page_number=page_number,
            requested_page_size=page_size,
        )


def _default_open_url() -> OpenUrl:
    context = ssl.create_default_context()
    opener = build_opener(HTTPSHandler(context=context), _NoRedirectHandler())
    director: OpenerDirector = opener

    def open_url(request: Request, timeout: float) -> ResponseLike:
        return cast(ResponseLike, director.open(request, timeout=timeout))

    return open_url


def _normalize_service_key(service_key: str) -> str:
    if not isinstance(service_key, str) or not service_key:
        raise KamisTransportError("service_key_missing")
    normalized = unquote(service_key)
    if not normalized:
        raise KamisTransportError("service_key_missing")
    return normalized


def _validate_page_size(page_size: int) -> None:
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or page_size < 1
        or page_size > MAX_PAGE_SIZE
    ):
        raise KamisTransportError("invalid_page_size")


def _build_request(normalized_key: str, *, page_number: int, page_size: int) -> Request:
    parameters = {
        "serviceKey": normalized_key,
        "returnType": "json",
        "pageNo": str(page_number),
        "numOfRows": str(page_size),
    }
    if frozenset(parameters) != _REQUEST_PARAMETER_NAMES:
        raise KamisTransportError("request_parameter_allowlist_violation")
    query = urlencode(parameters, doseq=False, safe="")
    return Request(  # noqa: S310 - the endpoint is a fixed HTTPS constant.
        f"{KAMIS_ENDPOINT}?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )


def _validated_content_type(headers: HTTPMessage | Mapping[str, str]) -> str:
    raw_content_type = headers.get("Content-Type")
    if not isinstance(raw_content_type, str):
        raise KamisTransportError("missing_content_type")
    message = Message()
    message["Content-Type"] = raw_content_type
    media_type = message.get_content_type().lower()
    charset = message.get_content_charset()
    if media_type != "application/json":
        raise KamisTransportError("unexpected_content_type")
    if charset is None:
        raise KamisTransportError("missing_charset")
    try:
        normalized_charset = charset.encode("ascii").decode("ascii").lower().replace("_", "-")
    except UnicodeError:
        raise KamisTransportError("unexpected_charset") from None
    if normalized_charset not in {"utf-8", "utf8"}:
        raise KamisTransportError("unexpected_charset")
    return media_type


def _content_length(headers: HTTPMessage | Mapping[str, str]) -> int | None:
    raw_length = headers.get("Content-Length")
    if raw_length is None:
        return None
    if not isinstance(raw_length, str) or not raw_length.isascii() or not raw_length.isdigit():
        raise KamisTransportError("invalid_content_length")
    return int(raw_length)


def _decode_page(
    raw_body: bytes,
    *,
    requested_page_number: int,
    requested_page_size: int,
) -> _DecodedPage | _RetrySignal:
    decoded: JsonValue | None = None
    decode_failed = False
    try:
        text = raw_body.decode("utf-8", errors="strict")
        decoded = cast(JsonValue, json.loads(text, object_pairs_hook=_reject_duplicate_keys))
    except UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError:
        decode_failed = True
    if decode_failed:
        raise KamisTransportError("invalid_json", page_number=requested_page_number)

    top = _require_object(decoded, "invalid_envelope", page_number=requested_page_number)
    _require_exact_keys(top, _SUCCESS_TOP_LEVEL_KEYS, page_number=requested_page_number)
    response = _require_object(
        top["response"], "invalid_envelope", page_number=requested_page_number
    )
    _require_exact_keys(response, _SUCCESS_RESPONSE_KEYS, page_number=requested_page_number)
    header = _require_object(
        response["header"], "invalid_header", page_number=requested_page_number
    )
    _require_exact_keys(header, _HEADER_KEYS, page_number=requested_page_number)

    provider_code_value = header["resultCode"]
    provider_message = header["resultMsg"]
    if (
        not isinstance(provider_code_value, str)
        or _SAFE_PROVIDER_CODE.fullmatch(provider_code_value) is None
        or not isinstance(provider_message, str)
    ):
        raise KamisTransportError("invalid_provider_header", page_number=requested_page_number)
    provider_code = provider_code_value
    if provider_code != "0":
        if provider_code in _RETRYABLE_PROVIDER_CODES:
            return _RetrySignal("retryable_provider_error", provider_result_code=provider_code)
        raise KamisTransportError(
            "terminal_provider_error",
            page_number=requested_page_number,
            provider_result_code=provider_code,
        )

    body = _require_object(response["body"], "invalid_body", page_number=requested_page_number)
    _require_exact_keys(body, _BODY_KEYS, page_number=requested_page_number)
    if body["dataType"] != "JSON":
        raise KamisTransportError("unexpected_data_type", page_number=requested_page_number)

    declared_page_number = _require_nonnegative_int(
        body["pageNo"], "invalid_declared_page", page_number=requested_page_number
    )
    declared_page_size = _require_nonnegative_int(
        body["numOfRows"], "invalid_declared_page_size", page_number=requested_page_number
    )
    declared_total_count = _require_nonnegative_int(
        body["totalCount"], "invalid_declared_total", page_number=requested_page_number
    )
    if declared_page_number != requested_page_number:
        raise KamisTransportError("declared_page_mismatch", page_number=requested_page_number)
    if declared_page_size != requested_page_size:
        raise KamisTransportError("declared_page_size_mismatch", page_number=requested_page_number)

    items_container = _require_object(
        body["items"], "invalid_items_envelope", page_number=requested_page_number
    )
    _require_exact_keys(items_container, _ITEMS_KEYS, page_number=requested_page_number)
    raw_items = items_container["item"]
    if not isinstance(raw_items, list):
        raise KamisTransportError("items_not_array", page_number=requested_page_number)
    items: list[JsonObject] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise KamisTransportError("item_not_object", page_number=requested_page_number)
        items.append(raw_item)

    return _DecodedPage(
        items=tuple(items),
        declared_page_number=declared_page_number,
        declared_page_size=declared_page_size,
        declared_total_count=declared_total_count,
        provider_result_code=provider_code,
        byte_length=len(raw_body),
        body_sha256=hashlib.sha256(raw_body).hexdigest(),
    )


class _DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


def _require_object(value: JsonValue, code: str, *, page_number: int) -> JsonObject:
    if not isinstance(value, dict):
        raise KamisTransportError(code, page_number=page_number)
    return value


def _require_exact_keys(
    value: JsonObject,
    expected: frozenset[str],
    *,
    page_number: int,
) -> None:
    if frozenset(value) != expected:
        raise KamisTransportError("unexpected_envelope_keys", page_number=page_number)


def _require_nonnegative_int(value: JsonValue, code: str, *, page_number: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise KamisTransportError(code, page_number=page_number)
    return value


def _ordered_manifest_sha256(receipts: tuple[PageReceipt, ...]) -> str:
    manifest = [receipt.body_sha256 for receipt in receipts]
    canonical = json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()
