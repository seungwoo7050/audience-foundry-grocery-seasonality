"""Fail-closed sealing for the independently reviewed historical retail bundle."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.historical_review_models import HistoricalCollectionReviewDecision


def _set_historical_seal_token(revision_id: uuid.UUID | None) -> None:
    token = "" if revision_id is None else str(revision_id)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('grocery.historical_seal_id', %s, true)",
            [token],
        )


def _month_number(value: str) -> int:
    return int(value[:4]) * 12 + int(value[4:]) - 1


def _month_value(value: int) -> str:
    year, month = divmod(value, 12)
    return f"{year:04d}{month + 1:02d}"


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _reviewed_collection(
    decision: HistoricalCollectionReviewDecision,
    expected_kind: str,
) -> HistoricalSourceCollection:
    if HistoricalCollectionReviewDecision.objects.filter(supersedes_id=decision.id).exists():
        raise ValidationError("Historical publication requires the current review tail.")
    collection = decision.collection
    if (
        decision.decision != HistoricalCollectionReviewDecision.Decision.APPROVE
        or collection.kind != expected_kind
        or collection.state != HistoricalSourceCollection.State.VALIDATED
        or decision.approved_result_sha256 != collection.result_sha256
        or decision.approved_partition_manifest_sha256 != collection.partition_manifest_sha256
    ):
        raise ValidationError("Historical publication requires an exact approved collection.")
    return collection


def _fact_set_payload(
    monthly: Iterable[MonthlyRegionalRetailPrice],
    regional: Iterable[DailyRegionalRetailPrice],
    markets: Iterable[DailyMarketRetailPrice],
) -> dict[str, object]:
    monthly_rows = sorted(
        (
            row.series.series_identity_sha256,
            row.region.region_code,
            row.year_month,
            _decimal(row.provider_mean),
            _decimal(row.provider_low),
            _decimal(row.provider_high),
            row.source_row_sha256,
        )
        for row in monthly
    )
    regional_rows = sorted(
        (
            row.series.series_identity_sha256,
            row.region.region_code,
            row.survey_date.isoformat(),
            _decimal(row.provider_mean),
            _decimal(row.provider_low),
            _decimal(row.provider_high),
            row.source_row_sha256,
        )
        for row in regional
    )
    market_rows = sorted(
        (
            row.series.series_identity_sha256,
            row.region.region_code,
            row.market.market_code,
            row.survey_date.isoformat(),
            _decimal(row.provider_price),
            row.source_row_sha256,
        )
        for row in markets
    )
    return {"monthly": monthly_rows, "regional": regional_rows, "markets": market_rows}


def _validate_completeness(
    *,
    monthly: list[MonthlyRegionalRetailPrice],
    regional: list[DailyRegionalRetailPrice],
    markets: list[DailyMarketRetailPrice],
    month_max: str,
) -> tuple[set[uuid.UUID], set[date]]:
    monthly_series = {row.series_id for row in monthly}
    regional_series = {row.series_id for row in regional}
    market_series = {row.series_id for row in markets}
    if not monthly_series or monthly_series != regional_series or monthly_series != market_series:
        raise ValidationError("Historical source series sets must match exactly.")

    required_months = {
        _month_value(value)
        for value in range(_month_number(month_max) - 35, _month_number(month_max) + 1)
    }
    months_by_series_region: dict[tuple[uuid.UUID, uuid.UUID], set[str]] = defaultdict(set)
    for row in monthly:
        months_by_series_region[(row.series_id, row.region_id)].add(row.year_month)
    if any(
        not any(
            series_id == candidate_series and required_months <= months
            for (candidate_series, _region_id), months in months_by_series_region.items()
        )
        for series_id in monthly_series
    ):
        raise ValidationError(
            "Every historical series requires one complete recent 36-month region."
        )

    regional_keys = {(row.series_id, row.region_id, row.survey_date) for row in regional}
    market_keys = {(row.series_id, row.region_id, row.survey_date) for row in markets}
    shared = regional_keys & market_keys
    if any(not any(key[0] == series_id for key in shared) for series_id in monthly_series):
        raise ValidationError("Every historical series requires a shared regional and market date.")
    return monthly_series, {key[2] for key in shared}


@transaction.atomic
def seal_historical_publication(
    *,
    monthly_review_id: uuid.UUID,
    regional_review_id: uuid.UUID,
    market_review_id: uuid.UUID,
    compatibility_report_sha256: str,
    public_copy_revision: str = HistoricalRetailPublicationRevision.COPY_REVISION,
) -> HistoricalRetailPublicationRevision:
    decisions = {
        decision.id: decision
        for decision in HistoricalCollectionReviewDecision.objects.select_for_update()
        .select_related("collection", "collection__source_configuration")
        .filter(id__in=(monthly_review_id, regional_review_id, market_review_id))
    }
    if len(decisions) != 3:
        raise ValidationError("Historical publication requires three distinct review decisions.")
    monthly_decision = decisions[monthly_review_id]
    regional_decision = decisions[regional_review_id]
    market_decision = decisions[market_review_id]
    monthly_collection = _reviewed_collection(
        monthly_decision, HistoricalSourceCollection.Kind.MONTHLY
    )
    regional_collection = _reviewed_collection(
        regional_decision, HistoricalSourceCollection.Kind.REGIONAL_DAILY
    )
    market_collection = _reviewed_collection(
        market_decision, HistoricalSourceCollection.Kind.MARKET_DAILY
    )
    collections = (monthly_collection, regional_collection, market_collection)
    manifests = {collection.code_manifest_sha256 for collection in collections}
    if len(manifests) != 1:
        raise ValidationError("Historical collections require one reviewed code manifest.")
    if any(
        collection.date_min is not None
        and collection.date_max is not None
        and (collection.date_max - collection.date_min).days > 30
        for collection in (regional_collection, market_collection)
    ):
        raise ValidationError("Historical daily collections must stay within 31 calendar days.")

    monthly = list(
        MonthlyRegionalRetailPrice.objects.select_for_update()
        .filter(collection=monthly_collection)
        .select_related("series", "region")
    )
    regional = list(
        DailyRegionalRetailPrice.objects.select_for_update()
        .filter(collection=regional_collection)
        .select_related("series", "region")
    )
    markets = list(
        DailyMarketRetailPrice.objects.select_for_update()
        .filter(collection=market_collection)
        .select_related("series", "region", "market")
    )
    counts = (len(monthly), len(regional), len(markets))
    if counts != tuple(collection.accepted_row_count for collection in collections):
        raise ValidationError("Historical fact counts must match reconciled collection counts.")
    series_ids, shared_dates = _validate_completeness(
        monthly=monthly,
        regional=regional,
        markets=markets,
        month_max=monthly_collection.month_max,
    )
    code_manifest_sha256 = manifests.pop()
    if any(row.series.code_manifest_sha256 != code_manifest_sha256 for row in monthly):
        raise ValidationError("Historical series identities must match the bundle code manifest.")
    typed_fact_set_sha256 = _canonical_sha256(_fact_set_payload(monthly, regional, markets))
    fields: dict[str, object] = {
        "monthly_review_id": monthly_review_id,
        "regional_review_id": regional_review_id,
        "market_review_id": market_review_id,
        "code_manifest_sha256": code_manifest_sha256,
        "compatibility_report_sha256": compatibility_report_sha256,
        "fact_hash_version": HistoricalRetailPublicationRevision.FACT_HASH_VERSION,
        "typed_fact_set_sha256": typed_fact_set_sha256,
        "series_count": len(series_ids),
        "monthly_fact_count": counts[0],
        "regional_fact_count": counts[1],
        "market_fact_count": counts[2],
        "month_min": monthly_collection.month_min,
        "month_max": monthly_collection.month_max,
        "date_min": min(shared_dates),
        "date_max": max(shared_dates),
        "public_copy_revision": public_copy_revision,
    }
    candidate = HistoricalRetailPublicationRevision(**fields)
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    existing = (
        HistoricalRetailPublicationRevision.objects.select_for_update()
        .filter(
            typed_fact_set_sha256=typed_fact_set_sha256,
            public_copy_revision=public_copy_revision,
        )
        .first()
    )
    if existing is not None:
        if existing.sealed_at is None or any(
            getattr(existing, field_name) != value for field_name, value in fields.items()
        ):
            raise ValidationError("Historical publication replay conflicts with stored evidence.")
        return existing
    candidate._seal_write = True
    _set_historical_seal_token(candidate.id)
    candidate.save()
    if (
        HistoricalRetailPublicationRevision.objects.filter(
            pk=candidate.id, sealed_at__isnull=True
        ).update(sealed_at=timezone.now())
        != 1
    ):
        raise ValidationError("Historical publication seal did not affect one revision.")
    _set_historical_seal_token(None)
    candidate.refresh_from_db()
    return candidate
