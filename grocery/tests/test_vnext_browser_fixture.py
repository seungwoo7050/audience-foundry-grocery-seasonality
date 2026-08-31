from unittest.mock import patch

import pytest
from django.test import override_settings

from grocery.historical_activation_models import HistoricalRetailPublicationChannel
from grocery.historical_daily_models import DailyMarketRetailPrice
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.models import PublicationChannel
from grocery.tests.vnext_browser_fixture import build_vnext_browser_fixture


@override_settings(DEBUG=True, ADMIN_ENABLED=False, QA_STATE_PREVIEWS_ENABLED=True)
def test_vnext_browser_fixture_uses_real_sealed_activation_without_source_calls(
    transactional_db: None,
) -> None:
    with (
        patch(
            "grocery.source.client.KamisHttpClient.fetch_recent_prices",
            side_effect=AssertionError("source call forbidden"),
        ),
        patch(
            "grocery.source.client.KamisHttpClient.fetch_historical_prices",
            side_effect=AssertionError("source call forbidden"),
        ),
    ):
        fixture = build_vnext_browser_fixture()

    assert (fixture.series_count, fixture.region_count, fixture.market_count) == (5, 2, 31)
    assert fixture.monthly_fact_count == 360
    assert MonthlyRegionalRetailPrice.objects.count() == 360
    assert DailyMarketRetailPrice.objects.count() == 155
    assert PublicationChannel.objects.get().current_revision_id == fixture.recent_revision_id
    assert (
        HistoricalRetailPublicationChannel.objects.get().current_revision_id
        == fixture.historical_revision_id
    )


@pytest.mark.django_db
@override_settings(DEBUG=False, ADMIN_ENABLED=False, QA_STATE_PREVIEWS_ENABLED=False)
def test_vnext_browser_fixture_is_denied_outside_disposable_qa() -> None:
    with pytest.raises(RuntimeError, match="environment_denied"):
        build_vnext_browser_fixture()
