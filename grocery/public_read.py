"""Read-only presentation data from the active recent-retail publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Prefetch, QuerySet
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
) -> QuerySet[PublicationEntry]:
    catalog_week_references = ReferencePrice.objects.filter(
        period=ReferencePrice.Period.WEEK
    ).select_related("change_fact")
    entries = (
        active.revision.entries.select_related("snapshot__series")
        .prefetch_related(
            Prefetch(
                "snapshot__reference_prices",
                queryset=catalog_week_references,
                to_attr="catalog_week_references",
            )
        )
        .order_by(
            "snapshot__series__category_code",
            "snapshot__series__item_name",
            "snapshot__series__item_code",
            "snapshot__series__variety_code",
            "snapshot__series__grade_code",
            "snapshot__series__raw_unit",
            "snapshot__series__raw_unit_size",
        )
    )
    if category:
        entries = entries.filter(snapshot__series__category_code=_CATEGORY_CODES[category])
    if query:
        entries = entries.filter(snapshot__series__item_name__icontains=query)
    return entries[:PUBLIC_RESULT_LIMIT]


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
        "current_price_label": format_krw(snapshot.current_price),
        "source_date_iso": snapshot.source_effective_date.isoformat(),
        "source_date_label": format_korean_date(snapshot.source_effective_date),
        "freshness_state": active.freshness_state,
        "freshness_label": active.freshness_label,
        "week_comparison": _catalog_week_comparison(snapshot),
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


def _catalog_week_comparison(snapshot: object) -> dict[str, object]:
    references = getattr(snapshot, "catalog_week_references", None)
    if not isinstance(references, list) or len(references) != 1:
        raise ValidationError("Published catalog requires exactly one WEEK reference.")
    reference = references[0]
    if not isinstance(reference, ReferencePrice) or reference.period != ReferencePrice.Period.WEEK:
        raise ValidationError("Published catalog WEEK reference is malformed.")
    return _comparison_context(reference)


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
