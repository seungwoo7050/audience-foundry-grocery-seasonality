"""Redacted request construction for validated KAMIS historical queries.

This module performs no I/O. The shared bounded transport in ``source.client`` owns
retry, pagination, byte limits, exact envelope decoding, and raw-free receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request

from grocery.source.historical_contract import (
    HISTORICAL_ENDPOINT_CONTRACTS,
    HistoricalContractError,
    HistoricalDataset,
    HistoricalPriceQuery,
    ValidatedHistoricalQuery,
    validate_historical_query,
)

_COMMON_PARAMETER_NAMES = frozenset({"serviceKey", "returnType", "pageNo", "numOfRows"})
_COMMON_REDACTED_NAMES = frozenset({"numOfRows", "pageNo", "returnType"})


@dataclass(frozen=True, slots=True)
class PreparedHistoricalRequest:
    """A validated condition set plus a value-free operational request shape."""

    query: ValidatedHistoricalQuery
    request_shape: str

    def build(self, normalized_key: str, page_number: int, page_size: int) -> Request:
        contract = HISTORICAL_ENDPOINT_CONTRACTS[self.query.dataset]
        if not self.query.conditions.keys() <= contract.allowed_condition_names:
            raise HistoricalContractError("request_parameter_allowlist_violation")
        parameters = {
            "serviceKey": normalized_key,
            "returnType": "JSON",
            "pageNo": str(page_number),
            "numOfRows": str(page_size),
            **self.query.conditions,
        }
        if frozenset(parameters) != _COMMON_PARAMETER_NAMES | frozenset(self.query.conditions):
            raise HistoricalContractError("request_parameter_allowlist_violation")
        query_string = urlencode(parameters, doseq=False, safe="")
        return Request(  # noqa: S310 - the endpoint is a fixed HTTPS constant.
            f"{contract.endpoint}?{query_string}",
            headers={"Accept": "application/json"},
            method="GET",
        )


def prepare_historical_request(
    dataset: HistoricalDataset,
    query: HistoricalPriceQuery,
) -> PreparedHistoricalRequest:
    """Validate and prepare one exact, redacted historical request contract."""

    validated = validate_historical_query(dataset, query)
    contract = HISTORICAL_ENDPOINT_CONTRACTS[validated.dataset]
    condition_names = frozenset(validated.conditions)
    if not condition_names <= contract.allowed_condition_names:
        raise HistoricalContractError("request_parameter_allowlist_violation")
    names = sorted(_COMMON_REDACTED_NAMES | condition_names)
    names.append("serviceKey:<redacted>")
    request_shape = f"GET {contract.path} parameters=[{','.join(names)}]"
    return PreparedHistoricalRequest(query=validated, request_shape=request_shape)


def is_safe_historical_request_shape(value: str) -> bool:
    """Return whether a shape contains names from one fixed contract and no values."""

    for contract in HISTORICAL_ENDPOINT_CONTRACTS.values():
        prefix = f"GET {contract.path} parameters=["
        if not value.startswith(prefix) or not value.endswith("]"):
            continue
        names = value[len(prefix) : -1].split(",")
        if not names or names[-1] != "serviceKey:<redacted>":
            return False
        actual_names = frozenset(names[:-1])
        allowed_names = contract.allowed_condition_names | _COMMON_REDACTED_NAMES
        return actual_names <= allowed_names and _COMMON_REDACTED_NAMES <= actual_names
    return False
