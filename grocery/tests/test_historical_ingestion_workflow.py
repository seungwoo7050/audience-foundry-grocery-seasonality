import hashlib
import json
import uuid

from grocery.historical_ingestion_workflow import ingest_historical_collection
from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.models import SourceConfiguration
from grocery.source.client import KamisFetchResult, PageReceipt
from grocery.source.historical_client import prepare_historical_request
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle
from grocery.tests.historical_fixtures import monthly_row
from grocery.tests.test_acquisition_models import create_source_configuration


class _SyntheticClient:
    def __init__(self, row: dict[str, str]) -> None:
        self.row = row
        self.calls = 0

    def fetch_historical_prices(
        self,
        dataset: HistoricalDataset,
        service_key: str,
        *,
        query: HistoricalPriceQuery,
        page_size: int,
    ) -> KamisFetchResult:
        del service_key
        self.calls += 1
        prepared = prepare_historical_request(dataset, query)
        body_hash = hashlib.sha256(b"synthetic-page").hexdigest()
        manifest = hashlib.sha256(
            json.dumps([body_hash], separators=(",", ":")).encode("ascii")
        ).hexdigest()
        return KamisFetchResult(
            rows=(self.row,),
            page_receipts=(
                PageReceipt(1, 1, 1, page_size, 1, 1, 200, "0", 10, body_hash),
            ),
            ordered_manifest_sha256=manifest,
            call_count=1,
            request_scope_sha256=prepared.scope_sha256,
        )


def test_workflow_uses_synthetic_transport_and_stops_before_review(db: None) -> None:
    bundle = create_reviewed_historical_bundle()
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    row = monthly_row()
    row.update(exmn_ym="202512", sgg_cd=bundle.region.region_code, sgg_nm=bundle.region.region_name)
    client = _SyntheticClient(row)
    query = HistoricalPriceQuery(
        start="202512", end="202512", category_code="200", item_code="212"
    )
    review_count = HistoricalCollectionReviewDecision.objects.count()

    outcome = ingest_historical_collection(
        collection_id=uuid.uuid4(),
        source_configuration_id=source.id,
        dataset=HistoricalDataset.MONTHLY,
        queries=(query,),
        code_manifest_sha256="a" * 64,
        service_key="synthetic-only",
        client=client,
    )

    assert (outcome.collection.state, outcome.accepted_row_count, client.calls) == (
        "VALIDATED",
        1,
        1,
    )
    assert HistoricalCollectionReviewDecision.objects.count() == review_count
    assert HistoricalRetailPublicationRevision.objects.count() == 0
