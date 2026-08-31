"""Disposable DEBUG+QA database builder for vNext browser evidence."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection, transaction

from grocery.historical_activation_models import (
    HistoricalRetailPublicationActivation,
    HistoricalRetailPublicationChannel,
)
from grocery.historical_activations import transition_historical_publication
from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_collections import complete_historical_collection
from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailMarketKey,
    RetailRegionKey,
    price_series_identity_sha256,
)
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.historical_publications import seal_historical_publication
from grocery.models import (
    PriceSeriesKey,
    PublicationActivation,
    PublicationChannel,
    PublicationRevision,
    SourceArtifact,
    SourceConfiguration,
    seal_recent_publication,
    transition_recent_publication,
)
from grocery.tests.historical_bundle_factory import (
    _approve,
    _collection_part,
    _year_month,
)
from grocery.tests.test_publication_revision_models import create_approved_generation


@dataclass(frozen=True, slots=True)
class VnextBrowserFixture:
    recent_revision_id: uuid.UUID
    historical_revision_id: uuid.UUID
    series_count: int
    region_count: int
    market_count: int
    monthly_fact_count: int


def _require_disposable_qa() -> None:
    if (
        settings.DEBUG is not True
        or getattr(settings, "ADMIN_ENABLED", None) is not False
        or getattr(settings, "QA_STATE_PREVIEWS_ENABLED", None) is not True
    ):
        raise RuntimeError("qa_fixture_environment_denied")
    database = connection.settings_dict
    name = database.get("NAME")
    host = database.get("HOST")
    if (
        database.get("ENGINE") != "django.db.backends.postgresql"
        or host not in {"127.0.0.1", "localhost", "::1"}
        or not isinstance(name, str)
        or not name.startswith("grocery_vnext_")
    ):
        raise RuntimeError("qa_fixture_database_denied")
    root_models = (
        SourceConfiguration,
        SourceArtifact,
        PriceSeriesKey,
        PublicationRevision,
        PublicationChannel,
        HistoricalRetailSeriesKey,
        RetailRegionKey,
        RetailMarketKey,
        HistoricalSourceCollection,
        HistoricalRetailPublicationRevision,
        HistoricalRetailPublicationChannel,
    )
    if any(model.objects.exists() for model in root_models):
        raise RuntimeError("qa_fixture_database_not_empty")


def _permission(codename: str) -> Permission:
    return Permission.objects.get(content_type__app_label="grocery", codename=codename)


@transaction.atomic
def build_vnext_browser_fixture() -> VnextBrowserFixture:
    _require_disposable_qa()
    recent_decision, snapshots, _recent_reviewer = create_approved_generation(snapshot_count=5)
    publisher = get_user_model().objects.create_user(username="qa-vnext-publisher")
    publisher.user_permissions.add(
        _permission("publish_publication"),
        _permission("publish_historical_publication"),
    )
    recent_revision = seal_recent_publication(recent_decision.id, "ko-v4")
    transition_recent_publication(
        operation_id=uuid.uuid4(),
        actor=publisher,
        operation=PublicationActivation.Operation.ACTIVATE,
        target_revision_id=recent_revision.id,
        expected_current_revision_id=None,
        expected_version=0,
        reason_code="QA_VNEXT_RECENT_ACTIVATED",
        acceptance_evidence_sha256="a" * 64,
    )

    manifest = "b" * 64
    historical_series = tuple(
        HistoricalRetailSeriesKey.objects.create(
            recent_series=snapshot.series,
            series_identity_sha256=price_series_identity_sha256(snapshot.series),
            cross_source_evidence_revision="qa-vnext-v1",
            code_manifest_sha256=manifest,
        )
        for snapshot in snapshots
    )
    regions = (
        RetailRegionKey.objects.create(
            region_code="1101",
            region_name="서울",
            identity_evidence_revision="qa-vnext-v1",
        ),
        RetailRegionKey.objects.create(
            region_code="2100",
            region_name="부산",
            identity_evidence_revision="qa-vnext-v1",
        ),
    )
    markets = tuple(
        RetailMarketKey.objects.create(
            region=regions[0],
            market_code=f"{index + 1:07d}",
            market_name=f"조사 시장 {index + 1:02d}",
            identity_evidence_revision="qa-vnext-v1",
        )
        for index in range(31)
    )
    month_max_number = 2026 * 12 + 7
    month_min = _year_month(month_max_number - 35)
    month_max = _year_month(month_max_number)
    monthly_count = len(historical_series) * len(regions) * 36
    regional_count = len(historical_series) * len(regions)
    market_count = len(historical_series) * len(markets)
    monthly_part = _collection_part(
        kind=HistoricalSourceCollection.Kind.MONTHLY,
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
        parser_revision="qa-monthly-v1",
        fact_count=monthly_count,
        code_manifest_sha256=manifest,
        month_min=month_min,
        month_max=month_max,
    )
    regional_part = _collection_part(
        kind=HistoricalSourceCollection.Kind.REGIONAL_DAILY,
        dataset_id="15156062",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL,
        parser_revision="qa-regional-v1",
        fact_count=regional_count,
        code_manifest_sha256=manifest,
        date_min=date(2026, 8, 1),
        date_max=date(2026, 8, 31),
    )
    market_part = _collection_part(
        kind=HistoricalSourceCollection.Kind.MARKET_DAILY,
        dataset_id="15156065",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MARKET,
        parser_revision="qa-market-v1",
        fact_count=market_count,
        code_manifest_sha256=manifest,
        date_min=date(2026, 8, 1),
        date_max=date(2026, 8, 31),
    )

    MonthlyRegionalRetailPrice.objects.bulk_create(
        [
            MonthlyRegionalRetailPrice(
                collection=monthly_part.collection,
                collection_part=monthly_part,
                series=series,
                region=region,
                year_month=_year_month(month_number),
                provider_mean=Decimal(1000 + series_index * 90 + region_index * 40 + offset),
                provider_low=Decimal(900 + series_index * 90 + region_index * 40 + offset),
                provider_high=Decimal(1100 + series_index * 90 + region_index * 40 + offset),
                source_row_sha256=hashlib.sha256(
                    f"qa-month:{series.pk}:{region.id}:{month_number}".encode("ascii")
                ).hexdigest(),
                source_contract_revision="qa-15156060-v1",
            )
            for series_index, series in enumerate(historical_series)
            for region_index, region in enumerate(regions)
            for offset, month_number in enumerate(
                range(month_max_number - 35, month_max_number + 1)
            )
        ]
    )
    survey_date = date(2026, 8, 29)
    DailyRegionalRetailPrice.objects.bulk_create(
        [
            DailyRegionalRetailPrice(
                collection=regional_part.collection,
                collection_part=regional_part,
                series=series,
                region=region,
                survey_date=survey_date,
                provider_mean=Decimal(1200 + series_index * 100 + region_index * 50),
                provider_low=Decimal(1050 + series_index * 100 + region_index * 50),
                provider_high=Decimal(1350 + series_index * 100 + region_index * 50),
                source_row_sha256=hashlib.sha256(
                    f"qa-region:{series.pk}:{region.id}".encode("ascii")
                ).hexdigest(),
                source_contract_revision="qa-15156062-v1",
            )
            for series_index, series in enumerate(historical_series)
            for region_index, region in enumerate(regions)
        ]
    )
    DailyMarketRetailPrice.objects.bulk_create(
        [
            DailyMarketRetailPrice(
                collection=market_part.collection,
                collection_part=market_part,
                series=series,
                region=market.region,
                market=market,
                survey_date=survey_date,
                provider_price=Decimal(1100 + series_index * 100 + market_index * 3),
                source_row_sha256=hashlib.sha256(
                    f"qa-market:{series.pk}:{market.id}".encode("ascii")
                ).hexdigest(),
                source_contract_revision="qa-15156065-v1",
            )
            for series_index, series in enumerate(historical_series)
            for market_index, market in enumerate(markets)
        ]
    )
    for part in (monthly_part, regional_part, market_part):
        complete_historical_collection(part.collection_id)
        part.collection.refresh_from_db()

    reviewer = get_user_model().objects.create_user(username="qa-vnext-history-reviewer")
    reviewer.user_permissions.add(_permission("review_historical_collection"))
    monthly_review = _approve(monthly_part.collection, reviewer)
    regional_review = _approve(regional_part.collection, reviewer)
    market_review = _approve(market_part.collection, reviewer)
    historical_revision = seal_historical_publication(
        monthly_review_id=monthly_review.id,
        regional_review_id=regional_review.id,
        market_review_id=market_review.id,
        compatibility_report_sha256="c" * 64,
    )
    transition_historical_publication(
        operation_id=uuid.uuid4(),
        actor=publisher,
        operation=HistoricalRetailPublicationActivation.Operation.ACTIVATE,
        target_revision_id=historical_revision.id,
        expected_current_revision_id=None,
        expected_version=0,
        reason_code="QA_VNEXT_HISTORY_ACTIVATED",
        acceptance_evidence_sha256="d" * 64,
    )
    return VnextBrowserFixture(
        recent_revision_id=recent_revision.id,
        historical_revision_id=historical_revision.id,
        series_count=len(historical_series),
        region_count=len(regions),
        market_count=len(markets),
        monthly_fact_count=monthly_count,
    )
