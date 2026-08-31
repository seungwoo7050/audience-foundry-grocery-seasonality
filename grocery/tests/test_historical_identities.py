import pytest
from django.core.exceptions import ValidationError

from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailMarketKey,
    RetailRegionKey,
    price_series_identity_sha256,
)
from grocery.tests.test_price_series_key_models import create_series


def test_cross_source_identity_excludes_recent_coverage_but_rejects_drift(db: None) -> None:
    series = create_series()
    identity = HistoricalRetailSeriesKey.objects.create(
        recent_series=series,
        series_identity_sha256=price_series_identity_sha256(series),
        cross_source_evidence_revision="cross-source-v1",
        code_manifest_sha256="a" * 64,
    )

    assert identity.recent_series_id == series.id
    identity.series_identity_sha256 = "b" * 64
    with pytest.raises(ValidationError, match="immutable"):
        identity.save()


def test_region_and_market_preserve_official_leading_zero_codes(db: None) -> None:
    region = RetailRegionKey.objects.create(
        region_code="1101",
        region_name="서울",
        identity_evidence_revision="codebook-v1",
    )
    market = RetailMarketKey.objects.create(
        region=region,
        market_code="0110253",
        market_name="양곡도매",
        identity_evidence_revision="codebook-v1",
    )

    assert market.market_code == "0110253"
    assert market.region_id == region.id
    market.market_name = "changed"
    with pytest.raises(ValidationError, match="immutable"):
        market.save()
