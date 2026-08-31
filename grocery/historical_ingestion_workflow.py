"""Bounded source-to-candidate workflow shared by three operator commands."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from django.db.models import Max

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_collection_plans import plan_historical_collection
from grocery.historical_collections import complete_historical_collection
from grocery.historical_daily_generation import persist_market_part, persist_regional_part
from grocery.historical_generation import persist_monthly_part
from grocery.historical_registry import load_historical_dimension_registry
from grocery.models import FetchAttempt
from grocery.source.client import DEFAULT_PAGE_SIZE, KamisFetchResult, KamisTransportError
from grocery.source.historical_client import prepare_historical_request
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from grocery.source.historical_persistence import start_historical_fetch
from grocery.source.kamis import KamisParseError
from grocery.source.market_history import parse_market_price_rows
from grocery.source.monthly_history import parse_monthly_price_rows
from grocery.source.persistence import complete_kamis_fetch, fail_kamis_fetch
from grocery.source.regional_history import parse_regional_price_rows


class HistoricalSourceClient(Protocol):
    def fetch_historical_prices(
        self,
        dataset: HistoricalDataset,
        service_key: str,
        *,
        query: HistoricalPriceQuery,
        page_size: int,
    ) -> KamisFetchResult: ...


class HistoricalIngestionError(RuntimeError):
    SAFE_CODES = {
        "FETCH_FAILED",
        "FETCH_FINALIZATION_FAILED",
        "FETCH_PERSISTENCE_FAILED",
        "PARSE_FAILED",
        "PART_PERSISTENCE_FAILED",
        "COLLECTION_COMPLETION_FAILED",
    }

    def __init__(self, code: str) -> None:
        safe_code = code if code in self.SAFE_CODES else "PART_PERSISTENCE_FAILED"
        self.code = safe_code
        super().__init__(safe_code)


@dataclass(frozen=True, slots=True)
class HistoricalIngestionOutcome:
    collection: HistoricalSourceCollection
    partition_count: int
    accepted_row_count: int


def ingest_historical_collection(
    *,
    collection_id: uuid.UUID,
    source_configuration_id: uuid.UUID,
    dataset: HistoricalDataset,
    queries: tuple[HistoricalPriceQuery, ...],
    code_manifest_sha256: str,
    service_key: str,
    client: HistoricalSourceClient,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> HistoricalIngestionOutcome:
    prepared_requests = tuple(prepare_historical_request(dataset, query) for query in queries)
    collection = plan_historical_collection(
        collection_id=collection_id,
        source_configuration_id=source_configuration_id,
        prepared_requests=prepared_requests,
        code_manifest_sha256=code_manifest_sha256,
    )
    registry = load_historical_dimension_registry(code_manifest_sha256)
    next_attempt = (
        FetchAttempt.objects.filter(acquisition_run_id=collection.id).aggregate(
            maximum=Max("attempt_ordinal")
        )["maximum"]
        or 0
    ) + 1
    accepted = 0
    for partition_ordinal, (query, prepared) in enumerate(
        zip(queries, prepared_requests, strict=True), start=1
    ):
        attempt = start_historical_fetch(
            source_configuration_id,
            prepared_request=prepared,
            acquisition_run_id=collection.id,
            attempt_ordinal=next_attempt + partition_ordinal - 1,
        )
        try:
            fetched = client.fetch_historical_prices(
                dataset,
                service_key,
                query=query,
                page_size=page_size,
            )
        except KamisTransportError as error:
            try:
                fail_kamis_fetch(attempt.id, error)
            except Exception:
                raise HistoricalIngestionError("FETCH_FINALIZATION_FAILED") from None
            raise HistoricalIngestionError("FETCH_FAILED") from None
        try:
            completed_fetch = complete_kamis_fetch(attempt.id, fetched)
        except Exception:
            raise HistoricalIngestionError("FETCH_PERSISTENCE_FAILED") from None
        try:
            if dataset == HistoricalDataset.MONTHLY:
                parsed = parse_monthly_price_rows(fetched.rows, registry=registry)
                persist_monthly_part(
                    collection_id=collection.id,
                    ordinal=partition_ordinal,
                    artifact_id=completed_fetch.artifact.id,
                    prepared_request=prepared,
                    parsed=parsed,
                    code_manifest_sha256=code_manifest_sha256,
                )
            elif dataset == HistoricalDataset.REGIONAL:
                parsed = parse_regional_price_rows(fetched.rows, registry=registry)
                persist_regional_part(
                    collection_id=collection.id,
                    ordinal=partition_ordinal,
                    artifact_id=completed_fetch.artifact.id,
                    prepared_request=prepared,
                    parsed=parsed,
                    code_manifest_sha256=code_manifest_sha256,
                )
            else:
                parsed = parse_market_price_rows(fetched.rows, registry=registry)
                persist_market_part(
                    collection_id=collection.id,
                    ordinal=partition_ordinal,
                    artifact_id=completed_fetch.artifact.id,
                    prepared_request=prepared,
                    parsed=parsed,
                    code_manifest_sha256=code_manifest_sha256,
                )
        except KamisParseError:
            raise HistoricalIngestionError("PARSE_FAILED") from None
        except Exception:
            raise HistoricalIngestionError("PART_PERSISTENCE_FAILED") from None
        accepted += len(parsed.rows)
        del fetched, parsed
    try:
        completed = complete_historical_collection(collection.id)
    except Exception:
        raise HistoricalIngestionError("COLLECTION_COMPLETION_FAILED") from None
    return HistoricalIngestionOutcome(completed, len(prepared_requests), accepted)
