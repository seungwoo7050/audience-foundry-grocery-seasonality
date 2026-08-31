import uuid

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from grocery.historical_daily_read import markets_context, regions_context
from grocery.historical_history_read import history_context
from grocery.historical_public_read import ActiveHistoricalPublication
from grocery.historical_publications import seal_historical_publication
from grocery.models import (
    PriceChangeFact,
    PublicationActivation,
    seal_recent_publication,
    transition_recent_publication,
)
from grocery.public_read import (
    _filter_and_sort_catalog_entries,
    load_active_publication,
    publication_entries,
    publication_entries_for_series,
)
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle
from grocery.tests.test_publication_revision_models import create_approved_generation


def _activate_recent(count: int):
    decision, snapshots, publisher = create_approved_generation(snapshot_count=count)
    publisher.user_permissions.add(
        Permission.objects.get(content_type__app_label="grocery", codename="publish_publication")
    )
    publisher = type(publisher)._default_manager.get(pk=publisher.pk)
    revision = seal_recent_publication(decision.id, "ko-v4")
    transition_recent_publication(
        operation_id=uuid.uuid4(),
        actor=publisher,
        operation=PublicationActivation.Operation.ACTIVATE,
        target_revision_id=revision.id,
        expected_current_revision_id=None,
        expected_version=0,
        reason_code="LOCAL_VNEXT_QUERY_TEST",
        acceptance_evidence_sha256="a" * 64,
    )
    active = load_active_publication()
    assert active is not None
    return active, snapshots


def _sealed_historical():
    bundle = create_reviewed_historical_bundle()
    revision = seal_historical_publication(
        monthly_review_id=bundle.monthly_review.id,
        regional_review_id=bundle.regional_review.id,
        market_review_id=bundle.market_review.id,
        compatibility_report_sha256="b" * 64,
    )
    # Warm only publication metadata; measured calls must account for fact queries.
    _collections = (
        revision.monthly_review.collection,
        revision.regional_review.collection,
        revision.market_review.collection,
    )
    active = ActiveHistoricalPublication(
        revision=revision,
        checked_at=timezone.now(),
        freshness_state="current",
        freshness_label="KAMIS 자료 확인 완료",
        stale_message="",
    )
    return active, bundle


@pytest.mark.django_db
def test_catalog_materialization_is_two_queries_for_one_or_thirty_rows() -> None:
    active, _snapshots = _activate_recent(30)

    with CaptureQueriesContext(connection) as one:
        publication_entries(active, query="품목 212", category="")
    with CaptureQueriesContext(connection) as thirty:
        publication_entries(active, query="", category="")

    assert len(one) == len(thirty) == 2


@pytest.mark.django_db
def test_catalog_materialization_rejects_missing_duplicate_and_incomplete_selected_facts() -> None:
    active, _snapshots = _activate_recent(2)
    entries = publication_entries(active, query="", category="")
    entry = entries[0]
    reference = entry.snapshot.catalog_week_references[0]

    for malformed in ([], [reference, reference]):
        entry.snapshot.catalog_week_references = malformed
        with pytest.raises(ValidationError):
            _filter_and_sort_catalog_entries([entry], direction="all", sort="name")

    entry.snapshot.catalog_week_references = [reference]
    original_direction = reference.change_fact.direction
    original_percentage = reference.change_fact.signed_percentage
    reference.change_fact.direction = PriceChangeFact.Direction.HIGHER
    reference.change_fact.signed_percentage = None
    with pytest.raises(ValidationError):
        _filter_and_sort_catalog_entries([entry], direction="all", sort="name")
    reference.change_fact.direction = original_direction
    reference.change_fact.signed_percentage = original_percentage

    entries[1].snapshot.catalog_week_references = []
    with pytest.raises(ValidationError):
        _filter_and_sort_catalog_entries(
            entries,
            query=entries[0].snapshot.series.item_name,
            direction="all",
            sort="name",
        )


@pytest.mark.django_db
def test_history_and_daily_ledgers_have_row_independent_query_counts() -> None:
    active, bundle = _sealed_historical()

    with CaptureQueriesContext(connection) as twelve:
        history_context(
            active,
            bundle.series,
            selected_region_id=bundle.region.id,
            selected_range=12,
        )
    with CaptureQueriesContext(connection) as thirty_six:
        history_context(
            active,
            bundle.series,
            selected_region_id=bundle.region.id,
            selected_range=36,
        )
    with CaptureQueriesContext(connection) as regions:
        regions_context(active, bundle.series, selected_date=None)
    with CaptureQueriesContext(connection) as markets:
        markets_context(
            active,
            bundle.series,
            region_id=bundle.region.id,
            selected_date=None,
            page=1,
        )

    assert len(twelve) == len(thirty_six) == 1
    assert len(regions) == 2
    assert len(markets) == 2


@pytest.mark.django_db
def test_selection_reference_queries_do_not_grow_from_one_to_five_items() -> None:
    active, snapshots = _activate_recent(5)

    with CaptureQueriesContext(connection) as one:
        publication_entries_for_series(active, [snapshots[0].series_id])
    with CaptureQueriesContext(connection) as five:
        publication_entries_for_series(active, [snapshot.series_id for snapshot in snapshots])

    assert len(one) == len(five) == 2
