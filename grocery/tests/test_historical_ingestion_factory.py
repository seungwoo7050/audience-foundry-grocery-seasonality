from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from grocery.tests.historical_ingestion_factory import create_historical_artifact


def test_fixture_builds_a_scope_bound_hash_only_artifact(db: None) -> None:
    _source, prepared, artifact = create_historical_artifact(
        HistoricalDataset.MONTHLY,
        HistoricalPriceQuery(start="202512", end="202512", category_code="200"),
        row_count=1,
    )

    attempt = artifact.fetch_attempts.get()
    assert attempt.request_scope_sha256 == prepared.scope_sha256
    assert not hasattr(artifact, "body")
