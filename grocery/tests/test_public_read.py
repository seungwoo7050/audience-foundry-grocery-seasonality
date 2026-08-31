import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.utils import timezone

from grocery.models import (
    ParseRun,
    PriceChangeFact,
    PublicationActivation,
    PublicationRevision,
    ReferencePrice,
    RetailPriceSnapshot,
    persist_reference_price_facts,
    seal_recent_publication,
    transition_recent_publication,
)
from grocery.public_read import (
    _comparison_context,
    catalog_item,
    load_active_publication,
    publication_context,
    publication_entries,
)
from grocery.tests.test_price_series_key_models import create_series
from grocery.tests.test_publication_revision_models import create_approved_generation
from grocery.tests.test_retail_price_snapshot_models import create_snapshot

_EVIDENCE_HASH = "b" * 64


def _catalog_week_references(snapshot: RetailPriceSnapshot) -> list[ReferencePrice]:
    references = getattr(snapshot, "catalog_week_references", None)
    assert isinstance(references, list)
    assert all(isinstance(reference, ReferencePrice) for reference in references)
    return cast(list[ReferencePrice], references)


def _activate_publication(
    *,
    snapshot_count: int = 1,
    unavailable_month: bool = False,
) -> tuple[PublicationRevision, tuple[RetailPriceSnapshot, ...], Any]:
    decision, snapshots, publisher = create_approved_generation(
        snapshot_count=snapshot_count,
        unavailable_month=unavailable_month,
    )
    permission = Permission.objects.get(
        content_type__app_label="grocery",
        codename="publish_publication",
    )
    publisher.user_permissions.add(permission)
    publisher = type(publisher)._default_manager.get(pk=publisher.pk)
    revision = seal_recent_publication(decision.id, "ko-v3")
    transition_recent_publication(
        operation_id=uuid.uuid4(),
        actor=publisher,
        operation=PublicationActivation.Operation.ACTIVATE,
        target_revision_id=revision.id,
        expected_current_revision_id=None,
        expected_version=0,
        reason_code="LOCAL_PHASE0_ACTIVATE",
        acceptance_evidence_sha256=_EVIDENCE_HASH,
    )
    return revision, snapshots, publisher


@pytest.mark.parametrize("snapshot_count", (1, 100))
@pytest.mark.django_db
def test_catalog_week_comparisons_use_one_primary_and_one_filtered_prefetch_query(
    django_assert_num_queries: Any,
    snapshot_count: int,
) -> None:
    _activate_publication(snapshot_count=snapshot_count)
    active = load_active_publication()
    assert active is not None

    with django_assert_num_queries(2):
        entries = list(publication_entries(active, query="", category=""))

    with django_assert_num_queries(0):
        items = [
            catalog_item(entry, active, url=f"/detail/{entry.snapshot.series_id}/")
            for entry in entries
        ]

    assert len(items) == snapshot_count
    assert all(len(_catalog_week_references(entry.snapshot)) == 1 for entry in entries)
    assert items[0]["week_comparison"] == {
        "period_label": "1주 전 제공값",
        "reference_date_display": "",
        "reference_date_unavailable": True,
        "available": True,
        "reference_price_display": "10,000원",
        "difference_display": "2,000원",
        "percentage_display": "-20.0%",
        "direction_code": PriceChangeFact.Direction.LOWER,
        "direction_label": "낮음",
        "unavailable_reason": "",
        "microbar": {
            "x": Decimal("30.0"),
            "width": Decimal("20.0"),
            "capped": False,
            "cap_x": Decimal("30.0"),
            "is_equal": False,
        },
    }


@pytest.mark.django_db
def test_catalog_week_comparison_fails_closed_for_zero_multiple_or_malformed_prefetch() -> None:
    _activate_publication()
    active = load_active_publication()
    assert active is not None
    entry = list(publication_entries(active, query="", category=""))[0]
    week = _catalog_week_references(entry.snapshot)[0]

    for malformed in ([], [week, week], [object()]):
        entry.snapshot.__dict__["catalog_week_references"] = malformed
        with pytest.raises(ValidationError):
            catalog_item(entry, active, url="/detail/")


@pytest.mark.django_db
def test_catalog_treats_explicitly_unavailable_and_equal_week_values_as_normal() -> None:
    _activate_publication()
    active = load_active_publication()
    assert active is not None
    entry = list(publication_entries(active, query="", category=""))[0]
    week = _catalog_week_references(entry.snapshot)[0]

    week.value_status = ReferencePrice.ValueStatus.UNAVAILABLE
    week.value = None
    week.unavailable_reason = ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING
    week.change_fact.direction = PriceChangeFact.Direction.UNAVAILABLE
    week.change_fact.signed_difference = None
    week.change_fact.signed_percentage = None
    unavailable = catalog_item(entry, active, url="/detail/")["week_comparison"]
    assert unavailable == {
        "period_label": "1주 전 제공값",
        "reference_date_display": "",
        "reference_date_unavailable": True,
        "available": False,
        "reference_price_display": "",
        "difference_display": "",
        "percentage_display": "",
        "direction_code": PriceChangeFact.Direction.UNAVAILABLE,
        "direction_label": "비교 정보 없음",
        "unavailable_reason": "KAMIS가 이 기간의 비교값을 제공하지 않았습니다.",
        "microbar": None,
    }

    week.value_status = ReferencePrice.ValueStatus.AVAILABLE
    week.value = entry.snapshot.current_price
    week.unavailable_reason = None
    week.change_fact.direction = PriceChangeFact.Direction.EQUAL
    week.change_fact.signed_difference = Decimal("0")
    week.change_fact.signed_percentage = Decimal("0.0")
    equal = catalog_item(entry, active, url="/detail/")["week_comparison"]
    assert equal == {
        "period_label": "1주 전 제공값",
        "reference_date_display": "",
        "reference_date_unavailable": True,
        "available": True,
        "reference_price_display": "8,000원",
        "difference_display": "0원",
        "percentage_display": "0.0%",
        "direction_code": PriceChangeFact.Direction.EQUAL,
        "direction_label": "같음",
        "unavailable_reason": "",
        "microbar": {
            "x": Decimal("50.0"),
            "width": Decimal("0.0"),
            "capped": False,
            "cap_x": Decimal("50.0"),
            "is_equal": True,
        },
    }


