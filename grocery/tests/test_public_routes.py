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
    assert "아직 공개된 조사 자료가 없습니다" in catalog_response.content.decode()
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
    detail_html = " ".join(detail_response.content.decode().split())
    assert catalog_response.headers["X-Publication-Fact-Set"] == revision.typed_fact_set_sha256
    assert detail_response.headers["X-Publication-Fact-Set"] == revision.typed_fact_set_sha256
    assert snapshot.series.item_name in search_response.content.decode()
    assert "검색 결과가 없습니다" in empty_response.content.decode()
    assert "KAMIS 소매 조사 평균" in detail_html
    assert "조사일 평균이 비교값보다 2,000원 낮음 (-20.0%)" in detail_html
    assert "(+25.0%)" in detail_html
    assert "같음" in detail_html
    assert "KAMIS에서 제공하지 않음" in detail_html
    assert "데이터셋 15156063" in detail_html
    assert "sessionid" not in catalog_response.cookies
    assert catalog_response.context["publication"]["freshness_label"] == "KAMIS 자료 확인 완료"
    assert catalog_response.context["results"][0]["week_comparison"]["period_label"] == (
        "1주 전 제공값"
    )
    assert detail_response.context["publication"] == {
        "checked_at_iso": detail_response.context["provenance"]["checked_at_iso"],
        "checked_at_display": detail_response.context["provenance"]["checked_at_display"],
        "freshness_state": "current",
        "freshness_label": "KAMIS 자료 확인 완료",
    }


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
    assert "입력 내용을 확인하세요" in invalid_html
    assert "품목명은 80자 이하로 입력하세요." in invalid_html
    assert invalid_query not in invalid_html
    assert "검색 결과가 없습니다" not in invalid_html
    assert corrected_response.status_code == 200
    assert 'aria-invalid="true"' not in corrected_response.content.decode()


@pytest.mark.django_db
def test_category_validation_is_distinct_and_never_reflects_query_or_choice() -> None:
    activate_publication()
    query_marker = "응답에나오면안되는검색표시"
    category_marker = "응답에나오면안되는부류표시"

    response = Client().get(
        reverse("grocery:catalog"),
        {"q": query_marker, "category": category_marker},
    )
    html = response.content.decode()

    assert response.status_code == 400
    assert "부류 선택을 확인해 주세요." in html
    assert "부류 선택 초기화" in html
    assert 'aria-invalid="true"' not in html
    assert query_marker not in html
    assert category_marker not in html
    assert "검색 결과가 없습니다" not in html


@pytest.mark.django_db
def test_valid_unmatched_query_is_not_echoed_or_propagated_to_category_urls() -> None:
    activate_publication()
    marker = "응답에나오면안되는정상검색표시"

    response = Client().get(reverse("grocery:catalog"), {"q": marker})
    html = response.content.decode()

    assert response.status_code == 200
    assert marker not in html
    assert "q=" not in html


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
    assert stale.freshness_label == "마지막 공개 자료 · 최근 확인 필요"


@pytest.mark.django_db
def test_database_failure_uses_fixed_server_error_without_exception_reflection() -> None:
    marker = "must-not-be-reflected"
    with patch("grocery.views.load_active_publication", side_effect=DatabaseError(marker)):
        response = Client().get(reverse("grocery:catalog"))

    assert response.status_code == 503
    assert "조사 자료를 불러오지 못했습니다" in response.content.decode()
    assert marker not in response.content.decode()


@pytest.mark.django_db
def test_qa_state_routes_are_hard_disabled_unless_local_setting_is_explicit() -> None:
    for state in ("loading", "error_400", "error_403", "error_404", "error_500"):
        disabled = Client().get(reverse("grocery:qa_catalog_state", kwargs={"state": state}))
        assert disabled.status_code == 404

    with override_settings(QA_STATE_PREVIEWS_ENABLED=True):
        expected_headings = {
            "loading": "조사 자료를 불러오고 있습니다",
            "empty": "검색 결과가 없습니다",
            "unavailable": "아직 공개된 조사 자료가 없습니다",
            "stale": "마지막 공개 자료를 표시합니다",
            "server_error": "조사 자료를 불러오지 못했습니다",
        }
        for state, heading in expected_headings.items():
            response = Client().get(reverse("grocery:qa_catalog_state", kwargs={"state": state}))
            assert response.status_code == (503 if state == "server_error" else 200)
            assert heading in response.content.decode()

        error_previews = {
            "error_400": (400, "요청 내용을 확인하세요"),
            "error_403": (403, "이 페이지를 볼 수 없습니다"),
            "error_404": (404, "페이지를 찾을 수 없습니다"),
            "error_500": (500, "페이지를 표시하지 못했습니다"),
        }
        for state, (status, heading) in error_previews.items():
            response = Client().get(reverse("grocery:qa_catalog_state", kwargs={"state": state}))
            assert response.status_code == status
            assert f'<h1 id="error-heading">{heading}</h1>' in response.content.decode()


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


@pytest.mark.django_db
def test_public_html_is_no_store_for_success_validation_not_found_and_failure() -> None:
    _, snapshot, _ = activate_publication()
    client = Client()
    responses = [
        client.get(reverse("grocery:catalog")),
        client.get(reverse("grocery:catalog"), {"q": "가" * 81}),
        client.get(reverse("grocery:detail", kwargs={"series_id": uuid.uuid4()})),
        client.get(reverse("grocery:detail", kwargs={"series_id": snapshot.series_id})),
    ]
    with patch("grocery.views.load_active_publication", side_effect=DatabaseError("hidden")):
        responses.append(client.get(reverse("grocery:catalog")))

    assert [response.status_code for response in responses] == [200, 400, 404, 200, 503]
    assert all(response.headers["Cache-Control"] == "no-store" for response in responses)


@pytest.mark.django_db
def test_public_views_reject_unsafe_methods_before_reading_publication() -> None:
    client = Client()
    paths = (
        reverse("grocery:catalog"),
        reverse("grocery:detail", kwargs={"series_id": uuid.uuid4()}),
    )

    with patch("grocery.views.load_active_publication") as publication_read:
        for path in paths:
            for method in (client.post, client.put, client.patch, client.delete):
                response = method(path)
                assert response.status_code == 405

    publication_read.assert_not_called()
