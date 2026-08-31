"""Authorized, idempotent historical collection review recording."""

from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_review_models import HistoricalCollectionReviewDecision


def _set_historical_review_token(decision_id: uuid.UUID | None) -> None:
    token = "" if decision_id is None else str(decision_id)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('grocery.historical_review_id', %s, true)",
            [token],
        )


@transaction.atomic
def record_historical_review_decision(
    *,
    decision_id: uuid.UUID,
    actor: Any,
    collection_id: uuid.UUID,
    decision: str,
    reconciliation_report_sha256: str,
    acceptance_evidence_sha256: str,
    reason_code: str,
    approved_result_sha256: str = "",
    approved_partition_manifest_sha256: str = "",
    supersedes_id: uuid.UUID | None = None,
) -> tuple[HistoricalCollectionReviewDecision, bool]:
    has_permission = getattr(actor, "has_perm", None)
    if (
        getattr(actor, "pk", None) is None
        or not bool(getattr(actor, "is_authenticated", False))
        or not bool(getattr(actor, "is_active", False))
        or not callable(has_permission)
        or not has_permission("grocery.review_historical_collection")
    ):
        raise PermissionDenied("An active historical collection reviewer is required.")

    fields: dict[str, object] = {
        "collection_id": collection_id,
        "decision": decision,
        "reviewer_id": actor.pk,
        "reconciliation_report_sha256": reconciliation_report_sha256,
        "acceptance_evidence_sha256": acceptance_evidence_sha256,
        "reason_code": reason_code,
        "approved_result_sha256": approved_result_sha256,
        "approved_partition_manifest_sha256": approved_partition_manifest_sha256,
        "supersedes_id": supersedes_id,
    }
    existing = (
        HistoricalCollectionReviewDecision.objects.select_for_update()
        .filter(pk=decision_id)
        .first()
    )
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in fields.items()):
            raise ValidationError("Historical review UUID replay conflicts with stored evidence.")
        return existing, False

    HistoricalSourceCollection.objects.select_for_update().get(pk=collection_id)
    list(
        HistoricalCollectionReviewDecision.objects.select_for_update().filter(
            collection_id=collection_id
        )
    )
    candidate = HistoricalCollectionReviewDecision(id=decision_id, **fields)
    candidate._review_write = True
    _set_historical_review_token(decision_id)
    try:
        candidate.save()
    finally:
        _set_historical_review_token(None)
    return candidate, True
