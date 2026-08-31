#!/usr/bin/env python3
"""Build the disposable vNext browser dataset; never valid outside DEBUG+QA."""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from grocery.tests.vnext_browser_fixture import build_vnext_browser_fixture  # noqa: E402


def main() -> None:
    fixture = build_vnext_browser_fixture()
    print(  # noqa: T201 - this is an operator script with an ID/count-only receipt.
        " ".join(
            (
                "status=READY",
                f"recent_revision_id={fixture.recent_revision_id}",
                f"historical_revision_id={fixture.historical_revision_id}",
                f"series={fixture.series_count}",
                f"regions={fixture.region_count}",
                f"markets={fixture.market_count}",
                f"monthly_facts={fixture.monthly_fact_count}",
            )
        )
    )


if __name__ == "__main__":
    main()
