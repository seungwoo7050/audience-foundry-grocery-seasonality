import uuid
from datetime import date
from decimal import Decimal

from grocery.historical_collection_plans import plan_historical_collection
from grocery.historical_collections import complete_historical_collection
from grocery.historical_daily_generation import persist_market_part
from grocery.historical_daily_models import DailyMarketRetailPrice
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from grocery.source.historical_dimensions import MarketObservation, RegionObservation
from grocery.source.historical_parser import ParsedHistoricalResult
from grocery.source.kamis import IdentityObservation
from grocery.source.market_history import ParsedMarketPriceRow
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle
from grocery.tests.historical_ingestion_factory import create_historical_artifact


def test_market_parser_result_persists_one_planned_part(db: None) -> None:
    bundle = create_reviewed_historical_bundle()
    recent = bundle.series.recent_series
    query = HistoricalPriceQuery(
        start="20251231",
        end="20251231",
        category_code="200",
        item_code="212",
        region_code=bundle.region.region_code,
    )
    source, prepared, artifact = create_historical_artifact(
        HistoricalDataset.MARKET, query, row_count=1
    )
    row = ParsedMarketPriceRow(
        identity=IdentityObservation(
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
        ),
        region=RegionObservation(bundle.region.region_code, bundle.region.region_name),
        market=MarketObservation(bundle.market.market_code, bundle.market.market_name),
        source_effective_date=date(2025, 12, 31),
        raw_observed_price=Decimal("1000"),
        converted_observed_price=Decimal("1000"),
        source_recorded_at_raw="20251231",
        source_row_hash="7" * 64,
    )
    parsed = ParsedHistoricalResult(rows=(row,), input_row_count=1, result_hash="8" * 64)
    collection = plan_historical_collection(
        collection_id=uuid.uuid4(),
        source_configuration_id=source.id,
        prepared_requests=(prepared,),
        code_manifest_sha256="a" * 64,
    )

    persist_market_part(
        collection_id=collection.id,
        ordinal=1,
        artifact_id=artifact.id,
        prepared_request=prepared,
        parsed=parsed,
        code_manifest_sha256="a" * 64,
    )
    completed = complete_historical_collection(collection.id)

    fact = DailyMarketRetailPrice.objects.get(collection=completed)
    assert (completed.state, fact.provider_price) == ("VALIDATED", Decimal("1000"))
