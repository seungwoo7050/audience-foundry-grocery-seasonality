"""No-JavaScript URL selection route over the active recent publication."""

from __future__ import annotations

import logging
from typing import Final
from urllib.parse import urlencode

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from grocery.forms import parse_selection_query
from grocery.observability import log_event
from grocery.public_read import (
    load_active_publication,
    publication_candidate_entries,
    publication_context,
    publication_entries_for_series,
    selection_candidate_context,
    selection_item_context,
)

_LOGGER: Final = logging.getLogger("grocery.audit")


@require_safe
def selection(request: HttpRequest) -> HttpResponse:
    try:
        selection_query = parse_selection_query(request.GET)
    except ValidationError:
        return render(
            request,
            "grocery/selection.html",
            {
                "home_url": reverse("grocery:catalog"),
                "catalog_url": reverse("grocery:catalog"),
                "selection_state": "validation",
                "validation_errors": [{"message": "선택한 품목을 확인하세요.", "target": ""}],
            },
            status=400,
        )
    try:
        active = load_active_publication()
        base = {
            "home_url": reverse("grocery:catalog"),
            "catalog_url": reverse("grocery:catalog"),
            "selection_form_action": reverse("grocery:selection"),
        }
        if active is None:
            return render(
                request,
                "grocery/selection.html",
                {**base, "selection_state": "unavailable", "items": []},
            )
        entries = publication_entries_for_series(active, selection_query.series_ids)
        entry_ids = tuple(entry.snapshot.series_id for entry in entries)
        excluded_count = len(selection_query.series_ids) - len(entry_ids)
        items = []
        for entry in entries:
            remaining = tuple(
                series_id for series_id in entry_ids if series_id != entry.snapshot.series_id
            )
            items.append(
                selection_item_context(
                    entry,
                    active,
                    detail_url=reverse(
                        "grocery:detail", kwargs={"series_id": entry.snapshot.series_id}
                    ),
                    remove_url=_selection_url(remaining),
                )
            )
        limit_reached = len(items) >= 5
        candidates = (
            []
            if limit_reached
            else [
                selection_candidate_context(entry)
                for entry in publication_candidate_entries(active, excluded_series_ids=entry_ids)
            ]
        )
        canonical_url = _selection_url(entry_ids)
        context = {
            **base,
            "selection_url": canonical_url,
            "selection_count": len(items),
            "selection_state": "partial" if excluded_count else "ready",
            "selection_is_stale": bool(active.stale_message),
            "publication": publication_context(active),
            "items": items,
            "excluded_count": excluded_count,
            "result_count_label": f"선택 품목 {len(items)}개",
            "clear_url": reverse("grocery:selection") if items else "",
            "selection_limit_reached": limit_reached,
            "selection_candidates": candidates,
            "can_add_selection": bool(candidates) and not limit_reached,
        }
        response = render(request, "grocery/selection.html", context)
        response.headers["X-Publication-Fact-Set"] = active.revision.typed_fact_set_sha256
        return response
    except DatabaseError, ObjectDoesNotExist, ValidationError:
        log_event(_LOGGER, "ERROR", "public.selection.unavailable")
        return render(
            request,
            "grocery/selection.html",
            {
                "home_url": reverse("grocery:catalog"),
                "catalog_url": reverse("grocery:catalog"),
                "selection_state": "server_error",
                "retry_url": reverse("grocery:selection"),
            },
            status=503,
        )


def _selection_url(series_ids: tuple[object, ...]) -> str:
    base = reverse("grocery:selection")
    query = urlencode({"series": [str(series_id) for series_id in series_ids]}, doseq=True)
    return f"{base}?{query}" if query else base
