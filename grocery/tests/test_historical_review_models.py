import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.historical_reviews import record_historical_review_decision
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
    actor.user_permissions.add(Permission.objects.get(codename="review_historical_collection"))
    values = {
        "decision_id": uuid.uuid4(),
        "actor": actor,
        "collection_id": collection.id,
        "decision": HistoricalCollectionReviewDecision.Decision.APPROVE,
        "reconciliation_report_sha256": "d" * 64,
        "acceptance_evidence_sha256": "e" * 64,
        "reason_code": "RECONCILED",
        "approved_result_sha256": collection.result_sha256,
        "approved_partition_manifest_sha256": collection.partition_manifest_sha256,
    }

    decision, created = record_historical_review_decision(**values)
    assert created is True
    assert decision.collection_id == collection.id

    values["decision_id"] = uuid.uuid4()
    values["approved_result_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="hashes"):
        record_historical_review_decision(**values)
