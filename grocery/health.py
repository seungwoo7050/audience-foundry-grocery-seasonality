"""Safe process, database, and publication health endpoints."""

from __future__ import annotations

import logging
from typing import Final

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest, JsonResponse
from django.urls import path
from django.views.decorators.http import require_safe

from grocery.observability import log_event
from grocery.public_read import RECENT_RETAIL_CHANNEL, load_active_publication

_LOGGER: Final = logging.getLogger("grocery.audit")
_NO_STORE: Final = "no-store"


def _response(payload: dict[str, str], *, status: int) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response.headers["Cache-Control"] = _NO_STORE
    return response


def _database_and_migrations_ready() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        if cursor.fetchone() != (1,):
            return False

    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    targets = executor.loader.graph.leaf_nodes()
    return not executor.migration_plan(targets)


def _readiness_unavailable() -> JsonResponse:
    log_event(_LOGGER, "WARNING", "health.readiness.unavailable")
    return _response(
        {"check": "READINESS", "status": "UNAVAILABLE"},
        status=503,
    )


def _freshness_unavailable() -> JsonResponse:
    log_event(_LOGGER, "WARNING", "health.freshness.unavailable")
    return _response(
        {
            "check": "FRESHNESS",
            "channel": RECENT_RETAIL_CHANNEL,
            "publication_state": "UNAVAILABLE",
            "freshness_state": "UNAVAILABLE",
        },
        status=503,
    )


@require_safe
def live(request: HttpRequest) -> JsonResponse:
    """Confirm only that the Django process can serve a bounded response."""

    del request
    return _response(
        {"check": "LIVENESS", "status": "OK"},
        status=200,
    )


@require_safe
def ready(request: HttpRequest) -> JsonResponse:
    """Confirm schema currency and one readable, sealed publication pointer."""

    del request
    try:
        if not _database_and_migrations_ready():
            return _readiness_unavailable()
        active = load_active_publication()
        if active is None or active.freshness_state not in {"current", "stale"}:
            return _readiness_unavailable()
    except Exception:
        return _readiness_unavailable()
    return _response(
        {"check": "READINESS", "status": "READY"},
        status=200,
    )


@require_safe
def freshness(request: HttpRequest) -> JsonResponse:
    """Report only fixed publication availability and freshness states."""

    del request
    try:
        active = load_active_publication()
    except Exception:
        return _freshness_unavailable()
    if active is None:
        return _freshness_unavailable()
    if active.freshness_state == "stale":
        log_event(_LOGGER, "WARNING", "health.freshness.stale")
        return _response(
            {
                "check": "FRESHNESS",
                "channel": RECENT_RETAIL_CHANNEL,
                "publication_state": "AVAILABLE",
                "freshness_state": "STALE",
            },
            status=503,
        )
    if active.freshness_state != "current":
        return _freshness_unavailable()
    return _response(
        {
            "check": "FRESHNESS",
            "channel": RECENT_RETAIL_CHANNEL,
            "publication_state": "AVAILABLE",
            "freshness_state": "CURRENT",
        },
        status=200,
    )


app_name = "health"

urlpatterns = [
    path("live", live, name="live"),
    path("ready", ready, name="ready"),
    path("freshness", freshness, name="freshness"),
]
