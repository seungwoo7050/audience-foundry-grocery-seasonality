import uuid
from decimal import Decimal

import psycopg
import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

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
from grocery.models import ParseRun, SourceConfiguration
from grocery.tests.historical_test_support import create_scoped_artifact
from grocery.tests.test_acquisition_models import create_source_configuration
from grocery.tests.test_price_series_key_models import create_series


def _monthly_collection(source: SourceConfiguration, scope: str) -> HistoricalSourceCollection:
    return HistoricalSourceCollection.objects.create(
        kind=HistoricalSourceCollection.Kind.MONTHLY,
        source_configuration=source,
        code_manifest_sha256="a" * 64,
        partition_manifest_sha256=scope,
        expected_part_count=1,
        month_min="202501",
        month_max="202512",
    )


def _parse(source: SourceConfiguration, scope: str, suffix: str) -> ParseRun:
    now = timezone.now()
    return ParseRun.objects.create(
        artifact=create_scoped_artifact(source, scope),
        parser_revision=f"monthly-{suffix}",
        configuration_hash=suffix * 64,
        result_hash=scope,
        status=ParseRun.Status.VALIDATED,
        started_at=now,
        completed_at=now,
        total_row_count=1,
        accepted_row_count=1,
    )


def test_database_binds_part_scope_and_fact_membership_then_freezes_rows(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    first_scope = "b" * 64
    first_collection = _monthly_collection(source, first_scope)
    first_parse = _parse(source, first_scope, "c")

    with pytest.raises(DatabaseError), transaction.atomic():
        HistoricalSourceCollectionPart.objects.bulk_create(
            [
                HistoricalSourceCollectionPart(
                    collection=first_collection,
                    ordinal=1,
                    partition_scope_sha256="d" * 64,
                    parse_run=first_parse,
                    fact_count=1,
                )
            ]
        )

    first_part = HistoricalSourceCollectionPart.objects.create(
        collection=first_collection,
        ordinal=1,
        partition_scope_sha256=first_scope,
        parse_run=first_parse,
        fact_count=1,
    )
    second_scope = "e" * 64
    second_collection = _monthly_collection(source, second_scope)
    second_part = HistoricalSourceCollectionPart.objects.create(
        collection=second_collection,
        ordinal=1,
        partition_scope_sha256=second_scope,
        parse_run=_parse(source, second_scope, "f"),
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
        region_code="1101",
        region_name="서울",
        identity_evidence_revision="codes-v1",
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        MonthlyRegionalRetailPrice.objects.bulk_create(
            [
                MonthlyRegionalRetailPrice(
                    collection=first_collection,
                    collection_part=second_part,
                    series=series,
                    region=region,
                    year_month="202512",
                    provider_mean=Decimal("1200"),
                    provider_low=Decimal("1000"),
                    provider_high=Decimal("1500"),
                    source_row_sha256="1" * 64,
                    source_contract_revision="15156060-v1",
                )
            ]
        )

    fact = MonthlyRegionalRetailPrice.objects.create(
        collection=first_collection,
        collection_part=first_part,
        series=series,
        region=region,
        year_month="202512",
        provider_mean=Decimal("1200"),
        provider_low=Decimal("1000"),
        provider_high=Decimal("1500"),
        source_row_sha256="2" * 64,
        source_contract_revision="15156060-v1",
    )
    first_collection.state = HistoricalSourceCollection.State.VALIDATED
    first_collection.accepted_row_count = 1
    first_collection.result_sha256 = "3" * 64
    first_collection.completed_at = timezone.now()
    first_collection.save()

    with pytest.raises(DatabaseError), transaction.atomic():
        MonthlyRegionalRetailPrice.objects.filter(pk=fact.pk).update(
            provider_mean=Decimal("1300")
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        HistoricalSourceCollectionPart.objects.filter(pk=first_part.pk).delete()


def test_collection_completion_serializes_against_part_insert(transactional_db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    scope = "9" * 64
    collection = _monthly_collection(source, scope)
    parse_run = _parse(source, scope, "8")
    connection_params = connection.get_connection_params()

    with (
        psycopg.connect(**connection_params) as completing,
        psycopg.connect(**connection_params) as appending,
    ):
        completing.execute(
            "UPDATE grocery_historicalsourcecollection "
            "SET state = 'VALIDATED', completed_at = now(), result_sha256 = %s "
            "WHERE id = %s",
            ("7" * 64, collection.id),
        )
        appending.execute("SET LOCAL lock_timeout = '100ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            appending.execute(
                "INSERT INTO grocery_historicalsourcecollectionpart "
                "(id, ordinal, partition_scope_sha256, fact_count, collection_id, parse_run_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), 1, scope, 1, collection.id, parse_run.id),
            )
        appending.rollback()
        completing.rollback()
