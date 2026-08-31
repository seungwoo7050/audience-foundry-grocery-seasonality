"""Strict typed parser for public-data API 15156060 monthly retail rows."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from grocery.source.historical_dimensions import (
    HistoricalDimensionRegistry,
    RegionObservation,
    YearMonth,
)
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

KAMIS_MONTHLY_PRICE_FIELDS = frozenset(
    {
        "exmn_ym",
        "sgg_cd",
        "sgg_nm",
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
        "unit",
        "unit_sz",
        "pmm_avgprc",
        "pmm_hgprc",
        "pmm_lwprc",
        "pmm_stddvtn",
        "pmm_cfcntvrtn",
        "pmm_cfcntrng",
        "pyy_avgprc",
        "pyy_hgprc",
        "pyy_lwprc",
        "pyy_stddvtn",
        "pyy_cfcntvrtn",
        "pyy_cfcntrng",
        "orgnl_reg_dt",
    }
)

type MonthlySemanticKey = tuple[str, str, str, str, str, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class ParsedMonthlyPriceRow:
    identity: IdentityObservation
    region: RegionObservation
    source_effective_month: YearMonth
    pmm_avgprc: Decimal
    pmm_hgprc: Decimal
    pmm_lwprc: Decimal
    pmm_stddvtn: Decimal
    pmm_cfcntvrtn: Decimal
    pmm_cfcntrng: Decimal
    pyy_avgprc: Decimal
    pyy_hgprc: Decimal
    pyy_lwprc: Decimal
    pyy_stddvtn: Decimal
    pyy_cfcntvrtn: Decimal
    pyy_cfcntrng: Decimal
    source_recorded_at_raw: str
    source_row_hash: str

    @property
    def semantic_key(self) -> MonthlySemanticKey:
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
            self.source_effective_month.source_text(),
        )

    def canonical_data(self) -> dict[str, object]:
        return {
            "identity": identity_data(self.identity),
            "pmm_avgprc": decimal_text(self.pmm_avgprc),
            "pmm_cfcntvrtn": decimal_text(self.pmm_cfcntvrtn),
            "pmm_cfcntrng": decimal_text(self.pmm_cfcntrng),
            "pmm_hgprc": decimal_text(self.pmm_hgprc),
            "pmm_lwprc": decimal_text(self.pmm_lwprc),
            "pmm_stddvtn": decimal_text(self.pmm_stddvtn),
            "pyy_avgprc": decimal_text(self.pyy_avgprc),
            "pyy_cfcntvrtn": decimal_text(self.pyy_cfcntvrtn),
            "pyy_cfcntrng": decimal_text(self.pyy_cfcntrng),
            "pyy_hgprc": decimal_text(self.pyy_hgprc),
            "pyy_lwprc": decimal_text(self.pyy_lwprc),
            "pyy_stddvtn": decimal_text(self.pyy_stddvtn),
            "region": {"code": self.region.code, "name": self.region.name},
            "source_effective_month": self.source_effective_month.source_text(),
            "source_recorded_at_raw": self.source_recorded_at_raw,
            "source_row_hash": self.source_row_hash,
        }


def parse_monthly_price_rows(
    items: object,
    *,
    registry: HistoricalDimensionRegistry,
) -> ParsedHistoricalResult[ParsedMonthlyPriceRow]:
    """Parse only the exact 28-field 15156060 contract, deriving no facts."""

    source_items = require_items(items)
    parsed_rows: list[ParsedMonthlyPriceRow] = []
    seen: set[MonthlySemanticKey] = set()
    for row_index, raw_row in enumerate(source_items):
        validator = HistoricalRowValidator(
            raw_row,
            row_index=row_index,
            expected_fields=KAMIS_MONTHLY_PRICE_FIELDS,
            registry=registry,
        )
        parsed = _parse_row(validator)
        if parsed.semantic_key in seen:
            raise KamisParseError("duplicate_semantic_identity", row_index=row_index)
        seen.add(parsed.semantic_key)
        parsed_rows.append(parsed)

    ordered_rows = tuple(sorted(parsed_rows, key=lambda row: row.semantic_key))
    result_hash = canonical_hash(
        {
            "parser_contract": "kamis-15156060-v1",
            "rows": [row.canonical_data() for row in ordered_rows],
        }
    )
    return ParsedHistoricalResult(
        rows=ordered_rows,
        input_row_count=len(source_items),
        result_hash=result_hash,
    )


def _parse_row(validator: HistoricalRowValidator) -> ParsedMonthlyPriceRow:
    pmm_avgprc = validator.positive_price("pmm_avgprc")
    pmm_hgprc = validator.positive_price("pmm_hgprc")
    pmm_lwprc = validator.positive_price("pmm_lwprc")
    pyy_avgprc = validator.positive_price("pyy_avgprc")
    pyy_hgprc = validator.positive_price("pyy_hgprc")
    pyy_lwprc = validator.positive_price("pyy_lwprc")
    require_price_range(
        pmm_lwprc,
        pmm_avgprc,
        pmm_hgprc,
        row_index=validator.row_index,
        field="pmm_avgprc",
    )
    require_price_range(
        pyy_lwprc,
        pyy_avgprc,
        pyy_hgprc,
        row_index=validator.row_index,
        field="pyy_avgprc",
    )
    return ParsedMonthlyPriceRow(
        identity=validator.identity(),
        region=validator.region(),
        source_effective_month=YearMonth.from_source(
            validator.row["exmn_ym"], row_index=validator.row_index
        ),
        pmm_avgprc=pmm_avgprc,
        pmm_hgprc=pmm_hgprc,
        pmm_lwprc=pmm_lwprc,
        pmm_stddvtn=validator.nonnegative_decimal("pmm_stddvtn"),
        pmm_cfcntvrtn=validator.nonnegative_decimal("pmm_cfcntvrtn"),
        pmm_cfcntrng=validator.nonnegative_decimal("pmm_cfcntrng"),
        pyy_avgprc=pyy_avgprc,
        pyy_hgprc=pyy_hgprc,
        pyy_lwprc=pyy_lwprc,
        pyy_stddvtn=validator.nonnegative_decimal("pyy_stddvtn"),
        pyy_cfcntvrtn=validator.nonnegative_decimal("pyy_cfcntvrtn"),
        pyy_cfcntrng=validator.nonnegative_decimal("pyy_cfcntrng"),
        source_recorded_at_raw=validator.text("orgnl_reg_dt", maximum=64),
        source_row_hash=canonical_hash(validator.row),
    )
