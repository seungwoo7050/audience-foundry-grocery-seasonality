import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.models import ParseRun, RetailPriceSnapshot, persist_reference_price_facts
from grocery.publication_facts import (
    ENTRY_HASH_VERSION,
    FACT_SET_HASH_VERSION,
    build_publication_fact_set,
    canonical_snapshot_data,
    snapshot_fact_sha256,
)
from grocery.tests.test_artifact_parse_models import create_artifact
from grocery.tests.test_price_series_key_models import create_series
from grocery.tests.test_retail_price_snapshot_models import (
    create_validated_parse_run,
    replay_snapshot,
)

pytestmark = pytest.mark.django_db


def create_distinct_validated_parse_run() -> ParseRun:
    completed_at = timezone.now()
    return ParseRun.objects.create(
        artifact=create_artifact(),
        parser_revision="kamis-recent-v1",
        configuration_hash=uuid.uuid4().hex * 2,
        result_hash=uuid.uuid4().hex * 2,
        status=ParseRun.Status.VALIDATED,
        started_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
        total_row_count=1,
        accepted_row_count=1,
    )


def make_publishable_snapshot(
    *,
    parse_run: ParseRun | None = None,
    item_code: str = "212",
    source_date: date = date(2026, 8, 29),
    current_price: Decimal = Decimal("8000"),
) -> RetailPriceSnapshot:
    selected_parse_run = parse_run or create_distinct_validated_parse_run()
    series = create_series(item_code=item_code, item_name=f"품목 {item_code}")
    snapshot = replay_snapshot(
        parse_run=selected_parse_run,
        series=series,
        source_effective_date=source_date,
        current_price=current_price,
        source_row_sha256=item_code.zfill(64),
    )
    persist_reference_price_facts(
        snapshot_id=snapshot.id,
        reference_values={
            "WEEK": Decimal("10000"),
            "MONTH": None,
            "YEAR": Decimal("8000"),
        },
    )
    return snapshot


def test_canonical_entry_contains_only_complete_typed_public_facts() -> None:
    snapshot = make_publishable_snapshot()

    data = canonical_snapshot_data(snapshot)

    assert data["entry_hash_version"] == ENTRY_HASH_VERSION
    series = cast(dict[str, Any], data["series"])
    snapshot_data = cast(dict[str, Any], data["snapshot"])
    references = cast(list[dict[str, Any]], data["references"])
    assert series["item_code"] == "212"
    assert snapshot_data["current_price"] == "8000"
    assert [reference["period"] for reference in references] == [
        "WEEK",
        "MONTH",
        "YEAR",
    ]
    assert references[1]["value"] is None
    assert references[1]["change"]["direction"] == "UNAVAILABLE"
    serialized = str(data)
    assert "fetch" not in serialized
    assert "serviceKey" not in serialized


def test_fact_and_set_hashes_are_deterministic_and_order_independent() -> None:
    parse_run = create_validated_parse_run()
    first = make_publishable_snapshot(parse_run=parse_run, item_code="212")
    second = make_publishable_snapshot(
        parse_run=parse_run,
        item_code="213",
        source_date=date(2026, 8, 28),
        current_price=Decimal("9000"),
    )

    forward = build_publication_fact_set([first, second])
    reverse = build_publication_fact_set([second, first])

    assert forward == reverse
    assert len(forward.entries) == 2
    assert [entry.ordinal for entry in forward.entries] == [1, 2]
    assert forward.entries[0].snapshot_id == first.id
    assert forward.typed_fact_set_sha256 == reverse.typed_fact_set_sha256
    assert len(forward.typed_fact_set_sha256) == 64
    assert forward.source_effective_date_min == date(2026, 8, 28)
    assert forward.source_effective_date_max == date(2026, 8, 29)
    assert snapshot_fact_sha256(first) == forward.entries[0].fact_sha256
    assert FACT_SET_HASH_VERSION == "recent-retail-fact-set-v1"


def test_hash_changes_when_a_semantic_public_fact_changes() -> None:
    first = make_publishable_snapshot(item_code="212", current_price=Decimal("8000"))
    second = make_publishable_snapshot(item_code="213", current_price=Decimal("8001"))

    assert snapshot_fact_sha256(first) != snapshot_fact_sha256(second)


def test_set_rejects_empty_duplicate_or_mixed_generation_membership() -> None:
    first = make_publishable_snapshot(item_code="212")
    second = make_publishable_snapshot(item_code="213")

    with pytest.raises(ValidationError, match="empty"):
        build_publication_fact_set([])
    with pytest.raises(ValidationError, match="repeat"):
        build_publication_fact_set([first, first])
    with pytest.raises(ValidationError, match="one parse"):
        build_publication_fact_set([first, second])


def test_entry_rejects_missing_reference_or_change_fact() -> None:
    parse_run = create_validated_parse_run()
    series = create_series(item_code="212")
    snapshot = replay_snapshot(parse_run=parse_run, series=series)

    with pytest.raises(ValidationError, match="exactly"):
        canonical_snapshot_data(snapshot)
