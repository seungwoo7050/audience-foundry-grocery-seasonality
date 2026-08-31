import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
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
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.models import ParseRun, SourceArtifact, SourceConfiguration
from grocery.tests.test_acquisition_models import create_source_configuration
from grocery.tests.test_price_series_key_models import create_series


@dataclass(frozen=True)
class ReviewedHistoricalBundle:
    monthly_review: HistoricalCollectionReviewDecision
    regional_review: HistoricalCollectionReviewDecision
    market_review: HistoricalCollectionReviewDecision
    series: HistoricalRetailSeriesKey
    region: RetailRegionKey
    market: RetailMarketKey


def _year_month(number: int) -> str:
    year, month = divmod(number, 12)
    return f"{year:04d}{month + 1:02d}"


def _collection_part(
    *,
    kind: str,
    dataset_id: str,
    publication_mode: str,
    parser_revision: str,
    fact_count: int,
    month_min: str = "",
    month_max: str = "",
    date_min: date | None = None,
    date_max: date | None = None,
) -> HistoricalSourceCollectionPart:
    now = timezone.now()
    source = create_source_configuration(
        dataset_id=dataset_id,
        publication_mode=publication_mode,
    )
    digest = hashlib.sha256(parser_revision.encode("ascii")).hexdigest()
    artifact = SourceArtifact.objects.create(
        source_identity=f"fixture:{dataset_id}:{parser_revision}",
        ordered_manifest_sha256=digest,
        page_count=1,
        total_bytes=1,
        first_seen_at=now,
    )
    parse_run = ParseRun.objects.create(
        artifact=artifact,
        parser_revision=parser_revision,
        configuration_hash=digest,
        result_hash=digest,
        status=ParseRun.Status.VALIDATED,
        started_at=now,
        completed_at=now,
        total_row_count=fact_count,
        accepted_row_count=fact_count,
    )
    collection = HistoricalSourceCollection.objects.create(
        kind=kind,
        source_configuration=source,
        code_manifest_sha256="a" * 64,
        partition_manifest_sha256=digest,
        expected_part_count=1,
        month_min=month_min,
        month_max=month_max,
        date_min=date_min,
        date_max=date_max,
    )
    return HistoricalSourceCollectionPart.objects.create(
        collection=collection,
        ordinal=1,
        partition_scope_sha256=digest,
        parse_run=parse_run,
        fact_count=fact_count,
    )


def _complete(part: HistoricalSourceCollectionPart, fact_count: int) -> None:
    collection = part.collection
    collection.state = HistoricalSourceCollection.State.VALIDATED
    collection.accepted_row_count = fact_count
    collection.result_sha256 = hashlib.sha256(
        f"result:{collection.kind}".encode("ascii")
    ).hexdigest()
    collection.completed_at = timezone.now()
    collection.save()


def _approve(
    collection: HistoricalSourceCollection,
    reviewer: object,
) -> HistoricalCollectionReviewDecision:
    return HistoricalCollectionReviewDecision.objects.create(
        collection=collection,
        decision=HistoricalCollectionReviewDecision.Decision.APPROVE,
        reviewer=reviewer,
        reconciliation_report_sha256="d" * 64,
        acceptance_evidence_sha256="e" * 64,
        reason_code="RECONCILED",
        approved_result_sha256=collection.result_sha256,
        approved_partition_manifest_sha256=collection.partition_manifest_sha256,
    )


def create_reviewed_historical_bundle() -> ReviewedHistoricalBundle:
    recent = create_series(item_name="양배추", variety_name="양배추")
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
        market_name="양곡시장",
        identity_evidence_revision="codes-v1",
    )
    month_max_number = 2025 * 12 + 11
    month_min = _year_month(month_max_number - 35)
    month_max = _year_month(month_max_number)
    monthly_part = _collection_part(
        kind=HistoricalSourceCollection.Kind.MONTHLY,
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
        parser_revision="monthly-fixture-v1",
        fact_count=36,
        month_min=month_min,
        month_max=month_max,
    )
    regional_part = _collection_part(
        kind=HistoricalSourceCollection.Kind.REGIONAL_DAILY,
        dataset_id="15156062",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL,
        parser_revision="regional-fixture-v1",
        fact_count=1,
        date_min=date(2025, 12, 1),
        date_max=date(2025, 12, 31),
    )
    market_part = _collection_part(
        kind=HistoricalSourceCollection.Kind.MARKET_DAILY,
        dataset_id="15156065",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MARKET,
        parser_revision="market-fixture-v1",
        fact_count=1,
        date_min=date(2025, 12, 1),
        date_max=date(2025, 12, 31),
    )
    MonthlyRegionalRetailPrice.objects.bulk_create(
        [
            MonthlyRegionalRetailPrice(
                collection=monthly_part.collection,
                collection_part=monthly_part,
                series=series,
                region=region,
                year_month=_year_month(value),
                provider_mean=Decimal(1000 + value - month_max_number),
                provider_low=Decimal(900 + value - month_max_number),
                provider_high=Decimal(1100 + value - month_max_number),
                source_row_sha256=hashlib.sha256(f"monthly:{value}".encode()).hexdigest(),
                source_contract_revision="15156060-v1",
            )
            for value in range(month_max_number - 35, month_max_number + 1)
        ]
    )
    survey_date = date(2025, 12, 31)
    DailyRegionalRetailPrice.objects.create(
        collection=regional_part.collection,
        collection_part=regional_part,
        series=series,
        region=region,
        survey_date=survey_date,
        provider_mean=1000,
        provider_low=900,
        provider_high=1100,
        source_row_sha256="f" * 64,
        source_contract_revision="15156062-v1",
    )
    DailyMarketRetailPrice.objects.create(
        collection=market_part.collection,
        collection_part=market_part,
        series=series,
        region=region,
        market=market,
        survey_date=survey_date,
        provider_price=1000,
        source_row_sha256="1" * 64,
        source_contract_revision="15156065-v1",
    )
    for part, count in ((monthly_part, 36), (regional_part, 1), (market_part, 1)):
        _complete(part, count)
    reviewer = get_user_model().objects.create_user(username="bundle-reviewer")
    return ReviewedHistoricalBundle(
        monthly_review=_approve(monthly_part.collection, reviewer),
        regional_review=_approve(regional_part.collection, reviewer),
        market_review=_approve(market_part.collection, reviewer),
        series=series,
        region=region,
        market=market,
    )
