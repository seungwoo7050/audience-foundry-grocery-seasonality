"""Idempotent plans for ordered multi-part historical source collections."""

from __future__ import annotations

import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_collections import partition_manifest_sha256
from grocery.models import SourceConfiguration
from grocery.source.historical_client import PreparedHistoricalRequest
from grocery.source.historical_contract import HistoricalDataset


@transaction.atomic
def plan_historical_collection(
    *,
    collection_id: uuid.UUID,
    source_configuration_id: uuid.UUID,
    prepared_requests: tuple[PreparedHistoricalRequest, ...],
    code_manifest_sha256: str,
) -> HistoricalSourceCollection:
    if not prepared_requests or len(prepared_requests) > 100:
        raise ValidationError("Historical collection plans require 1 to 100 partitions.")
    datasets = {prepared.query.dataset for prepared in prepared_requests}
    scopes = [prepared.scope_sha256 for prepared in prepared_requests]
    if len(datasets) != 1 or len(set(scopes)) != len(scopes):
        raise ValidationError("Historical collection partitions must be unique for one dataset.")
    dataset = datasets.pop()
    source = SourceConfiguration.objects.select_for_update().get(pk=source_configuration_id)
    expected_mode = {
        HistoricalDataset.MONTHLY: SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
        HistoricalDataset.REGIONAL: SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL,
        HistoricalDataset.MARKET: SourceConfiguration.PublicationMode.HISTORICAL_MARKET,
    }[dataset]
    if (
        source.state != SourceConfiguration.State.ACTIVE
        or source.dataset_id != dataset.value
        or source.publication_mode != expected_mode
    ):
        raise ValidationError("Historical collection plan does not match its active source.")
    date_field = "exmn_ym" if dataset == HistoricalDataset.MONTHLY else "exmn_ymd"
    windows = {
        (
            prepared.query.conditions[f"cond[{date_field}::GTE]"],
            prepared.query.conditions[f"cond[{date_field}::LTE]"],
        )
        for prepared in prepared_requests
    }
    if len(windows) != 1:
        raise ValidationError("Historical collection partitions require one common window.")
    window_min, window_max = windows.pop()
    kind = {
        HistoricalDataset.MONTHLY: HistoricalSourceCollection.Kind.MONTHLY,
        HistoricalDataset.REGIONAL: HistoricalSourceCollection.Kind.REGIONAL_DAILY,
        HistoricalDataset.MARKET: HistoricalSourceCollection.Kind.MARKET_DAILY,
    }[dataset]
    fields: dict[str, object] = {
        "kind": kind,
        "source_configuration_id": source.id,
        "code_manifest_sha256": code_manifest_sha256,
        "partition_manifest_sha256": partition_manifest_sha256(scopes),
        "expected_part_count": len(scopes),
        "month_min": window_min if dataset == HistoricalDataset.MONTHLY else "",
        "month_max": window_max if dataset == HistoricalDataset.MONTHLY else "",
        "date_min": (
            None
            if dataset == HistoricalDataset.MONTHLY
            else datetime.strptime(window_min, "%Y%m%d").date()
        ),
        "date_max": (
            None
            if dataset == HistoricalDataset.MONTHLY
            else datetime.strptime(window_max, "%Y%m%d").date()
        ),
    }
    existing = (
        HistoricalSourceCollection.objects.select_for_update().filter(pk=collection_id).first()
    )
    if existing is not None:
        if any(getattr(existing, name) != value for name, value in fields.items()):
            raise ValidationError("Historical collection plan UUID conflicts with stored scope.")
        return existing
    return HistoricalSourceCollection.objects.create(id=collection_id, **fields)
