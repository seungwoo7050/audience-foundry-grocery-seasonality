from django.utils import timezone

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.historical_collections import (
    complete_historical_collection,
    partition_manifest_sha256,
)
from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailRegionKey,
    price_series_identity_sha256,
)
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.models import FetchAttempt, ParseRun, SourceConfiguration, build_source_artifact
from grocery.tests.test_acquisition_models import (
    create_fetch_attempt,
    create_page_receipt,
    create_source_configuration,
)
from grocery.tests.test_price_series_key_models import create_series


def test_completion_reconciles_planned_partition_parse_and_typed_fact(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    scope = "e" * 64
    attempt = create_fetch_attempt(source, request_scope_sha256=scope)
    create_page_receipt(
        attempt,
        declared_total_count=1,
        received_row_count=1,
        body_byte_length=10,
        body_sha256="b" * 64,
    )
    completed_at = timezone.now()
    attempt.state = FetchAttempt.State.SUCCEEDED
    attempt.completed_at = completed_at
    attempt.received_page_count = 1
    attempt.received_row_count = 1
    attempt.received_byte_count = 10
    attempt.save()
    artifact, _created = build_source_artifact(attempt.id)
    parse_run = ParseRun.objects.create(
        artifact=artifact,
        parser_revision="historical-monthly-v1",
        configuration_hash="c" * 64,
        result_hash="d" * 64,
        status=ParseRun.Status.VALIDATED,
        started_at=completed_at,
        completed_at=completed_at,
        total_row_count=1,
        accepted_row_count=1,
    )
    collection = HistoricalSourceCollection.objects.create(
        kind=HistoricalSourceCollection.Kind.MONTHLY,
        source_configuration=source,
        code_manifest_sha256="a" * 64,
        partition_manifest_sha256=partition_manifest_sha256([scope]),
        expected_part_count=1,
        month_min="202512",
        month_max="202512",
    )
    part = HistoricalSourceCollectionPart.objects.create(
        collection=collection,
        ordinal=1,
        partition_scope_sha256=scope,
        parse_run=parse_run,
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
    MonthlyRegionalRetailPrice.objects.create(
        collection=collection,
        collection_part=part,
        series=series,
        region=region,
        year_month="202512",
        provider_mean=1200,
        provider_low=1000,
        provider_high=1500,
        source_row_sha256="f" * 64,
        source_contract_revision="15156060-v1",
    )

    completed = complete_historical_collection(collection.id)

    assert completed.state == HistoricalSourceCollection.State.VALIDATED
    assert completed.accepted_row_count == 1
    assert len(completed.result_sha256) == 64
