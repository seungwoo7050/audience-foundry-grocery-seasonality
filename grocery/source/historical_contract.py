"""Fixed dataset, filter, and date-range contracts for KAMIS historical APIs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

MAX_HISTORICAL_MONTHS = 60
MAX_HISTORICAL_DAYS = 31
_FILTER_CODE = re.compile(r"[0-9]{1,20}\Z")


class HistoricalDataset(StrEnum):
    """Approved public-data API identifiers."""

    MONTHLY = "15156060"
    REGIONAL = "15156062"
    MARKET = "15156065"


@dataclass(frozen=True, slots=True, repr=False)
class HistoricalPriceQuery:
    """A bounded historical source slice; values are never retained in errors."""

    start: str
    end: str
    category_code: str
    item_code: str | None = None
    variety_code: str | None = None
    grade_code: str | None = None
    region_code: str | None = None
    market_code: str | None = None


@dataclass(frozen=True, slots=True)
class HistoricalEndpointContract:
    endpoint: str
    date_field: str
    allowed_filter_fields: frozenset[str]
    required_filter_fields: frozenset[str]
    monthly: bool

    @property
    def path(self) -> str:
        return f"/{self.endpoint.split('/', 3)[3]}"

    @property
    def allowed_condition_names(self) -> frozenset[str]:
        return frozenset(
            {
                f"cond[{self.date_field}::GTE]",
                f"cond[{self.date_field}::LTE]",
                *(f"cond[{field}::EQ]" for field in self.allowed_filter_fields),
            }
        )


HISTORICAL_ENDPOINT_CONTRACTS: Mapping[
    HistoricalDataset, HistoricalEndpointContract
] = MappingProxyType(
    {
        HistoricalDataset.MONTHLY: HistoricalEndpointContract(
            endpoint="https://apis.data.go.kr/B552845/perYearMonth/price",
            date_field="exmn_ym",
            allowed_filter_fields=frozenset(
                {"se_cd", "ctgry_cd", "item_cd", "vrty_cd", "grd_cd", "sgg_cd"}
            ),
            required_filter_fields=frozenset({"se_cd", "ctgry_cd"}),
            monthly=True,
        ),
        HistoricalDataset.REGIONAL: HistoricalEndpointContract(
            endpoint="https://apis.data.go.kr/B552845/perRegion/price",
            date_field="exmn_ymd",
            allowed_filter_fields=frozenset(
                {"se_cd", "ctgry_cd", "item_cd", "vrty_cd", "grd_cd", "sgg_cd"}
            ),
            required_filter_fields=frozenset({"se_cd", "ctgry_cd", "sgg_cd"}),
            monthly=False,
        ),
        HistoricalDataset.MARKET: HistoricalEndpointContract(
            endpoint="https://apis.data.go.kr/B552845/periodRetail/price",
            date_field="exmn_ymd",
            allowed_filter_fields=frozenset(
                {"ctgry_cd", "item_cd", "vrty_cd", "grd_cd", "sgg_cd", "mrkt_cd"}
            ),
            required_filter_fields=frozenset({"ctgry_cd"}),
            monthly=False,
        ),
    }
)

KAMIS_HISTORICAL_ENDPOINTS: Mapping[HistoricalDataset, str] = MappingProxyType(
    {dataset: contract.endpoint for dataset, contract in HISTORICAL_ENDPOINT_CONTRACTS.items()}
)


class HistoricalContractError(ValueError):
    """Value-free contract failure translated by the shared HTTP client."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedHistoricalQuery:
    """Exact condition names and values after contract validation."""

    dataset: HistoricalDataset
    conditions: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions", MappingProxyType(dict(self.conditions)))

    def __repr__(self) -> str:
        names = ",".join(sorted(self.conditions))
        return f"ValidatedHistoricalQuery(dataset={self.dataset.value}, condition_names=[{names}])"


def validate_historical_query(
    dataset: HistoricalDataset,
    query: HistoricalPriceQuery,
) -> ValidatedHistoricalQuery:
    """Validate endpoint scope, hierarchy, codes, and inclusive source range."""

    if not isinstance(dataset, HistoricalDataset):
        raise HistoricalContractError("invalid_historical_dataset")
    if not isinstance(query, HistoricalPriceQuery):
        raise HistoricalContractError("invalid_historical_filter")
    contract = HISTORICAL_ENDPOINT_CONTRACTS[dataset]
    if query.category_code not in {"200", "400"}:
        raise HistoricalContractError("invalid_historical_filter")
    if query.variety_code is not None and query.item_code is None:
        raise HistoricalContractError("invalid_historical_filter")
    if query.grade_code is not None and query.variety_code is None:
        raise HistoricalContractError("invalid_historical_filter")
    if query.market_code is not None and query.region_code is None:
        raise HistoricalContractError("invalid_historical_filter")
    _validate_range(contract, query.start, query.end)

    logical_filters: dict[str, str] = {"ctgry_cd": query.category_code}
    if "se_cd" in contract.allowed_filter_fields:
        logical_filters["se_cd"] = "01"
    for field, value in (
        ("item_cd", query.item_code),
        ("vrty_cd", query.variety_code),
        ("grd_cd", query.grade_code),
        ("sgg_cd", query.region_code),
        ("mrkt_cd", query.market_code),
    ):
        if value is not None:
            if field not in contract.allowed_filter_fields or _FILTER_CODE.fullmatch(value) is None:
                raise HistoricalContractError("invalid_historical_filter")
            logical_filters[field] = value
    if not contract.required_filter_fields <= logical_filters.keys():
        if "sgg_cd" in contract.required_filter_fields:
            raise HistoricalContractError("missing_historical_region")
        raise HistoricalContractError("invalid_historical_filter")

    conditions = {
        f"cond[{contract.date_field}::GTE]": query.start,
        f"cond[{contract.date_field}::LTE]": query.end,
        **{f"cond[{field}::EQ]": value for field, value in logical_filters.items()},
    }
    return ValidatedHistoricalQuery(dataset=dataset, conditions=conditions)


def _validate_range(contract: HistoricalEndpointContract, start: str, end: str) -> None:
    if contract.monthly:
        start_ordinal = _parse_month(start)
        end_ordinal = _parse_month(end)
        too_large = end_ordinal - start_ordinal + 1 > MAX_HISTORICAL_MONTHS
    else:
        start_day = _parse_day(start)
        end_day = _parse_day(end)
        start_ordinal = start_day.toordinal()
        end_ordinal = end_day.toordinal()
        too_large = (end_day - start_day).days + 1 > MAX_HISTORICAL_DAYS
    if start_ordinal > end_ordinal or too_large:
        raise HistoricalContractError("invalid_historical_range")


def _parse_month(value: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{6}", value) is None:
        raise HistoricalContractError("invalid_historical_date")
    try:
        parsed = datetime.strptime(value, "%Y%m")
    except ValueError:
        raise HistoricalContractError("invalid_historical_date") from None
    return parsed.year * 12 + parsed.month


def _parse_day(value: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise HistoricalContractError("invalid_historical_date")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise HistoricalContractError("invalid_historical_date") from None
