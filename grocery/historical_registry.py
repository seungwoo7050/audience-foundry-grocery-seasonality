"""Load the pre-reviewed identity dimensions used by historical source parsers."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailMarketKey,
    RetailRegionKey,
)
from grocery.source.historical_dimensions import HistoricalDimensionRegistry
from grocery.source.kamis import (
    IdentityObservation,
    KamisParseError,
    build_identity_registry_from_reviewed_evidence,
)
from grocery.source.registry import INITIAL_RETAIL_IDENTITY_REGISTRY


def load_historical_dimension_registry(
    code_manifest_sha256: str,
) -> HistoricalDimensionRegistry:
    series_keys = list(
        HistoricalRetailSeriesKey.objects.select_related("recent_series").order_by(
            "series_identity_sha256"
        )
    )
    if not series_keys or any(
        key.code_manifest_sha256 != code_manifest_sha256 for key in series_keys
    ):
        raise ValidationError("Historical series identities do not match the code manifest.")
    item_names: dict[tuple[str, str], str] = {}
    variety_names: dict[tuple[str, str, str], str] = {}
    grade_names: dict[tuple[str, str, str, str], str] = {}
    units: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    try:
        for key in series_keys:
            series = key.recent_series
            INITIAL_RETAIL_IDENTITY_REGISTRY.validate(
                IdentityObservation(
                    product_class_code=series.product_class_code,
                    product_class_name=series.product_class_name,
                    category_code=series.category_code,
                    category_name=series.category_name,
                    item_code=series.item_code,
                    item_name=series.item_name,
                    variety_code=series.variety_code,
                    variety_name=series.variety_name,
                    grade_code=series.grade_code,
                    grade_name=series.grade_name,
                    raw_unit=series.raw_unit,
                    raw_unit_size=series.raw_unit_size,
                    coverage_identity=series.coverage_identity,
                ),
                row_index=0,
            )
            item_names[(series.category_code, series.item_code)] = series.item_name
            variety_names[(series.category_code, series.item_code, series.variety_code)] = (
                series.variety_name
            )
            series_code_key = (
                series.category_code,
                series.item_code,
                series.variety_code,
                series.grade_code,
            )
            grade_names[series_code_key] = series.grade_name
            units[series_code_key] = (series.raw_unit, series.raw_unit_size)
    except KamisParseError:
        raise ValidationError(
            "Historical series identity drifted from reviewed evidence."
        ) from None

    regions = list(RetailRegionKey.objects.order_by("region_code"))
    markets = list(RetailMarketKey.objects.select_related("region").order_by("market_code"))
    if not regions:
        raise ValidationError("Historical ingestion requires reviewed region identities.")
    evidence_revisions = {
        *(region.identity_evidence_revision for region in regions),
        *(market.identity_evidence_revision for market in markets),
    }
    if len(evidence_revisions) != 1:
        raise ValidationError("Historical region and market evidence revisions must match.")
    historical_identity_registry = build_identity_registry_from_reviewed_evidence(
        item_names=item_names,
        variety_names=variety_names,
        grade_names=grade_names,
        units=units,
        evidence=INITIAL_RETAIL_IDENTITY_REGISTRY.evidence,
        coverage_identity=INITIAL_RETAIL_IDENTITY_REGISTRY.coverage_identity,
    )
    return HistoricalDimensionRegistry(
        identity_registry=historical_identity_registry,
        region_names={region.region_code: region.region_name for region in regions},
        market_names={
            (market.region.region_code, market.market_code): market.market_name
            for market in markets
        },
        dimension_evidence_revision=evidence_revisions.pop(),
    )
