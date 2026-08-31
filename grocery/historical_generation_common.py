"""Shared exact-identity and configuration checks for historical persistence."""

from __future__ import annotations

import hashlib
import json

from django.core.exceptions import ValidationError

from grocery.historical_identity_models import HistoricalRetailSeriesKey, RetailRegionKey
from grocery.models import PriceSeriesKey
from grocery.source.historical_contract import HistoricalDataset
from grocery.source.historical_dimensions import RegionObservation
from grocery.source.kamis import IdentityObservation


def historical_configuration_sha256(
    *,
    dataset: HistoricalDataset,
    parser_revision: str,
    code_manifest_sha256: str,
) -> str:
    canonical = json.dumps(
        {
            "code_manifest_sha256": code_manifest_sha256,
            "dataset": dataset.value,
            "parser_revision": parser_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def resolve_historical_series(
    identity: IdentityObservation,
    *,
    code_manifest_sha256: str,
) -> HistoricalRetailSeriesKey:
    recent = PriceSeriesKey.objects.get(
        product_class_code=identity.product_class_code,
        category_code=identity.category_code,
        item_code=identity.item_code,
        variety_code=identity.variety_code,
        grade_code=identity.grade_code,
        raw_unit=identity.raw_unit,
        raw_unit_size=identity.raw_unit_size,
        coverage_identity=identity.coverage_identity,
    )
    if (
        recent.product_class_name != identity.product_class_name
        or recent.category_name != identity.category_name
        or recent.item_name != identity.item_name
        or recent.variety_name != identity.variety_name
        or recent.grade_name != identity.grade_name
    ):
        raise ValidationError("Historical row display identity drifted from its reviewed series.")
    return HistoricalRetailSeriesKey.objects.get(
        recent_series=recent,
        code_manifest_sha256=code_manifest_sha256,
    )


def resolve_historical_region(observation: RegionObservation) -> RetailRegionKey:
    region = RetailRegionKey.objects.get(region_code=observation.code)
    if region.region_name != observation.name:
        raise ValidationError("Historical row region name drifted from reviewed evidence.")
    return region
