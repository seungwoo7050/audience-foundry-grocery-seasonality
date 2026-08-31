from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from grocery.source.client import KamisFetchResult
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery
from scripts.live_api_e2e_smoke import (
    CachedLiveClient,
    LiveSmokeInvariantError,
    LiveSmokeReceipt,
    month_shift,
    safe_failure_code,
    validate_disposable_environment,
)

SAFE_DATABASE = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "grocery_vnext_live_unit_test",
    "HOST": "127.0.0.1",
    "PORT": 55434,
}


def validate_environment(**overrides: object) -> None:
    values: dict[str, object] = {
        "opt_in": "1",
        "debug": True,
        "admin_enabled": False,
        "qa_previews_enabled": False,
        "control_plane_enabled": False,
        "database": SAFE_DATABASE,
        "occupied": False,
    }
    values.update(overrides)
    validate_disposable_environment(
        opt_in=values["opt_in"],
        debug=values["debug"],
        admin_enabled=values["admin_enabled"],
        qa_previews_enabled=values["qa_previews_enabled"],
        control_plane_enabled=values["control_plane_enabled"],
        database=cast(Mapping[str, object], values["database"]),
        occupied=cast(bool, values["occupied"]),
    )


def test_disposable_environment_accepts_only_empty_loopback_live_database() -> None:
    validate_environment()

    invalid = (
        {"opt_in": None},
        {"debug": False},
        {"admin_enabled": True},
        {"qa_previews_enabled": True},
        {"control_plane_enabled": True},
        {"database": {**SAFE_DATABASE, "NAME": "grocery"}},
        {"database": {**SAFE_DATABASE, "HOST": "database.internal"}},
        {"database": {**SAFE_DATABASE, "PORT": 5432}},
        {"occupied": True},
    )
    for override in invalid:
        with pytest.raises(LiveSmokeInvariantError, match="disposable_environment_denied"):
            validate_environment(**override)


def test_failure_receipt_never_reflects_exception_text_or_unsafe_code() -> None:
    marker = "credential-and-query-marker"

    class UnsafeCode(RuntimeError):
        code = f"unsafe={marker}"

    assert safe_failure_code(RuntimeError(marker)) == "RuntimeError"
    assert safe_failure_code(UnsafeCode(marker)) == "UnsafeCode"
    assert marker not in safe_failure_code(UnsafeCode(marker))


def test_month_window_and_success_receipt_are_value_free() -> None:
    assert month_shift(date(2026, 1, 31), -1) == "202512"
    assert month_shift(date(2026, 8, 1), -35) == "202309"
    assert LiveSmokeReceipt(10, 36, 1, 9).render() == (
        "status=PASS recent_rows=10 monthly_rows=36 regional_rows=1 market_rows=9 "
        "ssr_routes=5 source_calls_during_ssr=0 raw_response_retained=no"
    )


def test_cached_live_result_is_single_use_and_scope_bound() -> None:
    query = HistoricalPriceQuery(start="202601", end="202601", category_code="200")
    result = KamisFetchResult((), (), "a" * 64, 1)
    client = CachedLiveClient(HistoricalDataset.MONTHLY, query, result)

    assert (
        client.fetch_historical_prices(
            HistoricalDataset.MONTHLY,
            "test-only-cached-live-result",
            query=query,
            page_size=1_000,
        )
        is result
    )
    with pytest.raises(LiveSmokeInvariantError, match="cached_result_contract_invalid"):
        client.fetch_historical_prices(
            HistoricalDataset.MONTHLY,
            "test-only-cached-live-result",
            query=query,
            page_size=1_000,
        )


def test_make_target_is_explicit_and_outside_repository_gates() -> None:
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")

    assert "live-source-e2e-smoke: source-secret-env-check" in makefile
    assert "LIVE_SOURCE_E2E_SMOKE=1" in makefile
    assert "check: format-check lint type migration-check test" in makefile
    assert "production-check: source-secret-env-check production-env-check" in makefile
