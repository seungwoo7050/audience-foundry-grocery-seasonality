"""Canonical hashes for the fixed recent-retail publication fact set."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError

from grocery.models import PriceChangeFact, ReferencePrice, RetailPriceSnapshot

ENTRY_HASH_VERSION = "recent-retail-entry-v1"
FACT_SET_HASH_VERSION = "recent-retail-fact-set-v1"
_PERIOD_ORDER = ("WEEK", "MONTH", "YEAR")


@dataclass(frozen=True, slots=True)
class CanonicalPublicationEntry:
    ordinal: int
    snapshot_id: uuid.UUID
    fact_sha256: str


@dataclass(frozen=True, slots=True)
class CanonicalPublicationFactSet:
    entries: tuple[CanonicalPublicationEntry, ...]
    typed_fact_set_sha256: str
    source_effective_date_min: date
    source_effective_date_max: date


def canonical_snapshot_data(snapshot: RetailPriceSnapshot) -> dict[str, object]:
    """Build the complete, source-bounded public fact for one immutable snapshot."""

    series = snapshot.series
    references = list(snapshot.reference_prices.select_related("change_fact").order_by("period"))
    by_period = {reference.period: reference for reference in references}
    if len(references) != len(_PERIOD_ORDER) or set(by_period) != set(_PERIOD_ORDER):
        raise ValidationError("Publication requires exactly WEEK, MONTH, and YEAR references.")

    return {
        "entry_hash_version": ENTRY_HASH_VERSION,
        "series": {
            "product_class_code": series.product_class_code,
            "product_class_name": series.product_class_name,
            "category_code": series.category_code,
            "category_name": series.category_name,
            "item_code": series.item_code,
            "item_name": series.item_name,
            "variety_code": series.variety_code,
            "variety_name": series.variety_name,
            "grade_code": series.grade_code,
            "grade_name": series.grade_name,
            "raw_unit": series.raw_unit,
            "raw_unit_size": series.raw_unit_size,
            "coverage_identity": series.coverage_identity,
            "identity_evidence_revision": series.identity_evidence_revision,
        },
        "snapshot": {
            "source_effective_date": snapshot.source_effective_date.isoformat(),
            "source_recorded_at": (
                snapshot.source_recorded_at.isoformat()
                if snapshot.source_recorded_at is not None
                else None
            ),
            "current_price": _decimal_text(snapshot.current_price),
            "currency": snapshot.currency,
            "source_row_sha256": snapshot.source_row_sha256,
            "source_contract_revision": snapshot.source_contract_revision,
        },
        "references": [_canonical_reference_data(by_period[period]) for period in _PERIOD_ORDER],
    }


def snapshot_fact_sha256(snapshot: RetailPriceSnapshot) -> str:
    canonical = _canonical_json(canonical_snapshot_data(snapshot))
    return hashlib.sha256(f"{ENTRY_HASH_VERSION}\n".encode("ascii") + canonical).hexdigest()


def build_publication_fact_set(
    snapshots: Sequence[RetailPriceSnapshot],
) -> CanonicalPublicationFactSet:
    if not snapshots:
        raise ValidationError("A publication fact set cannot be empty.")

    parse_run_ids = {snapshot.parse_run_id for snapshot in snapshots}
    snapshot_ids = {snapshot.id for snapshot in snapshots}
    if len(parse_run_ids) != 1:
        raise ValidationError("A publication fact set must use one parse generation.")
    if len(snapshot_ids) != len(snapshots):
        raise ValidationError("A publication fact set cannot repeat a snapshot.")

    ordered = sorted(snapshots, key=_snapshot_order_key)
    entries = tuple(
        CanonicalPublicationEntry(
            ordinal=index,
            snapshot_id=snapshot.id,
            fact_sha256=snapshot_fact_sha256(snapshot),
        )
        for index, snapshot in enumerate(ordered, start=1)
    )
    hash_lines = "\n".join(entry.fact_sha256 for entry in entries)
    set_hash = hashlib.sha256(f"{FACT_SET_HASH_VERSION}\n{hash_lines}".encode("ascii")).hexdigest()
    source_dates = [snapshot.source_effective_date for snapshot in ordered]
    return CanonicalPublicationFactSet(
        entries=entries,
        typed_fact_set_sha256=set_hash,
        source_effective_date_min=min(source_dates),
        source_effective_date_max=max(source_dates),
    )


def _canonical_reference_data(reference: ReferencePrice) -> dict[str, object]:
    try:
        change = reference.change_fact
    except PriceChangeFact.DoesNotExist as error:
        raise ValidationError("Every publication reference requires a change fact.") from error
    return {
        "period": reference.period,
        "value_status": reference.value_status,
        "value": _nullable_decimal_text(reference.value),
        "unavailable_reason": reference.unavailable_reason,
        "reference_date_status": reference.reference_date_status,
        "source_reference_date": (
            reference.source_reference_date.isoformat()
            if reference.source_reference_date is not None
            else None
        ),
        "change": {
            "direction": change.direction,
            "signed_difference": _nullable_decimal_text(change.signed_difference),
            "signed_percentage": _nullable_decimal_text(change.signed_percentage),
            "calculation_revision": change.calculation_revision,
            "rounding_mode": change.rounding_mode,
        },
    }


def _snapshot_order_key(snapshot: RetailPriceSnapshot) -> tuple[str, ...]:
    series = snapshot.series
    return (
        series.product_class_code,
        series.category_code,
        series.item_code,
        series.variety_code,
        series.grade_code,
        series.raw_unit,
        series.raw_unit_size,
        series.coverage_identity,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _nullable_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
