"""PostgreSQL integration tests for deterministic KAMIS generation persistence."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from grocery.models import (
    ParseRun,
    PriceChangeFact,
    PriceSeriesKey,
    ReferencePrice,
    RetailPriceSnapshot,
    persist_reference_price_facts,
)
from grocery.source.generation import (
    KAMIS_PARSE_CONFIGURATION_SHA256,
    KAMIS_PARSER_REVISION,
    KAMIS_SOURCE_CONTRACT_REVISION,
    ParseGenerationError,
    ParseGenerationErrorCode,
    ParseGenerationFailureCode,
    complete_kamis_parse_generation,
    fail_kamis_parse_run,
    start_or_get_kamis_parse_run,
)
from grocery.source.kamis import (
    KAMIS_ALLOWED_CATEGORIES,
    ParsedRecentPriceResult,
    parse_recent_price_rows,
)
from grocery.source.registry import INITIAL_RETAIL_IDENTITY_REGISTRY
from grocery.tests.test_artifact_parse_models import create_artifact

pytestmark = pytest.mark.django_db


def _synthetic_contract_row(
    series_key: tuple[str, str, str, str],
    *,
    ordinal: int,
    missing_period: str | None = None,
) -> dict[str, object]:
    category_code, item_code, variety_code, grade_code = series_key
    registry = INITIAL_RETAIL_IDENTITY_REGISTRY
    raw_unit, raw_unit_size = registry.units[series_key]
    current = 8_000 + ordinal * 100
    values: dict[str, object] = {
        "ctgry_cd": category_code,
        "ctgry_nm": KAMIS_ALLOWED_CATEGORIES[category_code],
        "dd1_bfr_cnvs_prc": str(current - 50),
        "dd1_bfr_prc": str(current - 50),
        "exmn_dd_cnvs_prc": str(current),
        "exmn_dd_prc": str(current),
        "exmn_ymd": "20260829",
        "grd_cd": grade_code,
        "grd_nm": registry.grade_names[series_key],
        "item_cd": item_code,
        "item_nm": registry.item_names[(category_code, item_code)],
        "mm1_bfr_cnvs_prc": str(current - 200),
        "mm1_bfr_prc": str(current - 200),
        "se_cd": "01",
        "se_nm": "소매",
        "unit": raw_unit,
        "unit_sz": raw_unit_size,
        "vrty_cd": variety_code,
        "vrty_nm": registry.variety_names[(category_code, item_code, variety_code)],
        "ww1_bfr_cnvs_prc": str(current - 100),
        "ww1_bfr_prc": str(current - 100),
        "yy1_bfr_cnvs_prc": str(current),
        "yy1_bfr_prc": str(current),
    }
    if missing_period == "WEEK":
        values["ww1_bfr_prc"] = None
    elif missing_period == "MONTH":
        values["mm1_bfr_prc"] = None
    elif missing_period == "YEAR":
        values["yy1_bfr_prc"] = None
    return values


def _parsed_result(*, missing_row_ordinal: int | None = None) -> ParsedRecentPriceResult:
    rows = [
        _synthetic_contract_row(
            series_key,
            ordinal=ordinal,
            missing_period="MONTH" if ordinal == missing_row_ordinal else None,
        )
        for ordinal, series_key in enumerate(
            sorted(INITIAL_RETAIL_IDENTITY_REGISTRY.units),
            start=1,
        )
    ]
    out_of_scope = deepcopy(rows[0])
    out_of_scope["se_cd"] = "02"
    out_of_scope["se_nm"] = "합성 중도매"
    return parse_recent_price_rows(
        [*reversed(rows), out_of_scope],
        identity_registry=INITIAL_RETAIL_IDENTITY_REGISTRY,
    )


def _start_generation() -> ParseRun:
    return start_or_get_kamis_parse_run(create_artifact().id).parse_run


def test_positive_generation_persists_exact_typed_facts_and_replays_without_duplicates() -> None:
    artifact = create_artifact()
    first_start = start_or_get_kamis_parse_run(artifact.id)
    repeated_start = start_or_get_kamis_parse_run(artifact.id)
    result = _parsed_result()

    completed = complete_kamis_parse_generation(first_start.parse_run.id, result)
    replayed = complete_kamis_parse_generation(first_start.parse_run.id, result)

    assert first_start.created is True
    assert repeated_start.created is False
    assert repeated_start.parse_run.id == first_start.parse_run.id
    assert KAMIS_PARSER_REVISION == "kamis-recent-price-v1"
    assert KAMIS_SOURCE_CONTRACT_REVISION == "data-go-15156063-recent-v1"
    assert len(KAMIS_PARSE_CONFIGURATION_SHA256) == 64
    assert completed.replayed is False
    assert replayed.replayed is True
    assert [snapshot.id for snapshot in replayed.snapshots] == [
        snapshot.id for snapshot in completed.snapshots
    ]

    parse_run = ParseRun.objects.get(pk=first_start.parse_run.id)
    assert parse_run.status == ParseRun.Status.VALIDATED
    assert parse_run.parser_revision == KAMIS_PARSER_REVISION
    assert parse_run.configuration_hash == KAMIS_PARSE_CONFIGURATION_SHA256
    assert parse_run.result_hash == result.result_hash
    assert parse_run.total_row_count == 11
    assert parse_run.accepted_row_count == 10
    assert parse_run.out_of_scope_row_count == 1
    assert parse_run.missing_reference_row_count == 0
    assert parse_run.quarantined_row_count == 0
    assert PriceSeriesKey.objects.count() == 10
    assert RetailPriceSnapshot.objects.count() == 10
    assert ReferencePrice.objects.count() == 30
    assert PriceChangeFact.objects.count() == 30
    assert set(RetailPriceSnapshot.objects.values_list("source_contract_revision", flat=True)) == {
        KAMIS_SOURCE_CONTRACT_REVISION
    }
    assert set(PriceSeriesKey.objects.values_list("identity_evidence_revision", flat=True)) == {
        INITIAL_RETAIL_IDENTITY_REGISTRY.evidence.coverage_evidence_revision
    }


def test_missing_reference_counts_rows_once_and_persists_unavailable_fact() -> None:
    parse_run = _start_generation()
    result = _parsed_result(missing_row_ordinal=1)

    complete_kamis_parse_generation(parse_run.id, result)

    parse_run.refresh_from_db()
    unavailable = ReferencePrice.objects.get(value_status=ReferencePrice.ValueStatus.UNAVAILABLE)
    assert parse_run.missing_reference_row_count == 1
    assert ReferencePrice.objects.count() == 30
    assert unavailable.period == ReferencePrice.Period.MONTH
    assert unavailable.value is None
    assert unavailable.unavailable_reason == ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING
    assert unavailable.reference_date_status == (
        ReferencePrice.ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
    )
    assert unavailable.source_reference_date is None
    assert unavailable.change_fact.direction == PriceChangeFact.Direction.UNAVAILABLE
    assert unavailable.change_fact.signed_difference is None
    assert unavailable.change_fact.signed_percentage is None


def test_count_drift_fails_before_any_domain_fact_and_can_be_safely_finalized() -> None:
    parse_run = _start_generation()
    valid = _parsed_result()
    drifted = replace(valid, input_row_count=valid.input_row_count + 1)

    with pytest.raises(ParseGenerationError) as caught:
        complete_kamis_parse_generation(parse_run.id, drifted)

    assert caught.value.code is ParseGenerationErrorCode.RESULT_RECONCILIATION_FAILED
    assert str(caught.value) == "RESULT_RECONCILIATION_FAILED"
    parse_run.refresh_from_db()
    assert parse_run.status == ParseRun.Status.STARTED
    assert not PriceSeriesKey.objects.exists()
    assert not RetailPriceSnapshot.objects.exists()
    assert not ReferencePrice.objects.exists()
    assert not PriceChangeFact.objects.exists()

    failed = fail_kamis_parse_run(
        parse_run.id,
        ParseGenerationFailureCode.RECONCILIATION_FAILED,
    )
    assert failed.status == ParseRun.Status.FAILED
    assert failed.failure_code == "RECONCILIATION_FAILED"


def test_reference_replay_conflict_fails_closed_without_partial_new_rows() -> None:
    parse_run = _start_generation()
    result = _parsed_result()
    first_row = result.rows[0]
    registry = INITIAL_RETAIL_IDENTITY_REGISTRY
    series = PriceSeriesKey.get_or_validate(
        product_class_code=first_row.product_class_code,
        product_class_name=first_row.product_class_name,
        category_code=first_row.category_code,
        category_name=first_row.category_name,
        item_code=first_row.item_code,
        item_name=first_row.item_name,
        variety_code=first_row.variety_code,
        variety_name=first_row.variety_name,
        grade_code=first_row.grade_code,
        grade_name=first_row.grade_name,
        raw_unit=first_row.raw_unit,
        raw_unit_size=first_row.raw_unit_size,
        coverage_identity=first_row.coverage_identity,
        identity_evidence_revision=registry.evidence.coverage_evidence_revision,
    )
    snapshot = RetailPriceSnapshot(
        parse_run=parse_run,
        series=series,
        source_effective_date=first_row.source_effective_date,
        source_recorded_at=None,
        current_price=first_row.current_price,
        currency=RetailPriceSnapshot.Currency.KRW,
        source_row_sha256=first_row.source_row_hash,
        source_contract_revision=KAMIS_SOURCE_CONTRACT_REVISION,
    )
    RetailPriceSnapshot.objects.bulk_create([snapshot])
    ReferencePrice.objects.create(
        snapshot=snapshot,
        period=ReferencePrice.Period.WEEK,
        value_status=ReferencePrice.ValueStatus.AVAILABLE,
        value=Decimal("1"),
        unavailable_reason=None,
    )

    with pytest.raises(ParseGenerationError) as caught:
        complete_kamis_parse_generation(parse_run.id, result)

    assert caught.value.code is ParseGenerationErrorCode.PERSISTENCE_CONFLICT
    parse_run.refresh_from_db()
    assert parse_run.status == ParseRun.Status.STARTED
    assert RetailPriceSnapshot.objects.count() == 1
    assert ReferencePrice.objects.count() == 1
    assert PriceChangeFact.objects.count() == 0
    assert PriceSeriesKey.objects.count() == 1


def test_mid_generation_failure_rolls_back_run_and_every_new_fact() -> None:
    parse_run = _start_generation()
    result = _parsed_result()
    call_count = 0

    def persist_then_fail(
        *,
        snapshot_id: uuid.UUID,
        reference_values: Mapping[str, Decimal | None],
    ) -> tuple[PriceChangeFact, ...]:
        nonlocal call_count
        facts = persist_reference_price_facts(
            snapshot_id=snapshot_id,
            reference_values=reference_values,
        )
        call_count += 1
        if call_count == 1:
            raise ValidationError("test-only injected failure")
        return facts

    with (
        patch(
            "grocery.source.generation.persist_reference_price_facts",
            side_effect=persist_then_fail,
        ),
        pytest.raises(ParseGenerationError) as caught,
    ):
        complete_kamis_parse_generation(parse_run.id, result)

    assert caught.value.code is ParseGenerationErrorCode.PERSISTENCE_CONFLICT
    assert str(caught.value) == "PERSISTENCE_CONFLICT"
    parse_run.refresh_from_db()
    assert parse_run.status == ParseRun.Status.STARTED
    assert parse_run.result_hash == ""
    assert not PriceSeriesKey.objects.exists()
    assert not RetailPriceSnapshot.objects.exists()
    assert not ReferencePrice.objects.exists()
    assert not PriceChangeFact.objects.exists()


def test_same_artifact_changed_result_fails_as_nondeterministic_replay() -> None:
    parse_run = _start_generation()
    result = _parsed_result()
    completed = complete_kamis_parse_generation(parse_run.id, result)
    changed = replace(result, result_hash="f" * 64)

    with pytest.raises(ParseGenerationError) as caught:
        complete_kamis_parse_generation(parse_run.id, changed)

    assert caught.value.code is ParseGenerationErrorCode.NONDETERMINISTIC_REPLAY
    assert str(caught.value) == "NONDETERMINISTIC_REPLAY"
    assert RetailPriceSnapshot.objects.count() == len(completed.snapshots) == 10
    assert ReferencePrice.objects.count() == 30
    assert PriceChangeFact.objects.count() == 30


def test_failure_boundary_rejects_arbitrary_codes_without_echoing_them() -> None:
    parse_run = _start_generation()
    unsafe = "NOT_ALLOWED_private-input"

    with pytest.raises(ParseGenerationError) as caught:
        fail_kamis_parse_run(parse_run.id, unsafe)  # type: ignore[arg-type]

    assert caught.value.code is ParseGenerationErrorCode.FAILURE_CODE_INVALID
    assert str(caught.value) == "FAILURE_CODE_INVALID"
    assert unsafe not in str(caught.value)
    parse_run.refresh_from_db()
    assert parse_run.status == ParseRun.Status.STARTED
