"""Read-only presentation data from the active recent-retail publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils import timezone

from grocery.models import (
    FetchAttempt,
    PublicationChannel,
    PublicationEntry,
    PublicationRevision,
    ReferencePrice,
)
from grocery.presentation import (
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
KAMIS_LANDING_URL: Final = "https://www.data.go.kr/data/15156063/openapi.do"

_CATEGORY_CODES: Final = {"vegetable": "200", "fruit": "400"}
_PERIOD_LABELS: Final = {
    "WEEK": "1주 전 제공값",
    "MONTH": "1개월 전 제공값",
    "YEAR": "1년 전 제공값",
}
_PERIOD_ORDER: Final = {"WEEK": 1, "MONTH": 2, "YEAR": 3}
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
            freshness_label="마지막 검토 자료 · 새 확인 필요",
            stale_message=(
                "아래에는 마지막으로 검토해 공개한 조사값을 표시합니다. "
                "새 수집 또는 검토 상태를 운영자가 확인하고 있습니다."
            ),
        )
    return ActivePublication(
        revision=revision,
        checked_at=confirmed_attempt.completed_at,
        freshness_state="current",
        freshness_label="마지막 source 확인 완료",
        stale_message="",
    )


def publication_entries(
    active: ActivePublication,
    *,
    query: str,
    category: str,
) -> QuerySet[PublicationEntry]:
    entries = active.revision.entries.select_related("snapshot__series").order_by(
        "snapshot__series__category_code",
        "snapshot__series__item_name",
        "snapshot__series__item_code",
        "snapshot__series__variety_code",
        "snapshot__series__grade_code",
        "snapshot__series__raw_unit",
        "snapshot__series__raw_unit_size",
    )
    if category:
        entries = entries.filter(snapshot__series__category_code=_CATEGORY_CODES[category])
    if query:
        entries = entries.filter(snapshot__series__item_name__icontains=query)
    return entries[:PUBLIC_RESULT_LIMIT]


def catalog_item(entry: PublicationEntry, active: ActivePublication, *, url: str) -> dict[str, str]:
    snapshot = entry.snapshot
    series = snapshot.series
    return {
        "url": url,
        "category_label": series.category_name,
        "item_name": series.item_name,
        "variety_name": series.variety_name,
        "grade_name": series.grade_name,
        "unit_label": format_unit(series.raw_unit, series.raw_unit_size),
        "current_price_label": format_krw(snapshot.current_price),
        "source_date_iso": snapshot.source_effective_date.isoformat(),
        "source_date_label": format_korean_date(snapshot.source_effective_date),
        "freshness_state": active.freshness_state,
        "freshness_label": active.freshness_label,
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
            "checked_at_iso": active.checked_at.isoformat(),
            "checked_at_label": format_korean_datetime(active.checked_at),
            "reviewed_at_iso": active.revision.review_decision.decided_at.isoformat(),
            "reviewed_at_label": format_korean_datetime(active.revision.review_decision.decided_at),
            "freshness_state": active.freshness_state,
            "freshness_label": active.freshness_label,
        },
    }


def _comparison_context(reference: ReferencePrice) -> dict[str, object]:
    base: dict[str, object] = {
        "period_label": _PERIOD_LABELS[reference.period],
        "reference_date_available": reference.source_reference_date is not None,
        "reference_date_iso": (
            reference.source_reference_date.isoformat()
            if reference.source_reference_date is not None
            else ""
        ),
        "reference_date_label": (
            format_korean_date(reference.source_reference_date)
            if reference.source_reference_date is not None
            else ""
        ),
    }
    if reference.value_status == ReferencePrice.ValueStatus.UNAVAILABLE:
        base.update(
            {
                "available": False,
                "unavailable_reason_label": "source 응답에 비교 제공값이 없습니다.",
            }
        )
        return base

    change = reference.change_fact
    if (
        reference.value is None
        or change.signed_difference is None
        or change.signed_percentage is None
    ):
        raise ValidationError("An available reference requires a complete change fact.")
    base.update(
        {
            "available": True,
            "reference_value_label": format_krw(reference.value),
            "difference_label": format_absolute_krw(change.signed_difference),
            "percentage_label": format_signed_percentage(change.signed_percentage),
            "direction_code": change.direction,
            "direction_label": direction_label(change.direction),
        }
    )
    return base
