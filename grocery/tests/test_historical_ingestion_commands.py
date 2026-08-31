import uuid
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command

from grocery.source.historical_contract import HistoricalDataset


@pytest.mark.parametrize(
    ("command_name", "dataset", "start", "end", "regions", "partition_count"),
    (
        (
            "ingest_kamis_monthly",
            HistoricalDataset.MONTHLY,
            "202501",
            "202512",
            ["1101", "2100"],
            2,
        ),
        (
            "ingest_kamis_regional_daily",
            HistoricalDataset.REGIONAL,
            "20250801",
            "20250831",
            ["1101"],
            1,
        ),
        (
            "ingest_kamis_market_daily",
            HistoricalDataset.MARKET,
            "20250801",
            "20250831",
            None,
            1,
        ),
    ),
)
def test_historical_commands_delegate_only_bounded_validated_queries(
    command_name: str,
    dataset: HistoricalDataset,
    start: str,
    end: str,
    regions: list[str] | None,
    partition_count: int,
) -> None:
    collection_id = uuid.uuid4()
    outcome = SimpleNamespace(
        collection=SimpleNamespace(id=collection_id),
        partition_count=partition_count,
        accepted_row_count=7,
    )
    secret = Mock()
    secret.reveal.return_value = "synthetic-command-key"
    stdout = StringIO()
    with (
        patch("grocery.management.historical_ingestion.load_historical_dimension_registry"),
        patch(
            "grocery.management.historical_ingestion.load_kamis_api_key",
            return_value=secret,
        ),
        patch(
            "grocery.management.historical_ingestion.ingest_historical_collection",
            return_value=outcome,
        ) as ingest,
    ):
        call_command(
            command_name,
            collection_id=str(collection_id),
            source_configuration_id=str(uuid.uuid4()),
            code_manifest_sha256="a" * 64,
            start=start,
            end=end,
            category_code="200",
            region_code=regions,
            stdout=stdout,
        )

    delegated = ingest.call_args.kwargs
    assert delegated["dataset"] == dataset
    assert len(delegated["queries"]) == partition_count
    assert stdout.getvalue().startswith("status=VALIDATED collection_id=")
