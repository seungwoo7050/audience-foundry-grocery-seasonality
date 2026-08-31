"""Authorized CAS transitions for the independent historical publication channel."""

from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from grocery.historical_activation_models import (
    HistoricalRetailPublicationActivation,
    HistoricalRetailPublicationChannel,
)
from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.historical_review_models import HistoricalCollectionReviewDecision


def _set_historical_transition_token(operation_id: uuid.UUID | None) -> None:
    token = "" if operation_id is None else str(operation_id)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('grocery.historical_transition_id', %s, true)",
            [token],
        )


def _bootstrap_historical_channel(operation_id: uuid.UUID) -> None:
    _set_historical_transition_token(operation_id)
    HistoricalRetailPublicationChannel.objects.bulk_create(
        [HistoricalRetailPublicationChannel()],
        ignore_conflicts=True,
    )


def _target_is_currently_approved(revision: HistoricalRetailPublicationRevision) -> bool:
    review_ids = (
        revision.monthly_review_id,
        revision.regional_review_id,
        revision.market_review_id,
    )
    return (
        revision.sealed_at is not None
        and not HistoricalCollectionReviewDecision.objects.filter(
            supersedes_id__in=review_ids
        ).exists()
    )


@transaction.atomic
def transition_historical_publication(
    *,
    operation_id: uuid.UUID,
    actor: Any,
    operation: str,
    target_revision_id: uuid.UUID | None,
    expected_current_revision_id: uuid.UUID | None,
    expected_version: int,
    reason_code: str,
    acceptance_evidence_sha256: str,
) -> tuple[HistoricalRetailPublicationActivation, bool]:
    has_permission = getattr(actor, "has_perm", None)
    if (
        getattr(actor, "pk", None) is None
        or not bool(getattr(actor, "is_authenticated", False))
        or not bool(getattr(actor, "is_active", False))
        or not callable(has_permission)
        or not has_permission("grocery.publish_historical_publication")
    ):
        raise PermissionDenied("An active historical publication publisher is required.")
    if type(operation_id) is not uuid.UUID:
        raise ValidationError("Historical publication operation ID must be a UUID.")
    if type(expected_version) is not int or expected_version < 0:
        raise ValidationError("Expected historical publication version must be non-negative.")

    _bootstrap_historical_channel(operation_id)
    channel = HistoricalRetailPublicationChannel.objects.select_for_update().get(
        pk=HistoricalRetailPublicationChannel.CHANNEL
    )
    semantic_fields: dict[str, object] = {
        "channel_id": HistoricalRetailPublicationChannel.CHANNEL,
        "operation": operation,
        "sequence": expected_version + 1,
        "previous_revision_id": expected_current_revision_id,
        "target_revision_id": target_revision_id,
        "publisher_id": actor.pk,
        "reason_code": reason_code,
        "acceptance_evidence_sha256": acceptance_evidence_sha256,
    }
    existing = (
        HistoricalRetailPublicationActivation.objects.select_for_update()
        .filter(pk=operation_id)
        .first()
    )
    if existing is not None:
        if any(getattr(existing, name) != value for name, value in semantic_fields.items()):
            raise ValidationError(
                "Historical publication operation UUID conflicts with stored evidence."
            )
        _set_historical_transition_token(None)
        return existing, False
    if (
        channel.version != expected_version
        or channel.current_revision_id != expected_current_revision_id
    ):
        raise ValidationError("Historical publication expectation is stale.")
    if operation not in HistoricalRetailPublicationActivation.Operation.values:
        raise ValidationError("Historical publication operation is invalid.")

    target: HistoricalRetailPublicationRevision | None
    if operation == HistoricalRetailPublicationActivation.Operation.WITHDRAW:
        if target_revision_id is not None or channel.current_revision_id is None:
            raise ValidationError("Withdrawal requires a current historical revision.")
        target = None
    else:
        if target_revision_id is None or target_revision_id == channel.current_revision_id:
            raise ValidationError("Historical publication requires a different target.")
        target = (
            HistoricalRetailPublicationRevision.objects.select_for_update()
            .select_related("monthly_review", "regional_review", "market_review")
            .filter(pk=target_revision_id)
            .first()
        )
        if target is None or target.sealed_at is None:
            raise ValidationError("Historical publication target is not a sealed bundle.")
        if (
            operation == HistoricalRetailPublicationActivation.Operation.ACTIVATE
            and not _target_is_currently_approved(target)
        ):
            raise ValidationError("Historical activation requires current reviewed approvals.")
        if (
            operation == HistoricalRetailPublicationActivation.Operation.ROLLBACK
            and not HistoricalRetailPublicationActivation.objects.filter(
                channel=channel,
                sequence__lte=expected_version,
                operation__in=(
                    HistoricalRetailPublicationActivation.Operation.ACTIVATE,
                    HistoricalRetailPublicationActivation.Operation.ROLLBACK,
                ),
                target_revision_id=target.id,
            ).exists()
        ):
            raise ValidationError("Historical rollback target was not previously current.")

    activation = HistoricalRetailPublicationActivation(
        id=operation_id,
        channel=channel,
        operation=operation,
        sequence=expected_version + 1,
        previous_revision_id=expected_current_revision_id,
        target_revision=target,
        publisher_id=actor.pk,
        reason_code=reason_code,
        acceptance_evidence_sha256=acceptance_evidence_sha256,
    )
    activation._transition_write = True
    _set_historical_transition_token(operation_id)
    activation.save()
    updated = HistoricalRetailPublicationChannel.objects.filter(
        pk=HistoricalRetailPublicationChannel.CHANNEL,
        current_revision_id=expected_current_revision_id,
        version=expected_version,
    ).update(
        current_revision_id=target_revision_id,
        version=expected_version + 1,
        updated_at=timezone.now(),
    )
    if updated != 1:
        raise ValidationError("Historical publication pointer did not advance exactly once.")
    _set_historical_transition_token(None)
    activation.refresh_from_db()
    return activation, True
