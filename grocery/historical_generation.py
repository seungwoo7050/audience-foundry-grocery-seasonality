"""Atomic typed persistence for bounded historical parser results."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.historical_generation_common import (
    historical_configuration_sha256,
    resolve_historical_region,
    resolve_historical_series,
)
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.models import (
    FetchAttempt,
    ParseRun,
    SourceArtifact,
)
from grocery.source.historical_client import PreparedHistoricalRequest
from grocery.source.historical_contract import HistoricalDataset
from grocery.source.historical_parser import ParsedHistoricalResult
from grocery.source.monthly_history import ParsedMonthlyPriceRow

MONTHLY_PARSER_REVISION: Final = "kamis-15156060-v1"
MONTHLY_SOURCE_CONTRACT_REVISION: Final = "data-go-15156060-monthly-v1"


@dataclass(frozen=True, slots=True)
class CompletedHistoricalPart:
    parse_run: ParseRun
    collection: HistoricalSourceCollection
    part: HistoricalSourceCollectionPart
    replayed: bool


def _validate_monthly_scope(
    prepared_request: PreparedHistoricalRequest,
    rows: tuple[ParsedMonthlyPriceRow, ...],
) -> tuple[str, str]:
    conditions = prepared_request.query.conditions
    month_min = conditions["cond[exmn_ym::GTE]"]
    month_max = conditions["cond[exmn_ym::LTE]"]
    filter_fields = {
        "cond[se_cd::EQ]": "product_class_code",
        "cond[ctgry_cd::EQ]": "category_code",
        "cond[item_cd::EQ]": "item_code",
        "cond[vrty_cd::EQ]": "variety_code",
        "cond[grd_cd::EQ]": "grade_code",
    }
    for row in rows:
        month = row.source_effective_month.source_text()
        if not month_min <= month <= month_max:
            raise ValidationError("Historical monthly row is outside its prepared request.")
        if any(
            name in conditions and getattr(row.identity, attribute) != conditions[name]
            for name, attribute in filter_fields.items()
        ):
            raise ValidationError("Historical monthly identity is outside its prepared request.")
        if (
            "cond[sgg_cd::EQ]" in conditions
            and row.region.code != conditions["cond[sgg_cd::EQ]"]
        ):
            raise ValidationError("Historical monthly region is outside its prepared request.")
    return month_min, month_max


@transaction.atomic
def persist_monthly_part(
    *,
    collection_id: uuid.UUID,
    ordinal: int,
    artifact_id: uuid.UUID,
    prepared_request: PreparedHistoricalRequest,
    parsed: ParsedHistoricalResult[ParsedMonthlyPriceRow],
    code_manifest_sha256: str,
) -> CompletedHistoricalPart:
    if prepared_request.query.dataset != HistoricalDataset.MONTHLY:
        raise ValidationError("Monthly persistence requires the monthly source contract.")
    if parsed.input_row_count != len(parsed.rows):
        raise ValidationError("Historical parse rows do not reconcile with the input count.")
    month_min, month_max = _validate_monthly_scope(prepared_request, parsed.rows)
    collection = (
        HistoricalSourceCollection.objects.select_for_update()
        .select_related("source_configuration")
        .get(pk=collection_id)
    )
    source = collection.source_configuration
    if collection.state != HistoricalSourceCollection.State.STARTED:
        raise ValidationError("Historical parts require a started collection.")
    if collection.kind != HistoricalSourceCollection.Kind.MONTHLY:
        raise ValidationError("Monthly persistence requires a monthly collection.")
    if ordinal < 1 or ordinal > collection.expected_part_count:
        raise ValidationError("Historical part ordinal is outside its collection plan.")
    artifact = SourceArtifact.objects.select_for_update().get(pk=artifact_id)
    if not FetchAttempt.objects.select_for_update().filter(
        source_configuration=source,
        artifact=artifact,
        state=FetchAttempt.State.SUCCEEDED,
        request_scope_sha256=prepared_request.scope_sha256,
    ).exists():
        raise ValidationError("Historical artifact does not belong to the prepared source scope.")

    configuration_hash = historical_configuration_sha256(
        dataset=HistoricalDataset.MONTHLY,
        parser_revision=MONTHLY_PARSER_REVISION,
        code_manifest_sha256=code_manifest_sha256,
    )
    parse_run, created = ParseRun.objects.get_or_create(
        artifact=artifact,
        parser_revision=MONTHLY_PARSER_REVISION,
        configuration_hash=configuration_hash,
    )
    if not created:
        if (
            parse_run.status != ParseRun.Status.VALIDATED
            or parse_run.result_hash != parsed.result_hash
        ):
            raise ValidationError("Historical parse replay conflicts with its stored generation.")
        try:
            part = parse_run.historical_collection_part
        except HistoricalSourceCollectionPart.DoesNotExist:
            raise ValidationError(
                "Historical parse replay is missing its collection part."
            ) from None
        if part.collection_id != collection.id or part.ordinal != ordinal:
            raise ValidationError("Historical parse replay belongs to another collection part.")
        return CompletedHistoricalPart(parse_run, collection, part, True)

    if (
        collection.code_manifest_sha256 != code_manifest_sha256
        or collection.month_min != month_min
        or collection.month_max != month_max
    ):
        raise ValidationError("Monthly part does not match its planned collection.")
    parse_run.status = ParseRun.Status.VALIDATED
    parse_run.completed_at = timezone.now()
    parse_run.result_hash = parsed.result_hash
    parse_run.total_row_count = parsed.input_row_count
    parse_run.accepted_row_count = len(parsed.rows)
    parse_run.save()
    part = HistoricalSourceCollectionPart.objects.create(
        collection=collection,
        ordinal=1,
        partition_scope_sha256=prepared_request.scope_sha256,
        parse_run=parse_run,
        fact_count=len(parsed.rows),
    )
    for row in parsed.rows:
        MonthlyRegionalRetailPrice.objects.create(
            collection=collection,
            collection_part=part,
            series=resolve_historical_series(
                row.identity, code_manifest_sha256=code_manifest_sha256
            ),
            region=resolve_historical_region(row.region),
            year_month=row.source_effective_month.source_text(),
            provider_mean=row.pmm_avgprc,
            provider_low=row.pmm_lwprc,
            provider_high=row.pmm_hgprc,
            source_row_sha256=row.source_row_hash,
            source_contract_revision=MONTHLY_SOURCE_CONTRACT_REVISION,
        )
    return CompletedHistoricalPart(parse_run, collection, part, False)
