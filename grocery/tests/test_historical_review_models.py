import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.models import SourceConfiguration
from grocery.tests.test_acquisition_models import create_source_configuration


def test_approval_is_bound_to_the_validated_collection_hashes(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    collection = HistoricalSourceCollection.objects.create(
        kind=HistoricalSourceCollection.Kind.MONTHLY,
        source_configuration=source,
        state=HistoricalSourceCollection.State.VALIDATED,
        code_manifest_sha256="a" * 64,
        partition_manifest_sha256="b" * 64,
        expected_part_count=1,
        month_min="202301",
        month_max="202512",
        accepted_row_count=1,
        result_sha256="c" * 64,
        completed_at=timezone.now(),
    )
    actor = get_user_model().objects.create_user(username="historical-reviewer")
    values = {
        "collection": collection,
        "decision": HistoricalCollectionReviewDecision.Decision.APPROVE,
        "reviewer": actor,
        "reconciliation_report_sha256": "d" * 64,
        "acceptance_evidence_sha256": "e" * 64,
        "reason_code": "RECONCILED",
        "approved_result_sha256": collection.result_sha256,
        "approved_partition_manifest_sha256": collection.partition_manifest_sha256,
    }

    decision = HistoricalCollectionReviewDecision.objects.create(**values)
    assert decision.collection_id == collection.id

    values["approved_result_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="hashes"):
        HistoricalCollectionReviewDecision.objects.create(**values)
