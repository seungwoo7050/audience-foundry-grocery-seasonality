import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.models import FetchAttempt, SourceConfiguration, build_source_artifact
from grocery.tests.test_acquisition_models import (
    create_fetch_attempt,
    create_page_receipt,
    create_source_configuration,
)


def _completed_attempt(source: SourceConfiguration, scope_hash: str) -> FetchAttempt:
    attempt = create_fetch_attempt(source, request_scope_sha256=scope_hash)
    create_page_receipt(
        attempt,
        declared_total_count=1,
        received_row_count=1,
        body_byte_length=10,
        body_sha256="b" * 64,
    )
    attempt.state = FetchAttempt.State.SUCCEEDED
    attempt.completed_at = timezone.now()
    attempt.received_page_count = 1
    attempt.received_row_count = 1
    attempt.received_byte_count = 10
    attempt.save()
    return attempt


def test_historical_source_mode_allows_weekly_schedule(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
        schedule_interval_hours=168,
    )

    assert source.schedule_interval_hours == 168


def test_historical_dataset_and_mode_are_an_exact_pair(db: None) -> None:
    with pytest.raises(ValidationError):
        create_source_configuration(
            dataset_id="15156060",
            publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MARKET,
        )


def test_partition_scope_separates_identical_page_manifests(db: None) -> None:
    source = create_source_configuration()
    first, first_created = build_source_artifact(_completed_attempt(source, "a" * 64).id)
    second, second_created = build_source_artifact(_completed_attempt(source, "c" * 64).id)

    assert first_created is True
    assert second_created is True
    assert first.id != second.id
    assert first.ordered_manifest_sha256 == second.ordered_manifest_sha256
    assert first.source_identity.endswith(f"scope-sha256:{'a' * 64}")
    assert second.source_identity.endswith(f"scope-sha256:{'c' * 64}")
