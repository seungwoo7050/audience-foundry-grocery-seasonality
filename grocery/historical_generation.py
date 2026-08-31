"""Atomic typed persistence for bounded historical parser results."""

from __future__ import annotations

import uuid
from typing import Final

from django.core.exceptions import ValidationError
from django.db import transaction

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_generation_common import (
    HistoricalPartState,
    resolve_historical_region,
    resolve_historical_series,
    start_historical_part,
)
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.source.historical_client import PreparedHistoricalRequest
from grocery.source.historical_contract import HistoricalDataset
from grocery.source.historical_parser import ParsedHistoricalResult
from grocery.source.monthly_history import ParsedMonthlyPriceRow

MONTHLY_PARSER_REVISION: Final = "kamis-15156060-v1"
MONTHLY_SOURCE_CONTRACT_REVISION: Final = "data-go-15156060-monthly-v1"


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
        if "cond[sgg_cd::EQ]" in conditions and row.region.code != conditions["cond[sgg_cd::EQ]"]:
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
) -> HistoricalPartState:
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
    if collection.kind != HistoricalSourceCollection.Kind.MONTHLY:
        raise ValidationError("Monthly persistence requires a monthly collection.")
    state = start_historical_part(
        collection_id=collection.id,
        ordinal=ordinal,
        artifact_id=artifact_id,
        prepared_request=prepared_request,
        dataset=HistoricalDataset.MONTHLY,
        collection_kind=HistoricalSourceCollection.Kind.MONTHLY,
        parser_revision=MONTHLY_PARSER_REVISION,
        code_manifest_sha256=code_manifest_sha256,
        parsed_result_sha256=parsed.result_hash,
        input_row_count=parsed.input_row_count,
        accepted_row_count=len(parsed.rows),
    )
    if collection.month_min != month_min or collection.month_max != month_max:
        raise ValidationError("Monthly part does not match its planned collection.")
    if state.replayed:
        return state
    for row in parsed.rows:
        MonthlyRegionalRetailPrice.objects.create(
            collection=collection,
            collection_part=state.part,
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
    return state
