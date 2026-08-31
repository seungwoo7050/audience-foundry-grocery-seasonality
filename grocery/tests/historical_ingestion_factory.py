import hashlib
import json

from grocery.models import SourceArtifact, SourceConfiguration
from grocery.source.client import KamisFetchResult, PageReceipt
from grocery.source.historical_client import PreparedHistoricalRequest, prepare_historical_request
from grocery.source.historical_contract import (
    HistoricalDataset,
    HistoricalPriceQuery,
)
from grocery.source.historical_persistence import start_historical_fetch
from grocery.source.persistence import complete_kamis_fetch
from grocery.tests.test_acquisition_models import create_source_configuration


def create_historical_artifact(
    dataset: HistoricalDataset,
    query: HistoricalPriceQuery,
    *,
    row_count: int,
) -> tuple[SourceConfiguration, PreparedHistoricalRequest, SourceArtifact]:
    mode = {
        HistoricalDataset.MONTHLY: SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
        HistoricalDataset.REGIONAL: SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL,
        HistoricalDataset.MARKET: SourceConfiguration.PublicationMode.HISTORICAL_MARKET,
    }[dataset]
    source = create_source_configuration(dataset_id=dataset.value, publication_mode=mode)
    prepared = prepare_historical_request(dataset, query)
    attempt = start_historical_fetch(source.id, prepared_request=prepared)
    body_sha256 = hashlib.sha256(f"body:{dataset.value}".encode()).hexdigest()
    manifest_sha256 = hashlib.sha256(
        json.dumps([body_sha256], separators=(",", ":")).encode("ascii")
    ).hexdigest()
    result = KamisFetchResult(
        rows=tuple({"fixture": "not-persisted"} for _ in range(row_count)),
        page_receipts=(
            PageReceipt(
                ordinal=1,
                requested_page_number=1,
                declared_page_number=1,
                declared_page_size=100,
                declared_total_count=row_count,
                row_count=row_count,
                http_status=200,
                provider_result_code="0",
                byte_length=10,
                body_sha256=body_sha256,
            ),
        ),
        ordered_manifest_sha256=manifest_sha256,
        call_count=1,
        request_scope_sha256=prepared.scope_sha256,
    )
    completed = complete_kamis_fetch(attempt.id, result)
    return source, prepared, completed.artifact
