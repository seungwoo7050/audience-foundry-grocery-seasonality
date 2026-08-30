import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import Client, override_settings
from django.urls import reverse

from grocery.models import (
    PriceSeriesKey,
    PublicationActivation,
    PublicationChannel,
    PublicationRevision,
    RetailPriceSnapshot,
    seal_recent_publication,
    transition_recent_publication,
)
from grocery.public_read import load_active_publication
from grocery.tests.test_price_series_key_models import create_series
from grocery.tests.test_publication_revision_models import create_approved_generation

_EVIDENCE_HASH = "a" * 64


def activate_publication() -> tuple[PublicationRevision, RetailPriceSnapshot, Any]:
    decision, snapshots, publisher = create_approved_generation()
    permission = Permission.objects.get(
        content_type__app_label="grocery",
        codename="publish_publication",
    )
    publisher.user_permissions.add(permission)
    publisher = type(publisher)._default_manager.get(pk=publisher.pk)
    revision = seal_recent_publication(decision.id, "ko-v1")
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
    return revision, snapshots[0], publisher


@pytest.mark.django_db
def test_catalog_is_unavailable_before_activation_and_candidate_detail_is_hidden() -> None:
    _, snapshots, _ = create_approved_generation()
    candidate_series_id = snapshots[0].series_id

    catalog_response = Client().get(reverse("grocery:catalog"))
    detail_response = Client().get(
        reverse("grocery:detail", kwargs={"series_id": candidate_series_id})
    )

    assert catalog_response.status_code == 200
    assert "공개 조사값 없음" in catalog_response.content.decode()
    assert "X-Publication-Fact-Set" not in catalog_response
    assert detail_response.status_code == 404
    assert not PublicationChannel.objects.exists()


@pytest.mark.django_db
def test_catalog_search_and_detail_use_only_the_active_sealed_revision() -> None:
    revision, snapshot, _ = activate_publication()
    client = Client()

    catalog_response = client.get(reverse("grocery:catalog"))
    search_response = client.get(reverse("grocery:catalog"), {"q": snapshot.series.item_name})
    empty_response = client.get(reverse("grocery:catalog"), {"q": "일치하지않는공식품목명"})
    detail_response = client.get(
        reverse("grocery:detail", kwargs={"series_id": snapshot.series_id})
    )

    assert catalog_response.status_code == 200
    assert search_response.status_code == 200
    assert empty_response.status_code == 200
    assert detail_response.status_code == 200
    assert catalog_response.headers["X-Publication-Fact-Set"] == revision.typed_fact_set_sha256
    assert detail_response.headers["X-Publication-Fact-Set"] == revision.typed_fact_set_sha256
    assert snapshot.series.item_name in search_response.content.decode()
    assert "조건에 맞는 항목 없음" in empty_response.content.decode()
    assert "KAMIS 소매 조사 평균" in detail_response.content.decode()
    assert "2,000원 낮음" in detail_response.content.decode()
    assert "(+25.0%)" in detail_response.content.decode()
    assert "같음" in detail_response.content.decode()
    assert "source가 비교 기준일을 별도로 제공하지 않음" in detail_response.content.decode()
    assert "데이터셋 15156063" in detail_response.content.decode()
    assert "sessionid" not in catalog_response.cookies


@pytest.mark.django_db
def test_nonmember_series_is_not_addressable_through_active_publication() -> None:
    activate_publication()
    nonmember = create_series(item_code="999", item_name="검토되지않은후보")

    response = Client().get(reverse("grocery:detail", kwargs={"series_id": nonmember.id}))

    assert response.status_code == 404
    assert "검토되지않은후보" not in response.content.decode()


