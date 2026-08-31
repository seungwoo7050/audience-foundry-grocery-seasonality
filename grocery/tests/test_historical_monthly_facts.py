from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailRegionKey,
    price_series_identity_sha256,
)
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.models import SourceConfiguration
from grocery.tests.test_acquisition_models import create_source_configuration
from grocery.tests.test_historical_collections import _validated_parse_run
from grocery.tests.test_price_series_key_models import create_series


def test_monthly_fact_preserves_provider_range_and_is_immutable(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    collection = HistoricalSourceCollection.objects.create(
        kind=HistoricalSourceCollection.Kind.MONTHLY,
        source_configuration=source,
        code_manifest_sha256="a" * 64,
        partition_manifest_sha256="b" * 64,
        expected_part_count=1,
        month_min="202512",
        month_max="202512",
    )
    scope = "c" * 64
    part = HistoricalSourceCollectionPart.objects.create(
        collection=collection,
        ordinal=1,
        partition_scope_sha256=scope,
        parse_run=_validated_parse_run(source, scope),
        fact_count=1,
    )
    recent = create_series()
    series = HistoricalRetailSeriesKey.objects.create(
        recent_series=recent,
        series_identity_sha256=price_series_identity_sha256(recent),
        cross_source_evidence_revision="cross-v1",
        code_manifest_sha256="a" * 64,
    )
    region = RetailRegionKey.objects.create(
        region_code="1101", region_name="서울", identity_evidence_revision="codes-v1"
    )
    fact = MonthlyRegionalRetailPrice.objects.create(
        collection=collection,
        collection_part=part,
        series=series,
        region=region,
        year_month="202512",
        provider_mean=Decimal("1200.50"),
        provider_low=Decimal("1000.25"),
        provider_high=Decimal("1500.75"),
        source_row_sha256="d" * 64,
        source_contract_revision="15156060-v1",
    )

    fact.refresh_from_db()
    assert fact.provider_mean == Decimal("1200.50")
    fact.provider_mean = Decimal("1300")
    with pytest.raises(ValidationError, match="immutable"):
        fact.save()

    with pytest.raises(ValidationError):
        MonthlyRegionalRetailPrice.objects.create(
            collection=collection,
            collection_part=part,
            series=series,
            region=region,
            year_month="202511",
            provider_mean=Decimal("900"),
            provider_low=Decimal("1000"),
            provider_high=Decimal("1500"),
            source_row_sha256="e" * 64,
            source_contract_revision="15156060-v1",
        )
