from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle


def test_bundle_fixture_has_three_reviewed_sources_and_complete_36_months(db: None) -> None:
    bundle = create_reviewed_historical_bundle()

    assert MonthlyRegionalRetailPrice.objects.filter(series=bundle.series).count() == 36
    assert {
        bundle.monthly_review.collection.kind,
        bundle.regional_review.collection.kind,
        bundle.market_review.collection.kind,
    } == {"MONTHLY", "REGIONAL_DAILY", "MARKET_DAILY"}
