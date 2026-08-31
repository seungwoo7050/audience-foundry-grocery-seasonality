import uuid
from decimal import Decimal

from grocery.historical_collection_plans import plan_historical_collection
from grocery.historical_collections import complete_historical_collection
from grocery.historical_generation import persist_monthly_part
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from grocery.source.historical_dimensions import RegionObservation, YearMonth
from grocery.source.historical_parser import ParsedHistoricalResult
from grocery.source.kamis import IdentityObservation
from grocery.source.monthly_history import ParsedMonthlyPriceRow
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle
from grocery.tests.historical_ingestion_factory import create_historical_artifact


def test_monthly_parser_result_persists_candidate_without_publication(db: None) -> None:
    bundle = create_reviewed_historical_bundle()
    recent = bundle.series.recent_series
    source, prepared, artifact = create_historical_artifact(
        HistoricalDataset.MONTHLY,
        HistoricalPriceQuery(
            start="202512", end="202512", category_code="200", item_code="212"
        ),
        row_count=1,
    )
    row = ParsedMonthlyPriceRow(
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
        source_effective_month=YearMonth(2025, 12),
        pmm_avgprc=Decimal("1000"),
        pmm_hgprc=Decimal("1100"),
        pmm_lwprc=Decimal("900"),
        pmm_stddvtn=Decimal("0"),
        pmm_cfcntvrtn=Decimal("0"),
        pmm_cfcntrng=Decimal("0"),
        pyy_avgprc=Decimal("1000"),
        pyy_hgprc=Decimal("1100"),
        pyy_lwprc=Decimal("900"),
        pyy_stddvtn=Decimal("0"),
        pyy_cfcntvrtn=Decimal("0"),
        pyy_cfcntrng=Decimal("0"),
        source_recorded_at_raw="20251231",
        source_row_hash="3" * 64,
    )
    parsed = ParsedHistoricalResult(rows=(row,), input_row_count=1, result_hash="4" * 64)
    review_count = HistoricalCollectionReviewDecision.objects.count()
    collection = plan_historical_collection(
        collection_id=uuid.uuid4(),
        source_configuration_id=source.id,
        prepared_requests=(prepared,),
        code_manifest_sha256="a" * 64,
    )

    completed = persist_monthly_part(
        collection_id=collection.id,
        ordinal=1,
        artifact_id=artifact.id,
        prepared_request=prepared,
        parsed=parsed,
        code_manifest_sha256="a" * 64,
    )
    validated = complete_historical_collection(collection.id)
    replay = persist_monthly_part(
        collection_id=collection.id,
        ordinal=1,
        artifact_id=artifact.id,
        prepared_request=prepared,
        parsed=parsed,
        code_manifest_sha256="a" * 64,
    )

    fact = MonthlyRegionalRetailPrice.objects.get(collection=completed.collection)
    assert (validated.state, fact.provider_mean) == ("VALIDATED", Decimal("1000"))
    assert replay.replayed is True
    assert HistoricalCollectionReviewDecision.objects.count() == review_count
