"""Shared exact-identity and configuration checks for historical persistence."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailMarketKey,
    RetailRegionKey,
)
from grocery.models import FetchAttempt, ParseRun, PriceSeriesKey, SourceArtifact
from grocery.source.historical_client import PreparedHistoricalRequest
from grocery.source.historical_contract import HistoricalDataset
from grocery.source.historical_dimensions import MarketObservation, RegionObservation
from grocery.source.kamis import IdentityObservation


@dataclass(frozen=True, slots=True)
class HistoricalPartState:
    parse_run: ParseRun
    collection: HistoricalSourceCollection
    part: HistoricalSourceCollectionPart
    replayed: bool


@transaction.atomic
def start_historical_part(
    *,
    collection_id: uuid.UUID,
    ordinal: int,
    artifact_id: uuid.UUID,
    prepared_request: PreparedHistoricalRequest,
    dataset: HistoricalDataset,
    collection_kind: str,
    parser_revision: str,
    code_manifest_sha256: str,
    parsed_result_sha256: str,
    input_row_count: int,
    accepted_row_count: int,
) -> HistoricalPartState:
    if input_row_count != accepted_row_count:
        raise ValidationError("Historical parser rows do not reconcile with the input count.")
    if prepared_request.query.dataset != dataset:
        raise ValidationError("Historical parser dataset does not match the prepared request.")
    collection = (
        HistoricalSourceCollection.objects.select_for_update()
        .select_related("source_configuration")
        .get(pk=collection_id)
    )
    if (
        collection.kind != collection_kind
        or collection.code_manifest_sha256 != code_manifest_sha256
    ):
        raise ValidationError("Historical part does not match its planned collection.")
    if ordinal < 1 or ordinal > collection.expected_part_count:
        raise ValidationError("Historical part ordinal is outside its collection plan.")
    artifact = SourceArtifact.objects.select_for_update().get(pk=artifact_id)
    if (
        not FetchAttempt.objects.select_for_update()
        .filter(
            source_configuration=collection.source_configuration,
            artifact=artifact,
            state=FetchAttempt.State.SUCCEEDED,
            request_scope_sha256=prepared_request.scope_sha256,
        )
        .exists()
    ):
        raise ValidationError("Historical artifact does not belong to the prepared source scope.")
    parse_run, created = ParseRun.objects.get_or_create(
        artifact=artifact,
        parser_revision=parser_revision,
        configuration_hash=historical_configuration_sha256(
            dataset=dataset,
            parser_revision=parser_revision,
            code_manifest_sha256=code_manifest_sha256,
        ),
    )
    if not created:
        if (
            parse_run.status != ParseRun.Status.VALIDATED
            or parse_run.result_hash != parsed_result_sha256
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
        return HistoricalPartState(parse_run, collection, part, True)
    if collection.state != HistoricalSourceCollection.State.STARTED:
        raise ValidationError("New historical parts require a started collection.")
    parse_run.status = ParseRun.Status.VALIDATED
    parse_run.completed_at = timezone.now()
    parse_run.result_hash = parsed_result_sha256
    parse_run.total_row_count = input_row_count
    parse_run.accepted_row_count = accepted_row_count
    parse_run.save()
    part = HistoricalSourceCollectionPart.objects.create(
        collection=collection,
        ordinal=ordinal,
        partition_scope_sha256=prepared_request.scope_sha256,
        parse_run=parse_run,
        fact_count=accepted_row_count,
    )
    return HistoricalPartState(parse_run, collection, part, False)


def historical_configuration_sha256(
    *,
    dataset: HistoricalDataset,
    parser_revision: str,
    code_manifest_sha256: str,
) -> str:
    canonical = json.dumps(
        {
            "code_manifest_sha256": code_manifest_sha256,
            "dataset": dataset.value,
            "parser_revision": parser_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def resolve_historical_series(
    identity: IdentityObservation,
    *,
    code_manifest_sha256: str,
) -> HistoricalRetailSeriesKey:
    recent = PriceSeriesKey.objects.get(
        product_class_code=identity.product_class_code,
        category_code=identity.category_code,
        item_code=identity.item_code,
        variety_code=identity.variety_code,
        grade_code=identity.grade_code,
        raw_unit=identity.raw_unit,
        raw_unit_size=identity.raw_unit_size,
        coverage_identity=identity.coverage_identity,
    )
    if (
        recent.product_class_name != identity.product_class_name
        or recent.category_name != identity.category_name
        or recent.item_name != identity.item_name
        or recent.variety_name != identity.variety_name
        or recent.grade_name != identity.grade_name
    ):
        raise ValidationError("Historical row display identity drifted from its reviewed series.")
    return HistoricalRetailSeriesKey.objects.get(
        recent_series=recent,
        code_manifest_sha256=code_manifest_sha256,
    )


def resolve_historical_region(observation: RegionObservation) -> RetailRegionKey:
    region = RetailRegionKey.objects.get(region_code=observation.code)
    if region.region_name != observation.name:
        raise ValidationError("Historical row region name drifted from reviewed evidence.")
    return region


def resolve_historical_market(
    region: RetailRegionKey,
    observation: MarketObservation,
) -> RetailMarketKey:
    market = RetailMarketKey.objects.get(region=region, market_code=observation.code)
    if market.market_name != observation.name:
        raise ValidationError("Historical row market name drifted from reviewed evidence.")
    return market
