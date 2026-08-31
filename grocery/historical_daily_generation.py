"""Typed persistence for regional and market daily historical source parts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from django.core.exceptions import ValidationError
from django.db import transaction

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_generation_common import (
    HistoricalPartState,
    resolve_historical_market,
    resolve_historical_region,
    resolve_historical_series,
    start_historical_part,
)
from grocery.source.historical_client import PreparedHistoricalRequest
from grocery.source.historical_contract import HistoricalDataset
from grocery.source.historical_parser import ParsedHistoricalResult
from grocery.source.market_history import ParsedMarketPriceRow
from grocery.source.regional_history import ParsedRegionalPriceRow

REGIONAL_PARSER_REVISION: Final = "kamis-15156062-v1"
REGIONAL_SOURCE_CONTRACT_REVISION: Final = "data-go-15156062-regional-v1"
MARKET_PARSER_REVISION: Final = "kamis-15156065-v1"
MARKET_SOURCE_CONTRACT_REVISION: Final = "data-go-15156065-market-v1"


def _validate_regional_scope(
    prepared_request: PreparedHistoricalRequest,
    rows: tuple[ParsedRegionalPriceRow, ...],
) -> tuple[str, str]:
    conditions = prepared_request.query.conditions
    date_min = conditions["cond[exmn_ymd::GTE]"]
    date_max = conditions["cond[exmn_ymd::LTE]"]
    filters = {
        "cond[se_cd::EQ]": "product_class_code",
        "cond[ctgry_cd::EQ]": "category_code",
        "cond[item_cd::EQ]": "item_code",
        "cond[vrty_cd::EQ]": "variety_code",
        "cond[grd_cd::EQ]": "grade_code",
    }
    for row in rows:
        source_date = row.source_effective_date.strftime("%Y%m%d")
        if not date_min <= source_date <= date_max:
            raise ValidationError("Historical regional row is outside its prepared request.")
        if any(
            name in conditions and getattr(row.identity, attribute) != conditions[name]
            for name, attribute in filters.items()
        ):
            raise ValidationError("Historical regional identity is outside its prepared request.")
        if row.region.code != conditions["cond[sgg_cd::EQ]"]:
            raise ValidationError("Historical regional region is outside its prepared request.")
    return date_min, date_max


@transaction.atomic
def persist_regional_part(
    *,
    collection_id: uuid.UUID,
    ordinal: int,
    artifact_id: uuid.UUID,
    prepared_request: PreparedHistoricalRequest,
    parsed: ParsedHistoricalResult[ParsedRegionalPriceRow],
    code_manifest_sha256: str,
) -> HistoricalPartState:
    if prepared_request.query.dataset != HistoricalDataset.REGIONAL:
        raise ValidationError("Regional persistence requires the regional source contract.")
    date_min, date_max = _validate_regional_scope(prepared_request, parsed.rows)
    collection = HistoricalSourceCollection.objects.select_for_update().get(pk=collection_id)
    if (
        collection.date_min != datetime.strptime(date_min, "%Y%m%d").date()
        or collection.date_max != datetime.strptime(date_max, "%Y%m%d").date()
    ):
        raise ValidationError("Regional part does not match its planned collection window.")
    state = start_historical_part(
        collection_id=collection.id,
        ordinal=ordinal,
        artifact_id=artifact_id,
        prepared_request=prepared_request,
        dataset=HistoricalDataset.REGIONAL,
        collection_kind=HistoricalSourceCollection.Kind.REGIONAL_DAILY,
        parser_revision=REGIONAL_PARSER_REVISION,
        code_manifest_sha256=code_manifest_sha256,
        parsed_result_sha256=parsed.result_hash,
        input_row_count=parsed.input_row_count,
        accepted_row_count=len(parsed.rows),
    )
    if state.replayed:
        return state
    for row in parsed.rows:
        DailyRegionalRetailPrice.objects.create(
            collection=collection,
            collection_part=state.part,
            series=resolve_historical_series(
                row.identity, code_manifest_sha256=code_manifest_sha256
            ),
            region=resolve_historical_region(row.region),
            survey_date=row.source_effective_date,
            provider_mean=row.raw_average_price,
            provider_low=row.raw_min_price,
            provider_high=row.raw_max_price,
            source_row_sha256=row.source_row_hash,
            source_contract_revision=REGIONAL_SOURCE_CONTRACT_REVISION,
        )
    return state


def _validate_market_scope(
    prepared_request: PreparedHistoricalRequest,
    rows: tuple[ParsedMarketPriceRow, ...],
) -> tuple[str, str]:
    conditions = prepared_request.query.conditions
    date_min = conditions["cond[exmn_ymd::GTE]"]
    date_max = conditions["cond[exmn_ymd::LTE]"]
    filters = {
        "cond[ctgry_cd::EQ]": "category_code",
        "cond[item_cd::EQ]": "item_code",
        "cond[vrty_cd::EQ]": "variety_code",
        "cond[grd_cd::EQ]": "grade_code",
    }
    for row in rows:
        source_date = row.source_effective_date.strftime("%Y%m%d")
        if not date_min <= source_date <= date_max:
            raise ValidationError("Historical market row is outside its prepared request.")
        if any(
            name in conditions and getattr(row.identity, attribute) != conditions[name]
            for name, attribute in filters.items()
        ):
            raise ValidationError("Historical market identity is outside its prepared request.")
        if "cond[sgg_cd::EQ]" in conditions and (row.region.code != conditions["cond[sgg_cd::EQ]"]):
            raise ValidationError("Historical market region is outside its prepared request.")
        if "cond[mrkt_cd::EQ]" in conditions and (
            row.market.code != conditions["cond[mrkt_cd::EQ]"]
        ):
            raise ValidationError("Historical market is outside its prepared request.")
    return date_min, date_max


@transaction.atomic
def persist_market_part(
    *,
    collection_id: uuid.UUID,
    ordinal: int,
    artifact_id: uuid.UUID,
    prepared_request: PreparedHistoricalRequest,
    parsed: ParsedHistoricalResult[ParsedMarketPriceRow],
    code_manifest_sha256: str,
) -> HistoricalPartState:
    if prepared_request.query.dataset != HistoricalDataset.MARKET:
        raise ValidationError("Market persistence requires the market source contract.")
    date_min, date_max = _validate_market_scope(prepared_request, parsed.rows)
    collection = HistoricalSourceCollection.objects.select_for_update().get(pk=collection_id)
    if (
        collection.date_min != datetime.strptime(date_min, "%Y%m%d").date()
        or collection.date_max != datetime.strptime(date_max, "%Y%m%d").date()
    ):
        raise ValidationError("Market part does not match its planned collection window.")
    state = start_historical_part(
        collection_id=collection.id,
        ordinal=ordinal,
        artifact_id=artifact_id,
        prepared_request=prepared_request,
        dataset=HistoricalDataset.MARKET,
        collection_kind=HistoricalSourceCollection.Kind.MARKET_DAILY,
        parser_revision=MARKET_PARSER_REVISION,
        code_manifest_sha256=code_manifest_sha256,
        parsed_result_sha256=parsed.result_hash,
        input_row_count=parsed.input_row_count,
        accepted_row_count=len(parsed.rows),
    )
    if state.replayed:
        return state
    for row in parsed.rows:
        region = resolve_historical_region(row.region)
        DailyMarketRetailPrice.objects.create(
            collection=collection,
            collection_part=state.part,
            series=resolve_historical_series(
                row.identity, code_manifest_sha256=code_manifest_sha256
            ),
            region=region,
            market=resolve_historical_market(region, row.market),
            survey_date=row.source_effective_date,
            provider_price=row.raw_observed_price,
            source_row_sha256=row.source_row_hash,
            source_contract_revision=MARKET_SOURCE_CONTRACT_REVISION,
        )
    return state
