"""Source-specific, fail-closed parsers."""

from grocery.source.historical_dimensions import HistoricalDimensionRegistry, YearMonth
from grocery.source.kamis import (
    KAMIS_RETAIL_COVERAGE_IDENTITY,
    ExactIdentityRegistry,
    IdentityContractEvidence,
    KamisParseError,
    ParsedRecentPriceResult,
    ParsedRetailPriceRow,
    build_identity_registry_from_reviewed_evidence,
    parse_recent_price_rows,
)
from grocery.source.market_history import (
    KAMIS_MARKET_PRICE_FIELDS,
    ParsedMarketPriceRow,
    parse_market_price_rows,
)
from grocery.source.monthly_history import (
    KAMIS_MONTHLY_PRICE_FIELDS,
    ParsedMonthlyPriceRow,
    parse_monthly_price_rows,
)
from grocery.source.regional_history import (
    KAMIS_REGIONAL_PRICE_FIELDS,
    ParsedRegionalPriceRow,
    parse_regional_price_rows,
)

__all__ = [
    "KAMIS_RETAIL_COVERAGE_IDENTITY",
    "ExactIdentityRegistry",
    "HistoricalDimensionRegistry",
    "IdentityContractEvidence",
    "KAMIS_MONTHLY_PRICE_FIELDS",
    "KAMIS_MARKET_PRICE_FIELDS",
    "KAMIS_REGIONAL_PRICE_FIELDS",
    "KamisParseError",
    "ParsedMonthlyPriceRow",
    "ParsedMarketPriceRow",
    "ParsedRegionalPriceRow",
    "ParsedRecentPriceResult",
    "ParsedRetailPriceRow",
    "build_identity_registry_from_reviewed_evidence",
    "parse_monthly_price_rows",
    "parse_market_price_rows",
    "parse_recent_price_rows",
    "parse_regional_price_rows",
    "YearMonth",
]
