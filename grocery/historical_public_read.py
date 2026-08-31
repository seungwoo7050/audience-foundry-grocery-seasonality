"""Active historical publication and exact recent-series membership boundary."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.historical_activation_models import HistoricalRetailPublicationChannel
from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_identity_models import HistoricalRetailSeriesKey
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.presentation import format_korean_datetime, format_unit

HISTORICAL_RETAIL_CHANNEL: Final = "HISTORICAL_RETAIL"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_KIND_REVIEW_FIELDS: Final = {
    HistoricalSourceCollection.Kind.MONTHLY: "monthly_review",
    HistoricalSourceCollection.Kind.REGIONAL_DAILY: "regional_review",
    HistoricalSourceCollection.Kind.MARKET_DAILY: "market_review",
}


class PublicReadIntegrityError(ValidationError):
    """An active publication cannot be presented without inventing facts."""


class PublicParameterError(ValidationError):
    """A syntactically valid public value is not in the active allowlist."""


@dataclass(frozen=True, slots=True)
class ActiveHistoricalPublication:
    revision: HistoricalRetailPublicationRevision
    checked_at: datetime
    freshness_state: str
    freshness_label: str
    stale_message: str

    @property
    def monthly_collection(self) -> HistoricalSourceCollection:
        return self.revision.monthly_review.collection

    @property
    def regional_collection(self) -> HistoricalSourceCollection:
        return self.revision.regional_review.collection

    @property
    def market_collection(self) -> HistoricalSourceCollection:
        return self.revision.market_review.collection


def load_active_historical_publication(
    *, observed_at: datetime | None = None
) -> ActiveHistoricalPublication | None:
    """Load and validate the historical pointer independently of recent retail."""

    channel = (
        HistoricalRetailPublicationChannel.objects.select_related(
            "current_revision__monthly_review__collection__source_configuration",
            "current_revision__regional_review__collection__source_configuration",
            "current_revision__market_review__collection__source_configuration",
        )
        .filter(pk=HISTORICAL_RETAIL_CHANNEL)
        .first()
    )
    if channel is None or channel.current_revision is None:
        return None
    revision = channel.current_revision
    if (
        revision.sealed_at is None
        or revision.public_copy_revision != HistoricalRetailPublicationRevision.COPY_REVISION
        or not _SHA256_RE.fullmatch(revision.typed_fact_set_sha256)
    ):
        raise PublicReadIntegrityError("The historical pointer is not a sealed public revision.")

    collections: list[HistoricalSourceCollection] = []
    for expected_kind, review_field in _KIND_REVIEW_FIELDS.items():
        decision = getattr(revision, review_field)
        collection = decision.collection
        if (
            decision.decision != HistoricalCollectionReviewDecision.Decision.APPROVE
            or collection.kind != expected_kind
            or collection.state != HistoricalSourceCollection.State.VALIDATED
            or collection.completed_at is None
            or collection.code_manifest_sha256 != revision.code_manifest_sha256
            or decision.approved_result_sha256 != collection.result_sha256
            or decision.approved_partition_manifest_sha256 != collection.partition_manifest_sha256
        ):
            raise PublicReadIntegrityError(
                "The historical revision has an invalid reviewed source."
            )
        collections.append(collection)

    now = observed_at or timezone.now()
    monthly, regional, market = collections
    monthly_completed = cast(datetime, monthly.completed_at)
    regional_completed = cast(datetime, regional.completed_at)
    market_completed = cast(datetime, market.completed_at)
    monthly_age = timedelta(hours=settings.KAMIS_HISTORICAL_MONTHLY_MAX_AGE_HOURS)
    daily_age = timedelta(hours=settings.KAMIS_HISTORICAL_DAILY_MAX_AGE_HOURS)
    stale = bool(
        now - monthly_completed > monthly_age
        or now - regional_completed > daily_age
        or now - market_completed > daily_age
    )
    completed_by_source = {
        collection.source_configuration_id: cast(datetime, collection.completed_at)
        for collection in collections
    }
    newer_outcome_exists = any(
        completed_at is not None and completed_at > completed_by_source[source_configuration_id]
        for source_configuration_id, completed_at in HistoricalSourceCollection.objects.filter(
            source_configuration_id__in=completed_by_source,
            completed_at__isnull=False,
        )
        .exclude(id__in={collection.id for collection in collections})
        .values_list("source_configuration_id", "completed_at")
    )
    stale = stale or newer_outcome_exists
    checked_at = min(monthly_completed, regional_completed, market_completed)
    if stale:
        return ActiveHistoricalPublication(
            revision=revision,
            checked_at=checked_at,
            freshness_state="stale",
            freshness_label="마지막 공개 자료 · 최근 확인 필요",
            stale_message=(
                "최근 자료 확인이 필요합니다. 마지막으로 검토를 마친 조사값을 표시합니다."
            ),
        )
    return ActiveHistoricalPublication(
        revision=revision,
        checked_at=checked_at,
        freshness_state="current",
        freshness_label="KAMIS 자료 확인 완료",
        stale_message="",
    )


def historical_publication_context(active: ActiveHistoricalPublication) -> dict[str, str]:
    return {
        "checked_at_iso": active.checked_at.isoformat(),
        "checked_at_display": format_korean_datetime(active.checked_at),
        "freshness_state": active.freshness_state,
        "freshness_label": active.freshness_label,
    }


def historical_series_for_recent(
    active: ActiveHistoricalPublication, recent_series_id: uuid.UUID
) -> HistoricalRetailSeriesKey | None:
    series = (
        HistoricalRetailSeriesKey.objects.select_related("recent_series")
        .filter(recent_series_id=recent_series_id)
        .first()
    )
    if series is None:
        return None
    if series.code_manifest_sha256 != active.revision.code_manifest_sha256:
        raise PublicReadIntegrityError("Historical series identity uses a different code manifest.")
    memberships = (
        MonthlyRegionalRetailPrice.objects.filter(
            collection=active.monthly_collection, series=series
        ).exists(),
        DailyRegionalRetailPrice.objects.filter(
            collection=active.regional_collection, series=series
        ).exists(),
        DailyMarketRetailPrice.objects.filter(
            collection=active.market_collection, series=series
        ).exists(),
    )
    if len(set(memberships)) != 1:
        raise PublicReadIntegrityError("Historical series membership is incomplete.")
    if not all(memberships):
        return None
    return series


def historical_series_context(series: HistoricalRetailSeriesKey) -> dict[str, str]:
    recent = series.recent_series
    return {
        "category_label": recent.category_name,
        "item_name": recent.item_name,
        "variety_name": recent.variety_name,
        "grade_name": recent.grade_name,
        "unit_label": format_unit(recent.raw_unit, recent.raw_unit_size),
    }
