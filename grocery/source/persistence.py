"""Persist one successful KAMIS acquisition without retaining its raw payload."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from grocery.models import (
    FetchAttempt,
    PageReceipt,
    SourceArtifact,
    SourceConfiguration,
    build_source_artifact,
    ordered_page_manifest_sha256,
)
from grocery.source.client import REDACTED_REQUEST_SHAPE, KamisFetchResult


@dataclass(frozen=True, slots=True)
class CompletedKamisFetch:
    attempt: FetchAttempt
    artifact: SourceArtifact
    artifact_created: bool


@transaction.atomic
def start_kamis_fetch(
    source_configuration_id: uuid.UUID,
    *,
    acquisition_run_id: uuid.UUID | None = None,
    attempt_ordinal: int = 1,
) -> FetchAttempt:
    """Start one logical acquisition against an approved active source revision."""

    source = SourceConfiguration.objects.select_for_update().get(pk=source_configuration_id)
    if source.state != SourceConfiguration.State.ACTIVE:
        raise ValidationError("KAMIS fetches require an active source configuration.")
    if source.publication_mode != SourceConfiguration.PublicationMode.RECENT_COMPARISON:
        raise ValidationError("KAMIS recent fetches require the recent-comparison mode.")
    return FetchAttempt.objects.create(
        source_configuration=source,
        acquisition_run_id=acquisition_run_id or uuid.uuid4(),
        attempt_ordinal=attempt_ordinal,
        redacted_request_shape=REDACTED_REQUEST_SHAPE,
    )


@transaction.atomic
def complete_kamis_fetch(
    attempt_id: uuid.UUID,
    result: KamisFetchResult,
) -> CompletedKamisFetch:
    """Reconcile redacted receipts and atomically attach a content-addressed artifact."""

    attempt = (
        FetchAttempt.objects.select_for_update()
        .select_related("source_configuration")
        .get(pk=attempt_id)
    )
    if attempt.state == FetchAttempt.State.SUCCEEDED:
        return _validate_completed_replay(attempt, result)
    if attempt.state != FetchAttempt.State.STARTED:
        raise ValidationError("Only a started fetch attempt can be completed.")

    source = attempt.source_configuration
    _validate_result_budget(source, result)
    receipts = _receipt_candidates(attempt, result)
    if ordered_page_manifest_sha256(receipts) != result.ordered_manifest_sha256:
        raise ValidationError("The client and persistence ordered manifests differ.")

    PageReceipt.objects.bulk_create(receipts)
    attempt.state = FetchAttempt.State.SUCCEEDED
    attempt.completed_at = timezone.now()
    attempt.received_page_count = len(receipts)
    attempt.received_row_count = len(result.rows)
    attempt.received_byte_count = sum(receipt.body_byte_length for receipt in receipts)
    attempt.save()

    artifact, created = build_source_artifact(attempt.id)
    if artifact.ordered_manifest_sha256 != result.ordered_manifest_sha256:
        raise ValidationError("The persisted artifact manifest differs from the client result.")
    attempt.refresh_from_db()
    return CompletedKamisFetch(attempt=attempt, artifact=artifact, artifact_created=created)


def _validate_result_budget(
    source: SourceConfiguration,
    result: KamisFetchResult,
) -> None:
    if not result.page_receipts:
        raise ValidationError("A successful fetch requires at least one page receipt.")
    if result.call_count < len(result.page_receipts):
        raise ValidationError("The call count cannot be smaller than the page count.")
    if result.call_count > source.max_requests_per_attempt:
        raise ValidationError("The fetch exceeded its configured request budget.")
    if len(result.page_receipts) > source.max_pages_per_attempt:
        raise ValidationError("The fetch exceeded its configured page budget.")
    if any(receipt.byte_length > source.max_page_bytes for receipt in result.page_receipts):
        raise ValidationError("A fetch page exceeded its configured byte budget.")


def _receipt_candidates(
    attempt: FetchAttempt,
    result: KamisFetchResult,
) -> list[PageReceipt]:
    expected_ordinals = list(range(1, len(result.page_receipts) + 1))
    if [receipt.ordinal for receipt in result.page_receipts] != expected_ordinals:
        raise ValidationError("Client page receipt ordinals must be contiguous from one.")
    if [receipt.requested_page_number for receipt in result.page_receipts] != expected_ordinals:
        raise ValidationError("Client requested page numbers must be contiguous from one.")

    declared_totals = {receipt.declared_total_count for receipt in result.page_receipts}
    if len(declared_totals) != 1 or sum(
        receipt.row_count for receipt in result.page_receipts
    ) != len(result.rows):
        raise ValidationError("Client page rows do not reconcile with the result row count.")
    if declared_totals != {len(result.rows)}:
        raise ValidationError("Client declared total does not match the result row count.")

    candidates: list[PageReceipt] = []
    for receipt in result.page_receipts:
        if receipt.requested_page_number != receipt.declared_page_number:
            raise ValidationError("Client requested and declared page numbers differ.")
        if receipt.http_status != 200 or receipt.provider_result_code != "0":
            raise ValidationError("A successful result contains a failed page receipt.")
        candidates.append(
            PageReceipt(
                fetch_attempt=attempt,
                request_ordinal=receipt.ordinal,
                page_number=receipt.requested_page_number,
                http_status=receipt.http_status,
                provider_result_code=receipt.provider_result_code,
                declared_total_count=receipt.declared_total_count,
                received_row_count=receipt.row_count,
                body_state=PageReceipt.BodyState.RECEIVED,
                body_byte_length=receipt.byte_length,
                body_sha256=receipt.body_sha256,
                media_type=PageReceipt.MediaType.JSON,
                encoding=PageReceipt.Encoding.UTF_8,
            )
        )
    return candidates


def _validate_completed_replay(
    attempt: FetchAttempt,
    result: KamisFetchResult,
) -> CompletedKamisFetch:
    source = attempt.source_configuration
    _validate_result_budget(source, result)
    persisted_receipts = list(attempt.page_receipts.order_by("request_ordinal"))
    candidate_receipts = _receipt_candidates(attempt, result)
    persisted_shape = [
        (
            receipt.request_ordinal,
            receipt.page_number,
            receipt.declared_total_count,
            receipt.received_row_count,
            receipt.body_byte_length,
            receipt.body_sha256,
        )
        for receipt in persisted_receipts
    ]
    candidate_shape = [
        (
            receipt.request_ordinal,
            receipt.page_number,
            receipt.declared_total_count,
            receipt.received_row_count,
            receipt.body_byte_length,
            receipt.body_sha256,
        )
        for receipt in candidate_receipts
    ]
    if persisted_shape != candidate_shape:
        raise ValidationError("A completed fetch replay conflicts with its receipts.")
    artifact = attempt.artifact
    if artifact is None or artifact.ordered_manifest_sha256 != result.ordered_manifest_sha256:
        raise ValidationError("A completed fetch replay conflicts with its artifact.")
    return CompletedKamisFetch(
        attempt=attempt,
        artifact=artifact,
        artifact_created=False,
    )
