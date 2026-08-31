"""Strict typed parser for public-data API 15156065 market daily rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from grocery.source.historical_dimensions import (
    HistoricalDimensionRegistry,
    MarketObservation,
    RegionObservation,
)
from grocery.source.historical_parser import (
    HistoricalRowValidator,
    ParsedHistoricalResult,
    canonical_hash,
    decimal_text,
    identity_data,
    require_items,
)
from grocery.source.kamis import IdentityObservation, KamisParseError

KAMIS_MARKET_PRICE_FIELDS = frozenset(
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
        "mrkt_cd",
        "mrkt_nm",
        "exmn_dd_prc",
        "exmn_dd_cnvs_prc",
        "orgnl_reg_dt",
    }
)

type MarketSemanticKey = tuple[str, str, str, str, str, str, str, str, str, str, date]


@dataclass(frozen=True, slots=True)
class ParsedMarketPriceRow:
    identity: IdentityObservation
    region: RegionObservation
    market: MarketObservation
    source_effective_date: date
    raw_observed_price: Decimal
    converted_observed_price: Decimal
    source_recorded_at_raw: str
    source_row_hash: str

    @property
    def semantic_key(self) -> MarketSemanticKey:
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
            self.market.code,
            self.source_effective_date,
        )

    def canonical_data(self) -> dict[str, object]:
        return {
            "exmn_dd_cnvs_prc": decimal_text(self.converted_observed_price),
            "exmn_dd_prc": decimal_text(self.raw_observed_price),
            "identity": identity_data(self.identity),
            "market": {"code": self.market.code, "name": self.market.name},
            "region": {"code": self.region.code, "name": self.region.name},
            "source_effective_date": self.source_effective_date.isoformat(),
            "source_recorded_at_raw": self.source_recorded_at_raw,
            "source_row_hash": self.source_row_hash,
        }


def parse_market_price_rows(
    items: object,
    *,
    registry: HistoricalDimensionRegistry,
) -> ParsedHistoricalResult[ParsedMarketPriceRow]:
    """Parse exact 20-field 15156065 observations without inferring market type."""

    source_items = require_items(items)
    parsed_rows: list[ParsedMarketPriceRow] = []
    seen: set[MarketSemanticKey] = set()
    for row_index, raw_row in enumerate(source_items):
        validator = HistoricalRowValidator(
            raw_row,
            row_index=row_index,
            expected_fields=KAMIS_MARKET_PRICE_FIELDS,
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
                "parser_contract": "kamis-15156065-v1",
                "rows": [row.canonical_data() for row in ordered_rows],
            }
        ),
    )


def _parse_row(validator: HistoricalRowValidator) -> ParsedMarketPriceRow:
    region = validator.region()
    return ParsedMarketPriceRow(
        identity=validator.identity(),
        region=region,
        market=validator.market(region),
        source_effective_date=validator.day(),
        raw_observed_price=validator.positive_decimal("exmn_dd_prc"),
        converted_observed_price=validator.positive_decimal("exmn_dd_cnvs_prc"),
        source_recorded_at_raw=validator.text("orgnl_reg_dt", maximum=64),
        source_row_hash=canonical_hash(validator.row),
    )
