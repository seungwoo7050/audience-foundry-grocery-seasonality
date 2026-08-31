import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.models import ParseRun, SourceConfiguration
from grocery.tests.historical_test_support import create_scoped_artifact
from grocery.tests.test_acquisition_models import create_source_configuration


def _validated_parse_run(
    source: SourceConfiguration,
    scope_sha256: str,
) -> ParseRun:
    completed_at = timezone.now()
    return ParseRun.objects.create(
        artifact=create_scoped_artifact(source, scope_sha256),
        parser_revision="historical-monthly-v1",
        configuration_hash="c" * 64,
        result_hash="d" * 64,
        status=ParseRun.Status.VALIDATED,
        started_at=completed_at,
        completed_at=completed_at,
        total_row_count=1,
        accepted_row_count=1,
    )


def test_collection_part_is_complete_then_terminally_immutable(db: None) -> None:
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
        month_min="202301",
        month_max="202512",
    )
    scope = "e" * 64
    part = HistoricalSourceCollectionPart.objects.create(
        collection=collection,
        ordinal=1,
        partition_scope_sha256=scope,
        parse_run=_validated_parse_run(source, scope),
        fact_count=1,
    )

    assert part.collection_id == collection.id
    collection.state = HistoricalSourceCollection.State.VALIDATED
    collection.completed_at = timezone.now()
    collection.accepted_row_count = 1
    collection.result_sha256 = "f" * 64
    collection.save()

    collection.accepted_row_count = 2
    with pytest.raises(ValidationError, match="immutable"):
        collection.save()


def test_collection_window_kind_fails_closed(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156062",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL,
    )

    with pytest.raises(ValidationError):
        HistoricalSourceCollection.objects.create(
            kind=HistoricalSourceCollection.Kind.REGIONAL_DAILY,
            source_configuration=source,
            code_manifest_sha256="a" * 64,
            partition_manifest_sha256="b" * 64,
            expected_part_count=1,
            month_min="202301",
            month_max="202512",
        )
