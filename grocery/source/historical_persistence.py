"""Persist the redacted identity of one explicitly bounded historical fetch."""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from grocery.models import FetchAttempt, SourceConfiguration
from grocery.source.historical_client import PreparedHistoricalRequest
from grocery.source.historical_contract import HistoricalDataset

_DATASET_MODES = {
    HistoricalDataset.MONTHLY: SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    HistoricalDataset.REGIONAL: SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL,
    HistoricalDataset.MARKET: SourceConfiguration.PublicationMode.HISTORICAL_MARKET,
}


@transaction.atomic
def start_historical_fetch(
    source_configuration_id: uuid.UUID,
    *,
    prepared_request: PreparedHistoricalRequest,
    acquisition_run_id: uuid.UUID | None = None,
    attempt_ordinal: int = 1,
) -> FetchAttempt:
    source = SourceConfiguration.objects.select_for_update().get(pk=source_configuration_id)
    if source.state != SourceConfiguration.State.ACTIVE:
        raise ValidationError("Historical fetches require an active source configuration.")
    expected_mode = _DATASET_MODES[prepared_request.query.dataset]
    if source.dataset_id != prepared_request.query.dataset.value:
        raise ValidationError("Historical source dataset and request do not match.")
    if source.publication_mode != expected_mode:
        raise ValidationError("Historical source mode and request do not match.")
    return FetchAttempt.objects.create(
        source_configuration=source,
        acquisition_run_id=acquisition_run_id or uuid.uuid4(),
        attempt_ordinal=attempt_ordinal,
        redacted_request_shape=prepared_request.request_shape,
        request_scope_sha256=prepared_request.scope_sha256,
    )
