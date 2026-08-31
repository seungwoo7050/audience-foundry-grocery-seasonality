"""SSR route for monthly KAMIS history."""

from __future__ import annotations

import logging
import uuid
from typing import Final, cast

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from grocery.forms import HistoryForm
from grocery.historical_history_read import history_context
from grocery.historical_public_read import (
    PublicParameterError,
    PublicReadIntegrityError,
    historical_publication_context,
    historical_series_context,
    historical_series_for_recent,
    load_active_historical_publication,
)
from grocery.historical_view_helpers import (
    active_recent_entry,
    fixed_parameter_error,
    historical_base_context,
    historical_server_error,
    section_navigation,
    validation_errors,
    with_publication_headers,
)
from grocery.observability import log_event

_LOGGER: Final = logging.getLogger("grocery.audit")


@require_safe
def history(request: HttpRequest, series_id: uuid.UUID) -> HttpResponse:
    recent = None
    historical = None
    try:
        recent, entry = active_recent_entry(series_id)
        form = HistoryForm(request.GET if request.GET else None)
        form_invalid = form.is_bound and not form.is_valid()
        historical = load_active_historical_publication()
        context = historical_base_context(entry, series_id, current="history")
        if historical is None:
            if form_invalid:
                return fixed_parameter_error(request, recent, None)
            context["history_state"] = "unavailable"
            return with_publication_headers(
                render(request, "grocery/history.html", context), recent, None
            )
        historical_series = historical_series_for_recent(historical, series_id)
        if historical_series is None:
            if form_invalid:
                return fixed_parameter_error(request, recent, historical)
            context["history_state"] = "unavailable"
            return with_publication_headers(
                render(request, "grocery/history.html", context), recent, historical
            )
        context.update(
            {
                "series": historical_series_context(historical_series),
                "section_nav": section_navigation(series_id, current="history", historical=True),
                "historical_publication": historical_publication_context(historical),
                "history_form_action": reverse("grocery:history", kwargs={"series_id": series_id}),
            }
        )
        if form_invalid:
            context.update(
                history_context(
                    historical,
                    historical_series,
                    selected_region_id=None,
                    selected_range=36,
                )
            )
            context.update(
                {
                    "history_state": "validation",
                    "region_error": "region" in form.errors,
                    "range_error": "range" in form.errors,
                    "validation_errors": validation_errors(
                        form, {"region": "history-region", "range": "history-range"}
                    ),
                }
            )
            return with_publication_headers(
                render(request, "grocery/history.html", context, status=400), recent, historical
            )
        cleaned = form.cleaned_data if form.is_bound else {}
        selected_region_id = cleaned.get("region")
        selected_range = int(cleaned.get("range", "36"))
        try:
            context.update(
                history_context(
                    historical,
                    historical_series,
                    selected_region_id=selected_region_id,
                    selected_range=selected_range,
                )
            )
        except PublicParameterError:
            safe_context = history_context(
                historical, historical_series, selected_region_id=None, selected_range=36
            )
            context.update(safe_context)
            region_options = cast(list[dict[str, object]], safe_context["region_options"])
            valid_regions = {str(option["value"]) for option in region_options}
            range_error = selected_region_id is None or (
                str(selected_region_id) in valid_regions and selected_range == 60
            )
            context.update(
                {
                    "history_state": "validation",
                    "range_error": range_error,
                    "region_error": not range_error,
                    "validation_errors": [
                        {
                            "message": (
                                "표시 기간을 확인하세요." if range_error else "지역을 확인하세요."
                            ),
                            "target": "history-range" if range_error else "history-region",
                        }
                    ],
                }
            )
            return with_publication_headers(
                render(request, "grocery/history.html", context, status=400), recent, historical
            )
        context["history_state"] = (
            "selection_required"
            if selected_region_id is None
            else "stale"
            if historical.stale_message
            else "ready"
        )
        return with_publication_headers(
            render(request, "grocery/history.html", context), recent, historical
        )
    except Http404:
        raise
    except DatabaseError, ObjectDoesNotExist, PublicReadIntegrityError, ValidationError:
        log_event(_LOGGER, "ERROR", "public.history.unavailable")
        return historical_server_error(
            request,
            template="grocery/history.html",
            state_name="history_state",
            recent=recent,
            historical=historical,
        )
