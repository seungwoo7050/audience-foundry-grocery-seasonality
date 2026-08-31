"""Strict typed parser for public-data API 15156062 regional daily rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from grocery.source.historical_dimensions import HistoricalDimensionRegistry, RegionObservation
from grocery.source.historical_parser import (
    HistoricalRowValidator,
    ParsedHistoricalResult,
    canonical_hash,
    decimal_text,
    identity_data,
    require_items,
    require_price_range,
)
from grocery.source.kamis import IdentityObservation, KamisParseError

KAMIS_REGIONAL_PRICE_FIELDS = frozenset(
    {
        "exmn_ymd",
        "se_cd",
        "se_nm",
        "ctgry_cd",
        "ctgry_nm",
        "item_cd",
        "item_nm",
        "vrty_cd",
        "vrty_nm",
        "grd_cd",
        "grd_nm",
        "sgg_cd",
        "sgg_nm",
        "unit",
        "unit_sz",
        "exmn_dd_min_prc",
        "exmn_dd_cnvs_min_prc",
        "exmn_dd_avg_prc",
        "exmn_dd_cnvs_avg_prc",
        "exmn_dd_max_prc",
        "exmn_dd_cnvs_max_prc",
    }
)

type RegionalSemanticKey = tuple[str, str, str, str, str, str, str, str, str, date]


@dataclass(frozen=True, slots=True)
class ParsedRegionalPriceRow:
    identity: IdentityObservation
    region: RegionObservation
    source_effective_date: date
    raw_min_price: Decimal
    raw_average_price: Decimal
    raw_max_price: Decimal
    converted_min_price: Decimal
    converted_average_price: Decimal
    converted_max_price: Decimal
    source_row_hash: str

    @property
    def semantic_key(self) -> RegionalSemanticKey:
        return (
            self.identity.product_class_code,
            self.identity.category_code,
            self.identity.item_code,
            self.identity.variety_code,
            self.identity.grade_code,
            self.identity.raw_unit,
            self.identity.raw_unit_size,
            self.identity.coverage_identity,
            self.region.code,
            self.source_effective_date,
        )

    def canonical_data(self) -> dict[str, object]:
        return {
            "exmn_dd_avg_prc": decimal_text(self.raw_average_price),
            "exmn_dd_cnvs_avg_prc": decimal_text(self.converted_average_price),
            "exmn_dd_cnvs_max_prc": decimal_text(self.converted_max_price),
            "exmn_dd_cnvs_min_prc": decimal_text(self.converted_min_price),
            "exmn_dd_max_prc": decimal_text(self.raw_max_price),
            "exmn_dd_min_prc": decimal_text(self.raw_min_price),
            "identity": identity_data(self.identity),
            "region": {"code": self.region.code, "name": self.region.name},
            "source_effective_date": self.source_effective_date.isoformat(),
            "source_row_hash": self.source_row_hash,
        }


def parse_regional_price_rows(
    items: object,
    *,
    registry: HistoricalDimensionRegistry,
) -> ParsedHistoricalResult[ParsedRegionalPriceRow]:
    """Parse only exact 21-field 15156062 rows without reconstructing an average."""

    source_items = require_items(items)
    parsed_rows: list[ParsedRegionalPriceRow] = []
    seen: set[RegionalSemanticKey] = set()
    for row_index, raw_row in enumerate(source_items):
        validator = HistoricalRowValidator(
            raw_row,
            row_index=row_index,
            expected_fields=KAMIS_REGIONAL_PRICE_FIELDS,
            registry=registry,
        )
        parsed = _parse_row(validator)
        if parsed.semantic_key in seen:
            raise KamisParseError("duplicate_semantic_identity", row_index=row_index)
        seen.add(parsed.semantic_key)
        parsed_rows.append(parsed)

    ordered_rows = tuple(sorted(parsed_rows, key=lambda row: row.semantic_key))
    return ParsedHistoricalResult(
        rows=ordered_rows,
        input_row_count=len(source_items),
        result_hash=canonical_hash(
            {
                "parser_contract": "kamis-15156062-v1",
                "rows": [row.canonical_data() for row in ordered_rows],
            }
        ),
    )


def _parse_row(validator: HistoricalRowValidator) -> ParsedRegionalPriceRow:
    raw_min = validator.positive_price("exmn_dd_min_prc")
    raw_average = validator.positive_price("exmn_dd_avg_prc")
    raw_max = validator.positive_price("exmn_dd_max_prc")
    converted_min = validator.positive_price("exmn_dd_cnvs_min_prc")
    converted_average = validator.positive_price("exmn_dd_cnvs_avg_prc")
    converted_max = validator.positive_price("exmn_dd_cnvs_max_prc")
    require_price_range(
        raw_min,
        raw_average,
        raw_max,
        row_index=validator.row_index,
        field="exmn_dd_avg_prc",
    )
    require_price_range(
        converted_min,
        converted_average,
        converted_max,
        row_index=validator.row_index,
        field="exmn_dd_cnvs_avg_prc",
    )
    return ParsedRegionalPriceRow(
        identity=validator.identity(),
        region=validator.region(),
        source_effective_date=validator.day(),
        raw_min_price=raw_min,
        raw_average_price=raw_average,
        raw_max_price=raw_max,
        converted_min_price=converted_min,
        converted_average_price=converted_average,
        converted_max_price=converted_max,
        source_row_hash=canonical_hash(validator.row),
    )
