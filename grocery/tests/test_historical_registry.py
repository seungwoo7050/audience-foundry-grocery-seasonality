from grocery.historical_registry import load_historical_dimension_registry
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle


def test_registry_loads_only_pre_reviewed_series_region_and_market(db: None) -> None:
    bundle = create_reviewed_historical_bundle()

    registry = load_historical_dimension_registry("a" * 64)

    assert registry.region_names == {bundle.region.region_code: bundle.region.region_name}
    assert registry.market_names == {
        (bundle.region.region_code, bundle.market.market_code): bundle.market.market_name
    }
    assert set(registry.identity_registry.units) == {
        (
            bundle.series.recent_series.category_code,
            bundle.series.recent_series.item_code,
            bundle.series.recent_series.variety_code,
            bundle.series.recent_series.grade_code,
        )
    }