@pytest.mark.django_db
def test_catalog_uses_only_active_revision_week_facts() -> None:
    revision, active_snapshots, _ = _activate_publication(snapshot_count=3)
    completed_at = timezone.now()
    unpublished_parse = ParseRun.objects.create(
        artifact=revision.generation.artifact,
        parser_revision="kamis-recent-v1",
        configuration_hash="f" * 64,
        result_hash="9" * 64,
        status=ParseRun.Status.VALIDATED,
        started_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
        total_row_count=1,
        accepted_row_count=1,
    )
    unpublished_snapshot = create_snapshot(
        parse_run=unpublished_parse,
        series=create_series(item_code="999", item_name="비공개 품목"),
        source_row_sha256="8" * 64,
    )
    persist_reference_price_facts(
        snapshot_id=unpublished_snapshot.id,
        reference_values={
            "WEEK": Decimal("2000"),
            "MONTH": Decimal("2100"),
            "YEAR": Decimal("2200"),
        },
    )
    active = load_active_publication()
    assert active is not None

    entries = list(publication_entries(active, query="", category=""))

    assert {entry.snapshot_id for entry in entries} == {
        snapshot.id for snapshot in active_snapshots
    }
    assert unpublished_snapshot.id not in {entry.snapshot_id for entry in entries}
    assert all(
        {reference.period for reference in _catalog_week_references(entry.snapshot)}
        == {ReferencePrice.Period.WEEK}
        for entry in entries
    )
    assert all(
        reference.snapshot_id == entry.snapshot_id
        for entry in entries
        for reference in _catalog_week_references(entry.snapshot)
    )
    assert all(
        reference.snapshot_id != unpublished_snapshot.id
        for entry in entries
        for reference in _catalog_week_references(entry.snapshot)
    )


@pytest.mark.django_db
def test_explicitly_unavailable_comparison_is_a_normal_public_state() -> None:
    _, snapshots, _ = _activate_publication(unavailable_month=True)
    reference = ReferencePrice.objects.select_related("change_fact").get(
        snapshot=snapshots[0],
        period=ReferencePrice.Period.MONTH,
    )

    assert _comparison_context(reference) == {
        "period_label": "1개월 전 제공값",
        "reference_date_display": "",
        "reference_date_unavailable": True,
        "available": False,
        "reference_price_display": "",
        "difference_display": "",
        "percentage_display": "",
        "direction_code": PriceChangeFact.Direction.UNAVAILABLE,
        "direction_label": "비교 정보 없음",
        "unavailable_reason": "KAMIS가 이 기간의 비교값을 제공하지 않았습니다.",
        "microbar": None,
    }


@pytest.mark.django_db
def test_comparison_fails_closed_for_reference_date_incomplete_fact_or_direction_drift() -> None:
    _, snapshots, _ = _activate_publication()
    reference = ReferencePrice.objects.select_related("change_fact").get(
        snapshot=snapshots[0],
        period=ReferencePrice.Period.WEEK,
    )

    reference.source_reference_date = date(2026, 8, 22)
    with pytest.raises(ValidationError):
        _comparison_context(reference)

    reference.source_reference_date = None
    reference.change_fact.signed_difference = None
    with pytest.raises(ValidationError):
        _comparison_context(reference)

    reference.change_fact.signed_difference = Decimal("-2000")
    reference.change_fact.direction = PriceChangeFact.Direction.HIGHER
    with pytest.raises(ValidationError):
        _comparison_context(reference)

    missing_change = ReferencePrice(
        period=ReferencePrice.Period.WEEK,
        value_status=ReferencePrice.ValueStatus.AVAILABLE,
        value=Decimal("10000"),
        unavailable_reason=None,
        reference_date_status=(
            ReferencePrice.ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
        ),
        source_reference_date=None,
    )
    with pytest.raises(ValidationError):
        _comparison_context(missing_change)


@pytest.mark.django_db
def test_publication_context_keeps_confirmation_time_separate_from_source_dates() -> None:
    _, snapshots, _ = _activate_publication()
    active = load_active_publication()
    assert active is not None

    summary = publication_context(active)

    assert summary == {
        "checked_at_iso": active.checked_at.isoformat(),
        "checked_at_display": summary["checked_at_display"],
        "freshness_state": "current",
        "freshness_label": "KAMIS 자료 확인 완료",
    }
    assert summary["checked_at_display"]
    assert snapshots[0].source_effective_date.isoformat() != summary["checked_at_iso"]

    stale = load_active_publication(
        observed_at=active.checked_at + timedelta(hours=37),
    )
    assert stale is not None
    assert publication_context(stale)["freshness_label"] == "마지막 공개 자료 · 최근 확인 필요"
