"""Reviewed dimension contracts and source-month type for historical rows."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from grocery.source.kamis import ExactIdentityRegistry, KamisParseError

_CODE = re.compile(r"[0-9]{1,20}\Z")

type MarketCodeKey = tuple[str, str]


@dataclass(frozen=True, slots=True, order=True)
class YearMonth:
    """A source month that does not invent a first-day date."""

    year: int
    month: int

    @classmethod
    def from_source(cls, value: object, *, row_index: int) -> YearMonth:
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{6}", value) is None:
            raise KamisParseError("invalid_source_month", row_index=row_index, field="exmn_ym")
        try:
            parsed = datetime.strptime(value, "%Y%m")
        except ValueError:
            raise KamisParseError(
                "invalid_source_month", row_index=row_index, field="exmn_ym"
            ) from None
        return cls(year=parsed.year, month=parsed.month)

    def source_text(self) -> str:
        return f"{self.year:04d}{self.month:02d}"


@dataclass(frozen=True, slots=True)
class RegionObservation:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class MarketObservation:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class HistoricalDimensionRegistry:
    """Reviewed identity, region, and market code/name contracts."""

    identity_registry: ExactIdentityRegistry
    region_names: Mapping[str, str]
    market_names: Mapping[MarketCodeKey, str]
    dimension_evidence_revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity_registry, ExactIdentityRegistry):
            raise TypeError("identity_registry must be an ExactIdentityRegistry")
        regions = dict(self.region_names)
        markets = dict(self.market_names)
        if not regions:
            raise ValueError("reviewed region registry cannot be empty")
        if not _is_registry_text(self.dimension_evidence_revision, maximum=200):
            raise ValueError("dimension_evidence_revision is invalid")
        for code, name in regions.items():
            if _CODE.fullmatch(code) is None or not _is_registry_text(name, maximum=100):
                raise ValueError("region registry contains an invalid code or name")
        for (region_code, market_code), name in markets.items():
            if (
                region_code not in regions
                or _CODE.fullmatch(market_code) is None
                or not _is_registry_text(name, maximum=100)
            ):
                raise ValueError("market registry contains an invalid code or name")
        object.__setattr__(self, "region_names", MappingProxyType(regions))
        object.__setattr__(self, "market_names", MappingProxyType(markets))


def is_bounded_source_text(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and all(not unicodedata.category(character).startswith("C") for character in value)
    )


def _is_registry_text(value: object, *, maximum: int) -> bool:
    return is_bounded_source_text(value, maximum=maximum)
