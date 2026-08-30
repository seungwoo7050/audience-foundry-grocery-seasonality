"""Source-specific, fail-closed parsers."""

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

__all__ = [
    "KAMIS_RETAIL_COVERAGE_IDENTITY",
    "ExactIdentityRegistry",
    "IdentityContractEvidence",
    "KamisParseError",
    "ParsedRecentPriceResult",
    "ParsedRetailPriceRow",
    "build_identity_registry_from_reviewed_evidence",
    "parse_recent_price_rows",
]
