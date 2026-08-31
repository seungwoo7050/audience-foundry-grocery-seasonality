from grocery.historical_publications import seal_historical_publication
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle


def test_seal_binds_complete_three_source_fact_set_and_replays(db: None) -> None:
    bundle = create_reviewed_historical_bundle()
    values = {
        "monthly_review_id": bundle.monthly_review.id,
        "regional_review_id": bundle.regional_review.id,
        "market_review_id": bundle.market_review.id,
        "compatibility_report_sha256": "2" * 64,
    }

    revision = seal_historical_publication(**values)
    replay = seal_historical_publication(**values)

    assert revision.sealed_at is not None
    assert revision.typed_fact_set_sha256 == replay.typed_fact_set_sha256
    assert (revision.series_count, revision.monthly_fact_count) == (1, 36)
    assert (revision.regional_fact_count, revision.market_fact_count) == (1, 1)
