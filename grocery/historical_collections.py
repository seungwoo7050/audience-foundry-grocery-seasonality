"""Atomic reconciliation for complete historical source collections."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.models import FetchAttempt, ParseRun


def partition_manifest_sha256(scope_hashes: Sequence[str]) -> str:
    canonical = json.dumps(list(scope_hashes), separators=(",", ":")).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


@transaction.atomic
def complete_historical_collection(collection_id: uuid.UUID) -> HistoricalSourceCollection:
    collection = HistoricalSourceCollection.objects.select_for_update().get(pk=collection_id)
    if collection.state == HistoricalSourceCollection.State.VALIDATED:
        return collection
    if collection.state != HistoricalSourceCollection.State.STARTED:
        raise ValidationError("Only a started historical collection can be completed.")

    parts = list(
        HistoricalSourceCollectionPart.objects.select_for_update()
        .select_related("parse_run__artifact")
        .filter(collection=collection)
        .order_by("ordinal")
    )
    expected_ordinals = list(range(1, collection.expected_part_count + 1))
    if [part.ordinal for part in parts] != expected_ordinals:
        raise ValidationError("Historical collection parts are incomplete or non-contiguous.")
    scopes = [part.partition_scope_sha256 for part in parts]
    if partition_manifest_sha256(scopes) != collection.partition_manifest_sha256:
        raise ValidationError("Historical collection partition manifest does not match its plan.")

    fact_models: dict[str, Any] = {
        HistoricalSourceCollection.Kind.MONTHLY: MonthlyRegionalRetailPrice,
        HistoricalSourceCollection.Kind.REGIONAL_DAILY: DailyRegionalRetailPrice,
        HistoricalSourceCollection.Kind.MARKET_DAILY: DailyMarketRetailPrice,
    }
    selected_model = fact_models[collection.kind]
    other_models = tuple(model for model in fact_models.values() if model is not selected_model)
    accepted = 0
    out_of_scope = 0
    result_parts: list[dict[str, object]] = []
    for part in parts:
        parse_run = part.parse_run
        if parse_run.status != ParseRun.Status.VALIDATED or not parse_run.result_hash:
            raise ValidationError("Historical collection parts require validated parse runs.")
        if not FetchAttempt.objects.filter(
            source_configuration=collection.source_configuration,
            artifact=parse_run.artifact,
            state=FetchAttempt.State.SUCCEEDED,
            request_scope_sha256=part.partition_scope_sha256,
        ).exists():
            raise ValidationError(
                "Historical collection parse source does not match the collection scope."
            )
        actual_count = selected_model.objects.filter(
            collection=collection,
            collection_part=part,
        ).count()
        if actual_count != part.fact_count or actual_count != parse_run.accepted_row_count:
            raise ValidationError("Historical collection fact count does not match its parse part.")
        if any(
            model.objects.filter(collection=collection, collection_part=part).exists()
            for model in other_models
        ):
            raise ValidationError("Historical collection contains facts from another source kind.")
        accepted += actual_count
        out_of_scope += parse_run.out_of_scope_row_count
        result_parts.append(
            {
                "ordinal": part.ordinal,
                "partition_scope_sha256": part.partition_scope_sha256,
                "parse_result_sha256": parse_run.result_hash,
                "fact_count": actual_count,
            }
        )

    result_bytes = json.dumps(
        {"kind": collection.kind, "parts": result_parts},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    collection.state = HistoricalSourceCollection.State.VALIDATED
    collection.accepted_row_count = accepted
    collection.out_of_scope_row_count = out_of_scope
    collection.quarantined_row_count = 0
    collection.result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    collection.completed_at = timezone.now()
    collection.save()
    return collection
