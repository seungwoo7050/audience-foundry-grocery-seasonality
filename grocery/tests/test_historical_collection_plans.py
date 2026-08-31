import uuid

from grocery.historical_collection_plans import plan_historical_collection
from grocery.models import SourceConfiguration
from grocery.source.historical_client import prepare_historical_request
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from grocery.tests.test_acquisition_models import create_source_configuration


def test_collection_plan_preserves_ordered_multi_partition_manifest(db: None) -> None:
    source = create_source_configuration(
        dataset_id="15156060",
        publication_mode=SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
    )
    prepared = tuple(
        prepare_historical_request(
            HistoricalDataset.MONTHLY,
            HistoricalPriceQuery(start="202301", end="202512", category_code=category),
        )
        for category in ("200", "400")
    )

    collection = plan_historical_collection(
        collection_id=uuid.uuid4(),
        source_configuration_id=source.id,
        prepared_requests=prepared,
        code_manifest_sha256="a" * 64,
    )

    assert collection.expected_part_count == 2
    assert collection.parts.count() == 0
