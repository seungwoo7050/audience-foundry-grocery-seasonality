"""Read-only presentation data from the active recent-retail publication."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Prefetch
from django.utils import timezone

from grocery.models import (
    FetchAttempt,
    PriceChangeFact,
    PublicationChannel,
    PublicationEntry,
    PublicationRevision,
    ReferencePrice,
)
from grocery.presentation import (
    comparison_microbar,
    direction_label,
    format_absolute_krw,
    format_korean_date,
    format_korean_datetime,
    format_krw,
    format_signed_percentage,
    format_unit,
)

RECENT_RETAIL_CHANNEL: Final = "RECENT_RETAIL"
PUBLIC_RESULT_LIMIT: Final = 100
PUBLIC_PAGE_SIZE: Final = 30
KAMIS_LANDING_URL: Final = "https://www.data.go.kr/data/15156063/openapi.do"

_CATEGORY_CODES: Final = {"vegetable": "200", "fruit": "400"}
_PERIOD_LABELS: Final = {
    "WEEK": "1주 전 제공값",
    "MONTH": "1개월 전 제공값",
    "YEAR": "1년 전 제공값",
}
_PERIOD_ORDER: Final = {"WEEK": 1, "MONTH": 2, "YEAR": 3}
_PUBLIC_PERIODS: Final = {"week": "WEEK", "month": "MONTH", "year": "YEAR"}
_PUBLIC_DIRECTIONS: Final = {
    "lower": PriceChangeFact.Direction.LOWER,
    "equal": PriceChangeFact.Direction.EQUAL,
    "higher": PriceChangeFact.Direction.HIGHER,
    "unavailable": PriceChangeFact.Direction.UNAVAILABLE,
}
_COVERAGE_LABELS: Final = {
    "KAMIS_RETAIL_ALL_REGIONS_22_CITIES_V1": "KAMIS 소매 조사 22개 도시 지역 전체 집계",
}


@dataclass(frozen=True, slots=True)
class ActivePublication:
    revision: PublicationRevision
    checked_at: datetime
    freshness_state: str
    freshness_label: str
    stale_message: str


def load_active_publication(*, observed_at: datetime | None = None) -> ActivePublication | None:
    """Load the only public pointer and derive operational freshness separately."""

    channel = (
        PublicationChannel.objects.select_related(
            "current_revision__generation__artifact",
            "current_revision__review_decision__source_configuration",
        )
        .filter(pk=RECENT_RETAIL_CHANNEL)
        .first()
    )
    if channel is None or channel.current_revision is None:
        return None

    revision = channel.current_revision
    if revision.sealed_at is None or revision.channel != RECENT_RETAIL_CHANNEL:
        raise ValidationError("The active publication pointer is not a sealed recent revision.")

    source = revision.review_decision.source_configuration
    artifact = revision.generation.artifact
    confirmed_attempt = (
        FetchAttempt.objects.filter(
            source_configuration=source,
            artifact=artifact,
            state=FetchAttempt.State.SUCCEEDED,
        )
        .order_by("-completed_at", "-started_at")
        .first()
    )
    if confirmed_attempt is None or confirmed_attempt.completed_at is None:
        raise ValidationError("The active publication has no completed source confirmation.")

    latest_attempt = (
        FetchAttempt.objects.filter(source_configuration=source)
        .exclude(state=FetchAttempt.State.STARTED)
        .order_by("-completed_at", "-started_at")
        .first()
    )
    now = observed_at or timezone.now()
    max_age = timedelta(hours=settings.KAMIS_CONFIRMATION_MAX_AGE_HOURS)
    newer_content_waits_for_review = bool(
        latest_attempt is not None
        and latest_attempt.state == FetchAttempt.State.SUCCEEDED
        and latest_attempt.artifact_id != artifact.id
    )
    newer_attempt_failed = bool(
        latest_attempt is not None
        and latest_attempt.state != FetchAttempt.State.SUCCEEDED
        and latest_attempt.completed_at is not None
        and latest_attempt.completed_at > confirmed_attempt.completed_at
    )
    confirmation_is_old = now - confirmed_attempt.completed_at > max_age
    stale = newer_content_waits_for_review or newer_attempt_failed or confirmation_is_old

    if stale:
        return ActivePublication(
            revision=revision,
            checked_at=confirmed_attempt.completed_at,
            freshness_state="stale",
            freshness_label="마지막 공개 자료 · 최근 확인 필요",
            stale_message=(
                "최근 자료 확인이 필요합니다. 마지막으로 검토를 마친 조사값을 표시합니다."
            ),
        )
    return ActivePublication(
        revision=revision,
        checked_at=confirmed_attempt.completed_at,
        freshness_state="current",
        freshness_label="KAMIS 자료 확인 완료",
        stale_message="",
    )


def publication_context(active: ActivePublication) -> dict[str, str]:
    """Expose one publication-level freshness summary without row repetition."""

    return {
        "checked_at_iso": active.checked_at.isoformat(),
        "checked_at_display": format_korean_datetime(active.checked_at),
        "freshness_state": active.freshness_state,
        "freshness_label": active.freshness_label,
    }


def publication_entries(
    active: ActivePublication,
    *,
    query: str,
    category: str,
    period: str = "week",
    direction: str = "all",
    sort: str = "name",
) -> list[PublicationEntry]:
    try:
        selected_period = _PUBLIC_PERIODS[period]
    except KeyError as error:
        raise ValidationError("Unknown public comparison period.") from error
    if direction != "all" and direction not in _PUBLIC_DIRECTIONS:
        raise ValidationError("Unknown public comparison direction.")
    if sort not in {"name", "change_asc", "change_desc"}:
        raise ValidationError("Unknown public catalog sort.")
    if category and category not in _CATEGORY_CODES:
        raise ValidationError("Unknown public category.")

    catalog_references = ReferencePrice.objects.filter(period=selected_period).select_related(
        "change_fact"
    )
    comparison_attribute = (
        "catalog_week_references"
        if selected_period == ReferencePrice.Period.WEEK
        else "catalog_comparison_references"
    )
    entries = active.revision.entries.select_related("snapshot__series").prefetch_related(
        Prefetch(
            "snapshot__reference_prices",
            queryset=catalog_references,
            to_attr=comparison_attribute,
        )
    )
    entries = entries.order_by(
        "snapshot__series__category_code",
        "snapshot__series__item_name",
        "snapshot__series__item_code",
        "snapshot__series__variety_code",
        "snapshot__series__grade_code",
        "snapshot__series__raw_unit",
        "snapshot__series__raw_unit_size",
        "snapshot__series_id",
    )
    materialized = list(entries[: PUBLIC_RESULT_LIMIT + 1])
    if len(materialized) > PUBLIC_RESULT_LIMIT:
        raise ValidationError("The active catalog exceeds its public result limit.")
    return _filter_and_sort_catalog_entries(
        materialized,
        query=query,
        category=category,
        direction=direction,
        sort=sort,
    )


def catalog_item(
    entry: PublicationEntry,
    active: ActivePublication,
    *,
    url: str,
) -> dict[str, object]:
    snapshot = entry.snapshot
    series = snapshot.series
    return {
        "url": url,
        "category_label": series.category_name,
        "item_name": series.item_name,
        "variety_name": series.variety_name,
        "grade_name": series.grade_name,
        "unit_label": format_unit(series.raw_unit, series.raw_unit_size),
        "current_price_machine": format(snapshot.current_price, "f"),
        "current_price_label": format_krw(snapshot.current_price),
        "source_date_iso": snapshot.source_effective_date.isoformat(),
        "source_date_label": format_korean_date(snapshot.source_effective_date),
        "freshness_state": active.freshness_state,
        "freshness_label": active.freshness_label,
        "comparison": _catalog_comparison(snapshot),
        # Kept for the Phase 0 template until the vNext frontend commit lands.
        "week_comparison": _catalog_comparison(snapshot),
    }


def publication_entries_for_series(
    active: ActivePublication, series_ids: Sequence[uuid.UUID]
) -> list[PublicationEntry]:
    """Materialize at most five selected members with one bounded reference prefetch."""

    if len(series_ids) > 5:
        raise ValidationError("A public selection cannot exceed five series.")
    if not series_ids:
        return []
    week_references = ReferencePrice.objects.filter(
        period=ReferencePrice.Period.WEEK
    ).select_related("change_fact")
    entries = list(
        active.revision.entries.select_related("snapshot__series")
        .prefetch_related(
            Prefetch(
                "snapshot__reference_prices",
                queryset=week_references,
                to_attr="catalog_comparison_references",
            )
        )
        .filter(snapshot__series_id__in=series_ids)
    )
    by_series = {entry.snapshot.series_id: entry for entry in entries}
    return [by_series[series_id] for series_id in series_ids if series_id in by_series]


def publication_candidate_entries(
    active: ActivePublication, *, excluded_series_ids: Sequence[uuid.UUID]
) -> list[PublicationEntry]:
    """Return only current publication members for the no-JS add control."""

    return list(
        active.revision.entries.select_related("snapshot__series")
        .exclude(snapshot__series_id__in=excluded_series_ids)
        .order_by(
            "snapshot__series__category_code",
            "snapshot__series__item_name",
            "snapshot__series__item_code",
            "snapshot__series__variety_code",
            "snapshot__series__grade_code",
            "snapshot__series__raw_unit",
            "snapshot__series__raw_unit_size",
            "snapshot__series_id",
        )[:PUBLIC_RESULT_LIMIT]
    )


def selection_item_context(
    entry: PublicationEntry,
    active: ActivePublication,
    *,
    detail_url: str,
    remove_url: str,
) -> dict[str, object]:
    item = catalog_item(entry, active, url=detail_url)
    item.update(
        {
            "series_value": str(entry.snapshot.series_id),
            "detail_url": detail_url,
            "remove_url": remove_url,
        }
    )
    return item


def selection_candidate_context(entry: PublicationEntry) -> dict[str, str]:
    series = entry.snapshot.series
    return {
        "value": str(series.id),
        "label": (
            f"{series.item_name} · {series.variety_name} · {series.grade_name} · "
            f"{format_unit(series.raw_unit, series.raw_unit_size)}"
        ),
    }


def detail_context(entry: PublicationEntry, active: ActivePublication) -> dict[str, object]:
    snapshot = entry.snapshot
    series = snapshot.series
    references = list(snapshot.reference_prices.select_related("change_fact"))
    if len(references) != 3 or {reference.period for reference in references} != set(_PERIOD_ORDER):
        raise ValidationError("Published detail requires exact WEEK, MONTH, and YEAR references.")
    references.sort(key=lambda reference: _PERIOD_ORDER[reference.period])

    source = active.revision.review_decision.source_configuration
    coverage_label = _COVERAGE_LABELS.get(series.coverage_identity)
    if coverage_label is None:
        raise ValidationError("Published detail has an unknown coverage identity.")

    publication = publication_context(active)
    return {
        "series": {
            "category_label": series.category_name,
            "item_name": series.item_name,
            "variety_name": series.variety_name,
            "grade_name": series.grade_name,
            "unit_label": format_unit(series.raw_unit, series.raw_unit_size),
            "current_price_machine": format(snapshot.current_price, "f"),
            "current_price_label": format_krw(snapshot.current_price),
        },
        "comparisons": [_comparison_context(reference) for reference in references],
        "provenance": {
            "source_name": f"{source.source_owner_name} KAMIS 최근일자 도·소매가격정보",
            "source_url": KAMIS_LANDING_URL,
            "dataset_id": source.dataset_id,
            "source_date_iso": snapshot.source_effective_date.isoformat(),
            "source_date_label": format_korean_date(snapshot.source_effective_date),
            "coverage_label": coverage_label,
            "checked_at_iso": publication["checked_at_iso"],
            "checked_at_display": publication["checked_at_display"],
            "reviewed_at_iso": active.revision.review_decision.decided_at.isoformat(),
            "reviewed_at_label": format_korean_datetime(active.revision.review_decision.decided_at),
            "freshness_state": publication["freshness_state"],
            "freshness_label": publication["freshness_label"],
        },
    }


def _catalog_comparison(snapshot: object) -> dict[str, object]:
    return _comparison_context(_catalog_reference(snapshot))


def _catalog_reference(snapshot: object) -> ReferencePrice:
    references = getattr(snapshot, "catalog_comparison_references", None)
    if references is None:
        references = getattr(snapshot, "catalog_week_references", None)
    if not isinstance(references, list) or len(references) != 1:
        raise ValidationError("Published catalog requires exactly one selected reference.")
    reference = references[0]
    if not isinstance(reference, ReferencePrice) or reference.period not in _PERIOD_ORDER:
        raise ValidationError("Published catalog reference is malformed.")
    return reference


def _filter_and_sort_catalog_entries(
    entries: Sequence[PublicationEntry],
    *,
    query: str = "",
    category: str = "",
    direction: str,
    sort: str,
) -> list[PublicationEntry]:
    validated: list[tuple[PublicationEntry, ReferencePrice]] = []
    for entry in entries:
        reference = _catalog_reference(entry.snapshot)
        _comparison_context(reference)
        validated.append((entry, reference))
    if category:
        category_code = _CATEGORY_CODES[category]
        validated = [
            (entry, reference)
            for entry, reference in validated
            if entry.snapshot.series.category_code == category_code
        ]
    if query:
        folded_query = query.casefold()
        validated = [
            (entry, reference)
            for entry, reference in validated
            if folded_query in entry.snapshot.series.item_name.casefold()
        ]
    if direction != "all":
        expected = _PUBLIC_DIRECTIONS[direction]
        validated = [
            (entry, reference)
            for entry, reference in validated
            if reference.change_fact.direction == expected
        ]
    if sort == "name":
        return [entry for entry, _reference in validated]

    descending = sort == "change_desc"

    def comparison_key(
        value: tuple[PublicationEntry, ReferencePrice],
    ) -> tuple[bool, Decimal, tuple[object, ...]]:
        entry, reference = value
        change = reference.change_fact
        unavailable = change.direction == PriceChangeFact.Direction.UNAVAILABLE
        percentage = change.signed_percentage or Decimal("0")
        if descending:
            percentage = -percentage
        series = entry.snapshot.series
        identity = (
            series.category_code,
            series.item_name,
            series.item_code,
            series.variety_code,
            series.grade_code,
            series.raw_unit,
            series.raw_unit_size,
            str(series.id),
        )
        return unavailable, percentage, identity

    validated.sort(key=comparison_key)
    return [entry for entry, _reference in validated]


def _comparison_context(reference: ReferencePrice) -> dict[str, object]:
    try:
        period_label = _PERIOD_LABELS[reference.period]
    except KeyError as error:
        raise ValidationError("Published comparison period is unknown.") from error

    if (
        reference.reference_date_status
        != ReferencePrice.ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
        or reference.source_reference_date is not None
    ):
        raise ValidationError("Published comparison reference date is malformed.")

    try:
        change = reference.change_fact
    except ObjectDoesNotExist as error:
        raise ValidationError("Published comparison has no change fact.") from error

    base: dict[str, object] = {
        "period_label": period_label,
        "reference_date_display": "",
        "reference_date_unavailable": True,
    }
    if reference.value_status == ReferencePrice.ValueStatus.UNAVAILABLE:
        if (
            reference.value is not None
            or reference.unavailable_reason != ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING
            or change.direction != PriceChangeFact.Direction.UNAVAILABLE
            or change.signed_difference is not None
            or change.signed_percentage is not None
        ):
            raise ValidationError("Published unavailable comparison is malformed.")
        base.update(
            {
                "available": False,
                "reference_price_display": "",
                "difference_display": "",
                "percentage_display": "",
                "direction_code": PriceChangeFact.Direction.UNAVAILABLE,
                "direction_label": direction_label(PriceChangeFact.Direction.UNAVAILABLE),
                "unavailable_reason": "KAMIS가 이 기간의 비교값을 제공하지 않았습니다.",
                "microbar": None,
            }
        )
        return base

    if reference.value_status != ReferencePrice.ValueStatus.AVAILABLE:
        raise ValidationError("Published comparison value state is unknown.")
    change = reference.change_fact
    if (
        reference.value is None
        or reference.unavailable_reason is not None
        or change.signed_difference is None
        or change.signed_percentage is None
    ):
        raise ValidationError("An available reference requires a complete change fact.")

    try:
        if (
            change.direction == PriceChangeFact.Direction.LOWER
            and not (change.signed_difference < 0 and change.signed_percentage < 0)
            or change.direction == PriceChangeFact.Direction.EQUAL
            and not (change.signed_difference == 0 and change.signed_percentage == 0)
            or change.direction == PriceChangeFact.Direction.HIGHER
            and not (change.signed_difference > 0 and change.signed_percentage > 0)
            or change.direction
            not in {
                PriceChangeFact.Direction.LOWER,
                PriceChangeFact.Direction.EQUAL,
                PriceChangeFact.Direction.HIGHER,
            }
        ):
            raise ValidationError("Published comparison direction is malformed.")

        reference_price_display = format_krw(reference.value)
        difference_display = format_absolute_krw(change.signed_difference)
        percentage_display = format_signed_percentage(change.signed_percentage)
        display_direction = direction_label(change.direction)
        microbar = comparison_microbar(change.signed_percentage, change.direction)
    except (ArithmeticError, ValueError) as error:
        raise ValidationError("Published comparison numeric values are malformed.") from error

    base.update(
        {
            "available": True,
            "reference_price_display": reference_price_display,
            "difference_display": difference_display,
            "percentage_display": percentage_display,
            "direction_code": change.direction,
            "direction_label": display_direction,
            "unavailable_reason": "",
            "microbar": microbar,
        }
    )
    return base