@pytest.mark.django_db
def test_invalid_mobile_search_input_returns_associated_correction_error() -> None:
    activate_publication()
    client = Client()

    invalid_query = "가" * 81
    invalid_response = client.get(reverse("grocery:catalog"), {"q": invalid_query})
    corrected_response = client.get(reverse("grocery:catalog"), {"q": "품목"})

    invalid_html = invalid_response.content.decode()
    assert invalid_response.status_code == 400
    assert 'role="alert"' in invalid_html
    assert 'aria-invalid="true"' in invalid_html
    assert 'aria-describedby="catalog-query-hint search-error"' in invalid_html
    assert "검색어는 80자 이하여야 합니다." in invalid_html
    assert corrected_response.status_code == 200
    assert 'aria-invalid="true"' not in corrected_response.content.decode()


@pytest.mark.django_db
def test_public_request_never_calls_external_source_client() -> None:
    _, snapshot, _ = activate_publication()
    client = Client()

    with patch(
        "grocery.source.client.KamisHttpClient.fetch_recent_prices",
        side_effect=AssertionError("external source must not be called"),
    ) as fetch:
        assert client.get(reverse("grocery:catalog")).status_code == 200
        assert (
            client.get(
                reverse("grocery:detail", kwargs={"series_id": snapshot.series_id})
            ).status_code
            == 200
        )

    fetch.assert_not_called()


@pytest.mark.django_db
def test_confirmation_age_is_separate_and_preserves_last_known_good() -> None:
    revision, _, _ = activate_publication()
    current = load_active_publication()
    assert current is not None

    with override_settings(KAMIS_CONFIRMATION_MAX_AGE_HOURS=1):
        stale = load_active_publication(observed_at=current.checked_at + timedelta(hours=2))

    assert stale is not None
    assert stale.revision.id == revision.id
    assert stale.freshness_state == "stale"
    assert "새 확인 필요" in stale.freshness_label


@pytest.mark.django_db
def test_database_failure_uses_fixed_server_error_without_exception_reflection() -> None:
    marker = "must-not-be-reflected"
    with patch("grocery.views.load_active_publication", side_effect=DatabaseError(marker)):
        response = Client().get(reverse("grocery:catalog"))

    assert response.status_code == 503
    assert "자료를 표시하지 못함" in response.content.decode()
    assert marker not in response.content.decode()


@pytest.mark.django_db
def test_qa_state_routes_are_hard_disabled_unless_local_setting_is_explicit() -> None:
    disabled = Client().get(reverse("grocery:qa_catalog_state", kwargs={"state": "loading"}))
    assert disabled.status_code == 404

    with override_settings(QA_STATE_PREVIEWS_ENABLED=True):
        for state in ("loading", "empty", "unavailable", "stale", "server_error"):
            response = Client().get(reverse("grocery:qa_catalog_state", kwargs={"state": state}))
            assert response.status_code == (503 if state == "server_error" else 200)
            assert "로컬 화면 상태 검수용 미리보기" in response.content.decode()


@pytest.mark.django_db
def test_catalog_response_does_not_expose_internal_actor_or_revision_ids() -> None:
    revision, _, publisher = activate_publication()

    response = Client().get(reverse("grocery:catalog"))
    body = response.content.decode()

    assert str(revision.id) not in body
    assert publisher.username not in body
    assert revision.review_decision.acceptance_evidence_sha256 not in body


@pytest.mark.django_db
def test_unrelated_unpublished_series_does_not_change_catalog_results() -> None:
    activate_publication()
    before = Client().get(reverse("grocery:catalog")).content
    PriceSeriesKey.get_or_validate(
        product_class_code="01",
        product_class_name="소매",
        category_code="200",
        category_name="채소류",
        item_code="998",
        item_name="공개되지않은긴후보이름",
        variety_code="00",
        variety_name="후보품종",
        grade_code="04",
        grade_name="상품",
        raw_unit="단",
        raw_unit_size="99",
        coverage_identity="KAMIS_RETAIL_ALL_REGIONS_22_CITIES_V1",
        identity_evidence_revision="kamis-codebook-20260830-v1",
    )

    after = Client().get(reverse("grocery:catalog")).content

    assert before == after
    assert "공개되지않은긴후보이름" not in after.decode()
