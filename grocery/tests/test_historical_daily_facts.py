import hashlib
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailMarketKey,
    RetailRegionKey,
    price_series_identity_sha256,
)
from grocery.models import ParseRun, SourceConfiguration
from grocery.tests.historical_test_support import create_scoped_artifact
from grocery.tests.test_acquisition_models import create_source_configuration
from grocery.tests.test_price_series_key_models import create_series


def _part(kind: str, parser_revision: str) -> HistoricalSourceCollectionPart:
    completed_at = timezone.now()
    source = create_source_configuration(
        dataset_id=(
            "15156062" if kind == HistoricalSourceCollection.Kind.REGIONAL_DAILY else "15156065"
        ),
        publication_mode=(
            SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL
            if kind == HistoricalSourceCollection.Kind.REGIONAL_DAILY
            else SourceConfiguration.PublicationMode.HISTORICAL_MARKET
        ),
    )
    scope = hashlib.sha256(parser_revision.encode()).hexdigest()
    parse_run = ParseRun.objects.create(
        artifact=create_scoped_artifact(source, scope),
        parser_revision=parser_revision,
        configuration_hash=hashlib.sha256(f"config:{parser_revision}".encode()).hexdigest(),
        result_hash=hashlib.sha256(f"result:{parser_revision}".encode()).hexdigest(),
        status=ParseRun.Status.VALIDATED,
        started_at=completed_at,
        completed_at=completed_at,
        total_row_count=1,
        accepted_row_count=1,
    )
    collection = HistoricalSourceCollection.objects.create(
        kind=kind,
        source_configuration=source,
        code_manifest_sha256="a" * 64,
        partition_manifest_sha256="b" * 64,
        expected_part_count=1,
        date_min=date(2025, 12, 1),
        date_max=date(2025, 12, 31),
    )
    return HistoricalSourceCollectionPart.objects.create(
        collection=collection,
        ordinal=1,
        partition_scope_sha256=scope,
        parse_run=parse_run,
        fact_count=1,
    )


def test_daily_sources_remain_distinct_and_reject_market_region_drift(db: None) -> None:
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
    market = RetailMarketKey.objects.create(
        region=region,
        market_code="0110253",
        market_name="양곡도매",
        identity_evidence_revision="codes-v1",
    )
    regional_part = _part(HistoricalSourceCollection.Kind.REGIONAL_DAILY, "regional-v1")
    market_part = _part(HistoricalSourceCollection.Kind.MARKET_DAILY, "market-v1")
    regional = DailyRegionalRetailPrice.objects.create(
        collection=regional_part.collection,
        collection_part=regional_part,
        series=series,
        region=region,
        survey_date=date(2025, 12, 4),
        provider_mean=Decimal("1300"),
        provider_low=Decimal("1100"),
        provider_high=Decimal("1600"),
        source_row_sha256="d" * 64,
        source_contract_revision="15156062-v1",
    )
    observed = DailyMarketRetailPrice.objects.create(
        collection=market_part.collection,
        collection_part=market_part,
        series=series,
        region=region,
        market=market,
        survey_date=date(2025, 12, 4),
        provider_price=Decimal("1250"),
        source_row_sha256="e" * 64,
        source_contract_revision="15156065-v1",
    )

    assert regional.provider_mean == Decimal("1300")
    assert observed.provider_price == Decimal("1250")
    observed.provider_price = Decimal("1")
    with pytest.raises(ValidationError, match="immutable"):
        observed.save()

    other_region = RetailRegionKey.objects.create(
        region_code="2100", region_name="부산", identity_evidence_revision="codes-v1"
    )
    market.region_id = other_region.id
    with pytest.raises(ValidationError, match="region"):
        DailyMarketRetailPrice(
            collection=market_part.collection,
            collection_part=market_part,
            series=series,
            region=region,
            market=market,
            survey_date=date(2025, 12, 5),
            provider_price=Decimal("1250"),
            source_row_sha256="f" * 64,
            source_contract_revision="15156065-v1",
        ).full_clean()
