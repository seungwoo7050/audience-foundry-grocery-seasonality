"""PostgreSQL integration tests for the local publication transition command."""

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

from grocery.management.local_phase0 import LOCAL_OPERATOR_USERNAME
from grocery.models import (
    PublicationActivation,
    PublicationChannel,
    seal_recent_publication,
)
from grocery.tests.test_publication_revision_models import create_approved_generation

pytestmark = pytest.mark.django_db

_EVIDENCE_HASH = "8" * 64


def _run(name: str, **options: object) -> str:
    output = io.StringIO()
    call_command(name, stdout=output, **options)
    return output.getvalue().strip()


def _operator() -> Any:
    return get_user_model()._default_manager.get(username=LOCAL_OPERATOR_USERNAME)


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_activate_v1_v2_rollback_withdraw_and_replay_exact_operation() -> None:
    _run("bootstrap_local_phase0_operator")
    decision, _snapshots, _reviewer = create_approved_generation()
    v1 = seal_recent_publication(decision.id, "ko-v1")
    v2 = seal_recent_publication(decision.id, "ko-v2")
    activate_v1_id = uuid.uuid4()

    activate_v1 = _run(
        "transition_recent_publication",
        operation="ACTIVATE",
        operation_id=activate_v1_id,
        acceptance_evidence_sha256=_EVIDENCE_HASH,
        expected_version=0,
        expected_current_revision="NONE",
        target_revision=v1.id,
    )
    activate_v2 = _run(
        "transition_recent_publication",
        operation="ACTIVATE",
        operation_id=uuid.uuid4(),
        acceptance_evidence_sha256=_EVIDENCE_HASH,
        expected_version=1,
        expected_current_revision=v1.id,
        target_revision=v2.id,
    )
    rollback = _run(
        "transition_recent_publication",
        operation="ROLLBACK",
        operation_id=uuid.uuid4(),
        acceptance_evidence_sha256=_EVIDENCE_HASH,
        expected_version=2,
        expected_current_revision=v2.id,
        target_revision=v1.id,
    )
    withdraw = _run(
        "transition_recent_publication",
        operation="WITHDRAW",
        operation_id=uuid.uuid4(),
        acceptance_evidence_sha256=_EVIDENCE_HASH,
        expected_version=3,
        expected_current_revision=v1.id,
    )
    replay = _run(
        "transition_recent_publication",
        operation="ACTIVATE",
        operation_id=activate_v1_id,
        acceptance_evidence_sha256=_EVIDENCE_HASH,
        expected_version=0,
        expected_current_revision="NONE",
        target_revision=v1.id,
    )

    actor = _operator()
    assert activate_v1 == " ".join(
        (
            "status=ACTIVATED",
            f"operation_id={activate_v1_id}",
            "previous_revision_id=NONE",
            f"target_revision_id={v1.id}",
            f"actor_id={actor.pk}",
            "resulting_version=1",
            "created=yes",
        )
    )
    assert "status=ACTIVATED" in activate_v2 and "resulting_version=2" in activate_v2
    assert "status=ROLLED_BACK" in rollback and "resulting_version=3" in rollback
    assert "status=WITHDRAWN" in withdraw and "resulting_version=4" in withdraw
    assert replay == activate_v1.removesuffix("created=yes") + "created=no"
    assert all(
        _EVIDENCE_HASH not in receipt
        for receipt in (activate_v1, activate_v2, rollback, withdraw, replay)
    )

    channel = PublicationChannel.objects.get(pk="RECENT_RETAIL")
    assert channel.version == 4
    assert channel.current_revision_id is None
    activations = list(PublicationActivation.objects.order_by("sequence"))
    assert [activation.operation for activation in activations] == [
        "ACTIVATE",
        "ACTIVATE",
        "ROLLBACK",
        "WITHDRAW",
    ]
    assert [activation.reason_code for activation in activations] == [
        "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
        "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
        "LOCAL_PHASE0_PUBLICATION_ROLLED_BACK",
        "LOCAL_PHASE0_PUBLICATION_WITHDRAWN",
    ]
    assert all(activation.publisher_id == actor.pk for activation in activations)
    assert all(
        activation.acceptance_evidence_sha256 == _EVIDENCE_HASH for activation in activations
    )


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_stale_expectation_fails_without_pointer_or_event_mutation() -> None:
    _run("bootstrap_local_phase0_operator")
    decision, _snapshots, _reviewer = create_approved_generation()
    v1 = seal_recent_publication(decision.id, "ko-v1")
    v2 = seal_recent_publication(decision.id, "ko-v2")
    _run(
        "transition_recent_publication",
        operation="ACTIVATE",
        operation_id=uuid.uuid4(),
        acceptance_evidence_sha256=_EVIDENCE_HASH,
        expected_version=0,
        expected_current_revision="NONE",
        target_revision=v1.id,
    )

    with pytest.raises(CommandError) as caught:
        _run(
            "transition_recent_publication",
            operation="ACTIVATE",
            operation_id=uuid.uuid4(),
            acceptance_evidence_sha256=_EVIDENCE_HASH,
            expected_version=0,
            expected_current_revision=v1.id,
            target_revision=v2.id,
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_TRANSITION_FAILED"
    channel = PublicationChannel.objects.get(pk="RECENT_RETAIL")
    assert (channel.version, channel.current_revision_id) == (1, v1.id)
    assert PublicationActivation.objects.count() == 1


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_extra_operator_permission_is_rejected_before_channel_bootstrap() -> None:
    _run("bootstrap_local_phase0_operator")
    decision, _snapshots, _reviewer = create_approved_generation()
    revision = seal_recent_publication(decision.id, "ko-v1")
    actor = _operator()
    actor.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="auth",
            content_type__model="user",
            codename="view_user",
        )
    )

    with pytest.raises(CommandError) as caught:
        _run(
            "transition_recent_publication",
            operation="ACTIVATE",
            operation_id=uuid.uuid4(),
            acceptance_evidence_sha256=_EVIDENCE_HASH,
            expected_version=0,
            expected_current_revision="NONE",
            target_revision=revision.id,
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_OPERATOR_CONFLICT"
    assert not PublicationChannel.objects.exists()
    assert not PublicationActivation.objects.exists()


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_production_settings_are_rejected_before_channel_bootstrap() -> None:
    _run("bootstrap_local_phase0_operator")
    decision, _snapshots, _reviewer = create_approved_generation()
    revision = seal_recent_publication(decision.id, "ko-v1")

    with (
        override_settings(DEBUG=False, ADMIN_ENABLED=False),
        pytest.raises(CommandError) as caught,
    ):
        _run(
            "transition_recent_publication",
            operation="ACTIVATE",
            operation_id=uuid.uuid4(),
            acceptance_evidence_sha256=_EVIDENCE_HASH,
            expected_version=0,
            expected_current_revision="NONE",
            target_revision=revision.id,
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_ENVIRONMENT_DENIED"
    assert not PublicationChannel.objects.exists()
    assert not PublicationActivation.objects.exists()
