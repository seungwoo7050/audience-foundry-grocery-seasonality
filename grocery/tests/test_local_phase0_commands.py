"""PostgreSQL integration tests for local review and publication commands."""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from grocery.management.local_phase0 import (
    LOCAL_APPROVAL_REASON_CODE,
    LOCAL_OPERATOR_USERNAME,
)
from grocery.models import PublicationRevision, ReviewDecision
from grocery.tests.test_review_decision_models import complete_generation

pytestmark = pytest.mark.django_db

_ACCEPTANCE_HASH = "7" * 64


def _run(name: str, **options: object) -> str:
    output = io.StringIO()
    call_command(name, stdout=output, **options)
    return output.getvalue().strip()


def _operator() -> Any:
    return get_user_model()._default_manager.get(username=LOCAL_OPERATOR_USERNAME)


def _permission_contract() -> set[tuple[str, str, str]]:
    return {
        ("grocery", "reviewdecision", "review_generation"),
        ("grocery", "publicationactivation", "publish_publication"),
    }


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_bootstrap_creates_exact_nonlogin_actor_and_replays_idempotently() -> None:
    first = _run("bootstrap_local_phase0_operator")
    second = _run("bootstrap_local_phase0_operator")

    actor = _operator()
    actual_permissions = set(
        actor.user_permissions.values_list(
            "content_type__app_label",
            "content_type__model",
            "codename",
        )
    )
    assert first == f"status=READY actor_id={actor.pk} created=yes"
    assert second == f"status=READY actor_id={actor.pk} created=no"
    assert LOCAL_OPERATOR_USERNAME not in first
    assert LOCAL_OPERATOR_USERNAME not in second
    assert actor.email == actor.first_name == actor.last_name == ""
    assert actor.is_active is True
    assert actor.is_staff is False
    assert actor.is_superuser is False
    assert actor.has_usable_password() is False
    assert actor.groups.count() == 0
    assert actual_permissions == _permission_contract()


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_bootstrap_missing_publish_permission_fails_before_actor_creation() -> None:
    Permission.objects.filter(
        content_type__app_label="grocery",
        content_type__model="publicationactivation",
        codename="publish_publication",
    ).delete()

    with pytest.raises(CommandError) as caught:
        _run("bootstrap_local_phase0_operator")

    assert str(caught.value) == "code=LOCAL_PHASE0_PERMISSION_MISSING"
    assert not get_user_model()._default_manager.filter(username=LOCAL_OPERATOR_USERNAME).exists()


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_bootstrap_existing_semantic_conflict_is_not_mutated() -> None:
    actor = get_user_model()._default_manager.create_user(
        username=LOCAL_OPERATOR_USERNAME,
        email="",
        password=None,
        is_active=True,
        is_staff=True,
    )

    with pytest.raises(CommandError) as caught:
        _run("bootstrap_local_phase0_operator")

    actor.refresh_from_db()
    assert str(caught.value) == "code=LOCAL_PHASE0_OPERATOR_CONFLICT"
    assert actor.is_staff is True
    assert actor.user_permissions.count() == 0


@pytest.mark.parametrize(
    ("debug", "admin_enabled"),
    ((False, False), (True, True)),
)
def test_bootstrap_environment_denial_writes_nothing(
    debug: bool,
    admin_enabled: bool,
) -> None:
    with (
        override_settings(DEBUG=debug, ADMIN_ENABLED=admin_enabled),
        pytest.raises(CommandError) as caught,
    ):
        _run("bootstrap_local_phase0_operator")

    assert str(caught.value) == "code=LOCAL_PHASE0_ENVIRONMENT_DENIED"
    assert not get_user_model()._default_manager.filter(username=LOCAL_OPERATOR_USERNAME).exists()


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_approve_locks_and_records_exact_generation_then_replays() -> None:
    _run("bootstrap_local_phase0_operator")
    source, parse_run = complete_generation()
    decision_id = uuid.uuid4()

    first = _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
    )
    second = _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
    )

    actor = _operator()
    decision = ReviewDecision.objects.get(pk=decision_id)
    expected_prefix = " ".join(
        (
            "status=APPROVED",
            f"decision_id={decision_id}",
            f"parse_run_id={parse_run.id}",
            f"artifact_id={parse_run.artifact_id}",
            f"source_configuration_id={source.id}",
            f"actor_id={actor.pk}",
        )
    )
    assert first == f"{expected_prefix} created=yes"
    assert second == f"{expected_prefix} created=no"
    assert LOCAL_OPERATOR_USERNAME not in first
    assert _ACCEPTANCE_HASH not in first
    assert decision.reviewer_id == actor.pk
    assert decision.decision == ReviewDecision.Decision.APPROVE
    assert decision.reconciliation_report_sha256 == parse_run.result_hash
    assert decision.acceptance_evidence_sha256 == _ACCEPTANCE_HASH
    assert decision.reason_code == LOCAL_APPROVAL_REASON_CODE
    assert decision.approved_mode == source.publication_mode
    assert decision.approved_coverage_identity == source.coverage_identity
    assert decision.approved_coverage_evidence_revision == source.coverage_evidence_revision
    assert ReviewDecision.objects.count() == 1


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_approve_uuid_replay_conflict_fails_without_mutating_decision() -> None:
    _run("bootstrap_local_phase0_operator")
    _source, parse_run = complete_generation()
    decision_id = uuid.uuid4()
    _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
    )

    with pytest.raises(CommandError) as caught:
        _run(
            "approve_recent_generation",
            parse_run_id=parse_run.id,
            decision_id=decision_id,
            acceptance_evidence_sha256="8" * 64,
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_REVIEW_FAILED"
    assert ReviewDecision.objects.get(pk=decision_id).acceptance_evidence_sha256 == (
        _ACCEPTANCE_HASH
    )


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_seal_calls_reviewed_publication_service_and_replays() -> None:
    _run("bootstrap_local_phase0_operator")
    _source, parse_run = complete_generation()
    decision_id = uuid.uuid4()
    _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
    )

    first = _run(
        "seal_recent_publication",
        decision_id=decision_id,
        public_copy_revision="ko-v1",
    )
    second = _run(
        "seal_recent_publication",
        decision_id=decision_id,
        public_copy_revision="ko-v1",
    )

    actor = _operator()
    revision = PublicationRevision.objects.get(review_decision_id=decision_id)
    expected_prefix = " ".join(
        (
            "status=SEALED",
            f"publication_id={revision.id}",
            f"decision_id={decision_id}",
            f"parse_run_id={parse_run.id}",
            f"actor_id={actor.pk}",
        )
    )
    assert first == f"{expected_prefix} created=yes"
    assert second == f"{expected_prefix} created=no"
    assert LOCAL_OPERATOR_USERNAME not in first
    assert "ko-v1" not in first
    assert revision.sealed_at is not None
    assert revision.entries.count() == parse_run.accepted_row_count
    assert PublicationRevision.objects.count() == 1


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_seal_refuses_operator_permission_drift_before_publication() -> None:
    _run("bootstrap_local_phase0_operator")
    _source, parse_run = complete_generation()
    decision_id = uuid.uuid4()
    _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
    )
    actor = _operator()
    publish_permission = Permission.objects.get(
        content_type__app_label="grocery",
        content_type__model="publicationactivation",
        codename="publish_publication",
    )
    actor.user_permissions.remove(publish_permission)

    with pytest.raises(CommandError) as caught:
        _run(
            "seal_recent_publication",
            decision_id=decision_id,
            public_copy_revision="ko-v1",
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_OPERATOR_CONFLICT"
    assert not PublicationRevision.objects.exists()
