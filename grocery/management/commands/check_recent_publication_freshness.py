"""Emit one scheduler-safe state receipt for the recent-retail publication."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Final

from django.core.management.base import BaseCommand, CommandError

from grocery.public_read import RECENT_RETAIL_CHANNEL, load_active_publication


class _FailureCode(StrEnum):
    STALE = "RECENT_PUBLICATION_FRESHNESS_STALE"
    UNAVAILABLE = "RECENT_PUBLICATION_FRESHNESS_UNAVAILABLE"
    FAILED = "RECENT_PUBLICATION_FRESHNESS_FAILED"


def _receipt(*, publication_state: str, freshness_state: str) -> str:
    return json.dumps(
        {
            "check": "FRESHNESS",
            "channel": RECENT_RETAIL_CHANNEL,
            "publication_state": publication_state,
            "freshness_state": freshness_state,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


_CURRENT_RECEIPT: Final = _receipt(
    publication_state="AVAILABLE",
    freshness_state="CURRENT",
)
_STALE_RECEIPT: Final = _receipt(
    publication_state="AVAILABLE",
    freshness_state="STALE",
)
_UNAVAILABLE_RECEIPT: Final = _receipt(
    publication_state="UNAVAILABLE",
    freshness_state="UNAVAILABLE",
)


class Command(BaseCommand):
    help = "Check the database-backed recent publication freshness for scheduler alerting."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        try:
            active = load_active_publication()
            freshness_state = None if active is None else active.freshness_state
        except Exception:
            self.stdout.write(_UNAVAILABLE_RECEIPT)
            raise CommandError(f"code={_FailureCode.FAILED.value}") from None

        if active is None:
            self.stdout.write(_UNAVAILABLE_RECEIPT)
            raise CommandError(f"code={_FailureCode.UNAVAILABLE.value}") from None
        if type(freshness_state) is not str:
            self.stdout.write(_UNAVAILABLE_RECEIPT)
            raise CommandError(f"code={_FailureCode.FAILED.value}") from None
        if freshness_state == "stale":
            self.stdout.write(_STALE_RECEIPT)
            raise CommandError(f"code={_FailureCode.STALE.value}") from None
        if freshness_state != "current":
            self.stdout.write(_UNAVAILABLE_RECEIPT)
            raise CommandError(f"code={_FailureCode.FAILED.value}") from None

        self.stdout.write(_CURRENT_RECEIPT)
