from grocery.historical_generation_common import (
    historical_configuration_sha256,
    resolve_historical_region,
    resolve_historical_series,
)
from grocery.source.historical_contract import HistoricalDataset
from grocery.source.historical_dimensions import RegionObservation
from grocery.source.kamis import IdentityObservation
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle


def test_generation_resolves_only_exact_reviewed_identity_and_manifest(db: None) -> None:
    bundle = create_reviewed_historical_bundle()
    recent = bundle.series.recent_series
    identity = IdentityObservation(
        product_class_code=recent.product_class_code,
        product_class_name=recent.product_class_name,
        category_code=recent.category_code,
        category_name=recent.category_name,
        item_code=recent.item_code,
        item_name=recent.item_name,
        variety_code=recent.variety_code,
        variety_name=recent.variety_name,
        grade_code=recent.grade_code,
        grade_name=recent.grade_name,
        raw_unit=recent.raw_unit,
        raw_unit_size=recent.raw_unit_size,
        coverage_identity=recent.coverage_identity,
    )

    assert resolve_historical_series(identity, code_manifest_sha256="a" * 64) == bundle.series
    assert resolve_historical_region(
        RegionObservation(bundle.region.region_code, bundle.region.region_name)
    ) == bundle.region
    assert len(
        historical_configuration_sha256(
            dataset=HistoricalDataset.MONTHLY,
            parser_revision="kamis-15156060-v1",
            code_manifest_sha256="a" * 64,
        )
    ) == 64
