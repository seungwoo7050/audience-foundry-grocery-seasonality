"""Shared fail-closed validators for approved KAMIS historical row parsers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from grocery.source.historical_dimensions import (
    HistoricalDimensionRegistry,
    MarketObservation,
    RegionObservation,
    is_bounded_source_text,
)
from grocery.source.kamis import IdentityObservation, KamisParseError

_CODE = re.compile(r"[0-9]{1,20}\Z")
_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")
_MAX_PRICE = Decimal("999999999999.99")


@dataclass(frozen=True, slots=True)
class ParsedHistoricalResult[RowT]:
    rows: tuple[RowT, ...]
    input_row_count: int
    result_hash: str


class HistoricalRowValidator:
    """One exact source row plus redacted, index-only validation context."""

    __slots__ = ("_registry", "row", "row_index")

    def __init__(
        self,
        raw_row: object,
        *,
        row_index: int,
        expected_fields: frozenset[str],
        registry: HistoricalDimensionRegistry,
    ) -> None:
        if not isinstance(raw_row, Mapping):
            raise KamisParseError("row_not_object", row_index=row_index)
        if not all(isinstance(key, str) for key in raw_row):
            raise KamisParseError("non_string_field_name", row_index=row_index)
        actual_fields = frozenset(raw_row)
        if actual_fields != expected_fields:
            if missing := expected_fields - actual_fields:
                raise KamisParseError(
                    "missing_field", row_index=row_index, field=sorted(missing)[0]
                )
            raise KamisParseError("unknown_field", row_index=row_index)
        self.row = {str(key): value for key, value in raw_row.items()}
        self.row_index = row_index
        self._registry = registry
        for field, value in self.row.items():
            if not isinstance(value, str):
                raise KamisParseError("field_type_drift", row_index=row_index, field=field)

    def identity(self) -> IdentityObservation:
        for field in ("se_cd", "ctgry_cd", "item_cd", "vrty_cd", "grd_cd"):
            self.code(field)
        observation = IdentityObservation(
            product_class_code=self.text("se_cd", maximum=20),
            product_class_name=self.name("se_nm"),
            category_code=self.text("ctgry_cd", maximum=20),
            category_name=self.name("ctgry_nm"),
            item_code=self.text("item_cd", maximum=20),
            item_name=self.name("item_nm"),
            variety_code=self.text("vrty_cd", maximum=20),
            variety_name=self.name("vrty_nm"),
            grade_code=self.text("grd_cd", maximum=20),
            grade_name=self.name("grd_nm"),
            raw_unit=self.text("unit", maximum=30),
            raw_unit_size=self.text("unit_sz", maximum=30),
            coverage_identity=self._registry.identity_registry.coverage_identity,
        )
        self.positive_decimal("unit_sz")
        self._registry.identity_registry.validate(observation, row_index=self.row_index)
        return observation

    def region(self) -> RegionObservation:
        code = self.code("sgg_cd")
        name = self.name("sgg_nm")
        if self._registry.region_names.get(code) != name:
            raise KamisParseError("region_code_name_drift", row_index=self.row_index)
        return RegionObservation(code=code, name=name)

    def market(self, region: RegionObservation) -> MarketObservation:
        code = self.code("mrkt_cd")
        name = self.name("mrkt_nm")
        if self._registry.market_names.get((region.code, code)) != name:
            raise KamisParseError("market_code_name_drift", row_index=self.row_index)
        return MarketObservation(code=code, name=name)

    def text(self, field: str, *, maximum: int = 100) -> str:
        value = self.row[field]
        if not isinstance(value, str) or not is_bounded_source_text(value, maximum=maximum):
            raise KamisParseError("invalid_source_text", row_index=self.row_index, field=field)
        return value

    def name(self, field: str) -> str:
        value = self.text(field, maximum=100)
        if _CODE.fullmatch(value) is not None:
            raise KamisParseError("invalid_source_name", row_index=self.row_index, field=field)
        return value

    def code(self, field: str) -> str:
        value = self.row[field]
        if not isinstance(value, str) or _CODE.fullmatch(value) is None:
            raise KamisParseError("invalid_source_code", row_index=self.row_index, field=field)
        return value

    def day(self, field: str = "exmn_ymd") -> date:
        value = self.row[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
            raise KamisParseError("invalid_source_date", row_index=self.row_index, field=field)
        try:
            return datetime.strptime(value, "%Y%m%d").date()
        except ValueError:
            raise KamisParseError(
                "invalid_source_date", row_index=self.row_index, field=field
            ) from None

    def positive_decimal(self, field: str) -> Decimal:
        value = self._decimal(field)
        if value <= 0:
            raise KamisParseError("invalid_positive_decimal", row_index=self.row_index, field=field)
        return value

    def positive_price(self, field: str) -> Decimal:
        value = self.positive_decimal(field)
        source_text = self.row[field]
        fraction = source_text.partition(".")[2]
        if len(fraction) > 2 or value > _MAX_PRICE:
            raise KamisParseError("invalid_price_precision", row_index=self.row_index, field=field)
        return value

    def nonnegative_decimal(self, field: str) -> Decimal:
        value = self._decimal(field)
        if value < 0:
            raise KamisParseError(
                "invalid_nonnegative_decimal", row_index=self.row_index, field=field
            )
        return value

    def _decimal(self, field: str) -> Decimal:
        value = self.row[field]
        if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
            raise KamisParseError("invalid_decimal", row_index=self.row_index, field=field)
        return Decimal(value)


def require_items(items: object) -> Sequence[object]:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        raise KamisParseError("items_not_array")
    return items


def require_price_range(
    low: Decimal,
    average: Decimal,
    high: Decimal,
    *,
    row_index: int,
    field: str,
) -> None:
    if not low <= average <= high:
        raise KamisParseError("invalid_price_range", row_index=row_index, field=field)


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def identity_data(value: IdentityObservation) -> dict[str, str]:
    return {
        "category_code": value.category_code,
        "category_name": value.category_name,
        "coverage_identity": value.coverage_identity,
        "grade_code": value.grade_code,
        "grade_name": value.grade_name,
        "item_code": value.item_code,
        "item_name": value.item_name,
        "product_class_code": value.product_class_code,
        "product_class_name": value.product_class_name,
        "raw_unit": value.raw_unit,
        "raw_unit_size": value.raw_unit_size,
        "variety_code": value.variety_code,
        "variety_name": value.variety_name,
    }


def canonical_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
