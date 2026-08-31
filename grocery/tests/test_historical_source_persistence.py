import hashlib
import json

from grocery.models import FetchAttempt, SourceConfiguration
from grocery.source.client import KamisFetchResult, PageReceipt
from grocery.source.historical_client import prepare_historical_request
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from grocery.source.historical_persistence import start_historical_fetch
from grocery.source.persistence import complete_kamis_fetch
from grocery.tests.test_acquisition_models import create_source_configuration


def test_historical_fetch_persists_only_redacted_scope_and_receipts(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    prepared = prepare_historical_request(
        HistoricalDataset.MONTHLY,
        HistoricalPriceQuery(start="202301", end="202512", category_code="200"),
    )
    attempt = start_historical_fetch(source.id, prepared_request=prepared)
    body_sha256 = "b" * 64
    manifest_sha256 = hashlib.sha256(
        json.dumps([body_sha256], separators=(",", ":")).encode("ascii")
    ).hexdigest()
    result = KamisFetchResult(
        rows=(),
        page_receipts=(
            PageReceipt(
                ordinal=1,
                requested_page_number=1,
                declared_page_number=1,
                declared_page_size=100,
                declared_total_count=0,
                row_count=0,
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

    assert completed.attempt.state == FetchAttempt.State.SUCCEEDED
    assert completed.attempt.request_scope_sha256 == prepared.scope_sha256
    assert "2023" not in completed.attempt.redacted_request_shape
    assert completed.artifact.source_identity.endswith(prepared.scope_sha256)
