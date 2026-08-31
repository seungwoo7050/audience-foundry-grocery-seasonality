"""Focused production control-plane boundary and lifecycle command tests."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from grocery.management.commands.approve_recent_generation import Command as ApproveCommand
from grocery.management.commands.seal_recent_publication import Command as SealCommand
from grocery.management.commands.transition_recent_publication import (
    Command as TransitionCommand,
)
from grocery.management.control_plane import (
    CONTROL_APPROVAL_REASON_CODE,
    CONTROL_PUBLISHER_USERNAME,
    CONTROL_REVIEWER_USERNAME,
    CONTROL_TRANSITION_REASON_CODES,
    ControlPlaneCode,
    ControlPlaneError,
    require_production_operation_environment,
)
from grocery.models import (
    PublicationActivation,
    PublicationChannel,
    PublicationRevision,
    ReviewDecision,
)
from grocery.tests.test_review_decision_models import complete_generation

_RELEASE_SHA = "a" * 40
_OTHER_RELEASE_SHA = "b" * 40
_ACCEPTANCE_HASH = "7" * 64
_TRANSITION_HASH = "8" * 64
_PRODUCTION_SETTINGS: dict[str, object] = {
    "DEBUG": False,
    "ADMIN_ENABLED": False,
    "QA_STATE_PREVIEWS_ENABLED": False,
    "CONTROL_PLANE_OPERATIONS_ENABLED": True,
    "DEPLOY_VERSION": _RELEASE_SHA,
}


def _run(name: str, **options: object) -> str:
    output = io.StringIO()
    call_command(name, stdout=output, **options)
    return output.getvalue().strip()


def _user(username: str) -> Any:
    return get_user_model()._default_manager.get(username=username)


def _permission_contract(actor: Any) -> set[tuple[str, str, str]]:
    return set(
        actor.user_permissions.values_list(
            "content_type__app_label",
            "content_type__model",
            "codename",
        )
    )


@contextmanager
def _production_settings(**changes: object) -> Iterator[None]:
    values = {**_PRODUCTION_SETTINGS, **changes}
    with override_settings(**values):
        yield


def test_production_precondition_requires_all_flags_and_exact_running_release() -> None:
    with _production_settings():
        require_production_operation_environment(_RELEASE_SHA)

    denied_settings = (
        {"DEBUG": True},
        {"ADMIN_ENABLED": True},
        {"QA_STATE_PREVIEWS_ENABLED": True},
        {"CONTROL_PLANE_OPERATIONS_ENABLED": False},
    )
    for changes in denied_settings:
        with (
            _production_settings(**changes),
            pytest.raises(ControlPlaneError) as caught,
        ):
            require_production_operation_environment(_RELEASE_SHA)
        assert caught.value.code is ControlPlaneCode.ENVIRONMENT_DENIED

    invalid_release_marker = "private-release-marker"
    with _production_settings(), pytest.raises(ControlPlaneError) as caught:
        require_production_operation_environment(invalid_release_marker)
    assert caught.value.code is ControlPlaneCode.RELEASE_SHA_INVALID
    assert invalid_release_marker not in str(caught.value)

    with _production_settings(), pytest.raises(ControlPlaneError) as caught:
        require_production_operation_environment(_OTHER_RELEASE_SHA)
    assert caught.value.code is ControlPlaneCode.RELEASE_SHA_MISMATCH
    assert _OTHER_RELEASE_SHA not in str(caught.value)


def test_write_commands_expose_release_lock_but_no_actor_override() -> None:
    commands = (
        ("approve_recent_generation", ApproveCommand()),
        ("seal_recent_publication", SealCommand()),
        ("transition_recent_publication", TransitionCommand()),
    )
    for name, command in commands:
        parser = command.create_parser("manage.py", name)
        destinations = {action.dest for action in parser._actions}
        assert "expected_release_sha" in destinations
        assert "actor" not in destinations
        assert "actor_id" not in destinations
        assert "username" not in destinations


@override_settings(**_PRODUCTION_SETTINGS)
def test_production_write_commands_require_release_before_service_dispatch() -> None:
    calls = (
        (
            "approve_recent_generation",
            "grocery.management.commands.approve_recent_generation.Command._approve",
            {
                "parse_run_id": uuid.uuid4(),
                "decision_id": uuid.uuid4(),
                "acceptance_evidence_sha256": _ACCEPTANCE_HASH,
            },
        ),
        (
            "seal_recent_publication",
            "grocery.management.commands.seal_recent_publication.Command._seal",
            {"decision_id": uuid.uuid4(), "public_copy_revision": "ko-v1"},
        ),
        (
            "transition_recent_publication",
            "grocery.management.commands.transition_recent_publication.Command._transition",
            {
                "operation": "ACTIVATE",
                "operation_id": uuid.uuid4(),
                "acceptance_evidence_sha256": _TRANSITION_HASH,
                "expected_version": 0,
                "expected_current_revision": "NONE",
                "target_revision": uuid.uuid4(),
            },
        ),
    )
    for command_name, service_path, options in calls:
        with patch(service_path) as service, pytest.raises(CommandError) as caught:
            _run(command_name, **options)
        service.assert_not_called()
        assert str(caught.value) == "code=CONTROL_PLANE_RELEASE_SHA_INVALID"


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_bootstrap_creates_exact_nonlogin_roles_and_replays_idempotently() -> None:
    first = _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)
    second = _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)

    reviewer = _user(CONTROL_REVIEWER_USERNAME)
    publisher = _user(CONTROL_PUBLISHER_USERNAME)
    assert first == " ".join(
        (
            "status=READY",
            "review_actor=READY",
            "review_created=yes",
            "publication_actor=READY",
            "publication_created=yes",
        )
    )
    assert second == first.replace("created=yes", "created=no")
    assert CONTROL_REVIEWER_USERNAME not in first
    assert CONTROL_PUBLISHER_USERNAME not in first
    assert _RELEASE_SHA not in first

    for actor in (reviewer, publisher):
        assert actor.email == actor.first_name == actor.last_name == ""
        assert actor.is_active is True
        assert actor.is_staff is False
        assert actor.is_superuser is False
        assert actor.has_usable_password() is False
        assert actor.groups.count() == 0
    assert _permission_contract(reviewer) == {
        ("grocery", "reviewdecision", "review_generation"),
        (
            "grocery",
            "historicalcollectionreviewdecision",
            "review_historical_collection",
        ),
    }
    assert _permission_contract(publisher) == {
        ("grocery", "publicationactivation", "publish_publication"),
        (
            "grocery",
            "historicalretailpublicationchannel",
            "publish_historical_publication",
        ),
    }


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_bootstrap_expands_existing_recent_only_actors_to_historical_roles() -> None:
    contracts = (
        (CONTROL_REVIEWER_USERNAME, "review_generation"),
        (CONTROL_PUBLISHER_USERNAME, "publish_publication"),
    )
    for username, codename in contracts:
        actor = get_user_model()._default_manager.create_user(
            username=username,
            password=None,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        actor.user_permissions.add(Permission.objects.get(codename=codename))

    receipt = _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)

    assert "review_created=no" in receipt and "publication_created=no" in receipt
    assert _permission_contract(_user(CONTROL_REVIEWER_USERNAME)) == {
        ("grocery", "reviewdecision", "review_generation"),
        (
            "grocery",
            "historicalcollectionreviewdecision",
            "review_historical_collection",
        ),
    }
    assert _permission_contract(_user(CONTROL_PUBLISHER_USERNAME)) == {
        ("grocery", "publicationactivation", "publish_publication"),
        (
            "grocery",
            "historicalretailpublicationchannel",
            "publish_historical_publication",
        ),
    }


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_bootstrap_release_mismatch_and_existing_drift_never_partially_mutate() -> None:
    with pytest.raises(CommandError) as caught:
        _run("bootstrap_control_plane_actors", expected_release_sha=_OTHER_RELEASE_SHA)
    assert str(caught.value) == "code=CONTROL_PLANE_RELEASE_SHA_MISMATCH"
    assert _OTHER_RELEASE_SHA not in str(caught.value)
    assert (
        not get_user_model()
        ._default_manager.filter(
            username__in=(CONTROL_REVIEWER_USERNAME, CONTROL_PUBLISHER_USERNAME)
        )
        .exists()
    )

    drift_marker = "private-actor-drift-marker@example.invalid"
    reviewer = get_user_model()._default_manager.create_user(
        username=CONTROL_REVIEWER_USERNAME,
        email=drift_marker,
        password=None,
        is_active=True,
        is_staff=False,
        is_superuser=False,
    )
    reviewer.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="grocery",
            content_type__model="reviewdecision",
            codename="review_generation",
        )
    )

    with pytest.raises(CommandError) as caught:
        _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)

    reviewer.refresh_from_db()
    assert str(caught.value) == "code=CONTROL_PLANE_ACTOR_CONFLICT"
    assert drift_marker not in str(caught.value)
    assert reviewer.email == drift_marker
    assert (
        not get_user_model()._default_manager.filter(username=CONTROL_PUBLISHER_USERNAME).exists()
    )


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_production_lifecycle_uses_separate_roles_reasons_and_idempotent_services() -> None:
    _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)
    source, parse_run = complete_generation()
    decision_id = uuid.uuid4()

    approval_first = _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
        expected_release_sha=_RELEASE_SHA,
    )
    approval_replay = _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
        expected_release_sha=_RELEASE_SHA,
    )
    seal_first = _run(
        "seal_recent_publication",
        decision_id=decision_id,
        public_copy_revision="ko-v1",
        expected_release_sha=_RELEASE_SHA,
    )
    seal_replay = _run(
        "seal_recent_publication",
        decision_id=decision_id,
        public_copy_revision="ko-v1",
        expected_release_sha=_RELEASE_SHA,
    )
    revision = PublicationRevision.objects.get(review_decision_id=decision_id)
    operation_id = uuid.uuid4()
    activation_first = _run(
        "transition_recent_publication",
        operation="ACTIVATE",
        operation_id=operation_id,
        acceptance_evidence_sha256=_TRANSITION_HASH,
        expected_version=0,
        expected_current_revision="NONE",
        target_revision=revision.id,
        expected_release_sha=_RELEASE_SHA,
    )
    activation_replay = _run(
        "transition_recent_publication",
        operation="ACTIVATE",
        operation_id=operation_id,
        acceptance_evidence_sha256=_TRANSITION_HASH,
        expected_version=0,
        expected_current_revision="NONE",
        target_revision=revision.id,
        expected_release_sha=_RELEASE_SHA,
    )

    reviewer = _user(CONTROL_REVIEWER_USERNAME)
    publisher = _user(CONTROL_PUBLISHER_USERNAME)
    decision = ReviewDecision.objects.get(pk=decision_id)
    activation = PublicationActivation.objects.get(pk=operation_id)
    assert decision.reviewer_id == reviewer.pk
    assert decision.reason_code == CONTROL_APPROVAL_REASON_CODE
    assert decision.source_configuration_id == source.id
    assert activation.publisher_id == publisher.pk
    assert activation.reason_code == CONTROL_TRANSITION_REASON_CODES["ACTIVATE"]
    assert approval_first.endswith("created=yes")
    assert approval_replay == approval_first.removesuffix("created=yes") + "created=no"
    assert seal_first.endswith("created=yes")
    assert seal_replay == seal_first.removesuffix("created=yes") + "created=no"
    assert activation_first.endswith("created=yes")
    assert activation_replay == activation_first.removesuffix("created=yes") + "created=no"

    all_receipts = "\n".join(
        (
            approval_first,
            approval_replay,
            seal_first,
            seal_replay,
            activation_first,
            activation_replay,
        )
    )
    for forbidden in (
        CONTROL_REVIEWER_USERNAME,
        CONTROL_PUBLISHER_USERNAME,
        "actor_id=",
        _RELEASE_SHA,
        _ACCEPTANCE_HASH,
        _TRANSITION_HASH,
    ):
        assert forbidden not in all_receipts


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_release_mismatch_blocks_each_write_command_before_lifecycle_mutation() -> None:
    _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)
    _source, parse_run = complete_generation()
    decision_id = uuid.uuid4()

    with pytest.raises(CommandError) as caught:
        _run(
            "approve_recent_generation",
            parse_run_id=parse_run.id,
            decision_id=decision_id,
            acceptance_evidence_sha256=_ACCEPTANCE_HASH,
            expected_release_sha=_OTHER_RELEASE_SHA,
        )
    assert str(caught.value) == "code=CONTROL_PLANE_RELEASE_SHA_MISMATCH"
    assert not ReviewDecision.objects.exists()

    _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
        expected_release_sha=_RELEASE_SHA,
    )
    with pytest.raises(CommandError) as caught:
        _run(
            "seal_recent_publication",
            decision_id=decision_id,
            public_copy_revision="ko-v1",
            expected_release_sha=_OTHER_RELEASE_SHA,
        )
    assert str(caught.value) == "code=CONTROL_PLANE_RELEASE_SHA_MISMATCH"
    assert not PublicationRevision.objects.exists()

    revision_receipt = _run(
        "seal_recent_publication",
        decision_id=decision_id,
        public_copy_revision="ko-v1",
        expected_release_sha=_RELEASE_SHA,
    )
    assert "status=SEALED" in revision_receipt
    revision = PublicationRevision.objects.get()
    with pytest.raises(CommandError) as caught:
        _run(
            "transition_recent_publication",
            operation="ACTIVATE",
            operation_id=uuid.uuid4(),
            acceptance_evidence_sha256=_TRANSITION_HASH,
            expected_version=0,
            expected_current_revision="NONE",
            target_revision=revision.id,
            expected_release_sha=_OTHER_RELEASE_SHA,
        )
    assert str(caught.value) == "code=CONTROL_PLANE_RELEASE_SHA_MISMATCH"
    assert not PublicationChannel.objects.exists()
    assert not PublicationActivation.objects.exists()
    assert _OTHER_RELEASE_SHA not in str(caught.value)


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_publisher_is_never_substituted_for_missing_or_drifted_reviewer() -> None:
    _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)
    _user(CONTROL_REVIEWER_USERNAME).delete()
    _source, parse_run = complete_generation()

    with pytest.raises(CommandError) as caught:
        _run(
            "approve_recent_generation",
            parse_run_id=parse_run.id,
            decision_id=uuid.uuid4(),
            acceptance_evidence_sha256=_ACCEPTANCE_HASH,
            expected_release_sha=_RELEASE_SHA,
        )
    assert str(caught.value) == "code=CONTROL_PLANE_ACTOR_MISSING"
    assert _user(CONTROL_PUBLISHER_USERNAME).has_perm("grocery.review_generation") is False
    assert not ReviewDecision.objects.exists()


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_reviewer_is_never_substituted_for_missing_publisher_on_seal_or_transition() -> None:
    _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)
    _source, parse_run = complete_generation()
    decision_id = uuid.uuid4()
    _run(
        "approve_recent_generation",
        parse_run_id=parse_run.id,
        decision_id=decision_id,
        acceptance_evidence_sha256=_ACCEPTANCE_HASH,
        expected_release_sha=_RELEASE_SHA,
    )
    _run(
        "seal_recent_publication",
        decision_id=decision_id,
        public_copy_revision="ko-v1",
        expected_release_sha=_RELEASE_SHA,
    )
    revision = PublicationRevision.objects.get()
    _user(CONTROL_PUBLISHER_USERNAME).delete()

    with pytest.raises(CommandError) as seal_error:
        _run(
            "seal_recent_publication",
            decision_id=decision_id,
            public_copy_revision="ko-v2",
            expected_release_sha=_RELEASE_SHA,
        )
    with pytest.raises(CommandError) as transition_error:
        _run(
            "transition_recent_publication",
            operation="ACTIVATE",
            operation_id=uuid.uuid4(),
            acceptance_evidence_sha256=_TRANSITION_HASH,
            expected_version=0,
            expected_current_revision="NONE",
            target_revision=revision.id,
            expected_release_sha=_RELEASE_SHA,
        )

    assert str(seal_error.value) == "code=CONTROL_PLANE_ACTOR_MISSING"
    assert str(transition_error.value) == "code=CONTROL_PLANE_ACTOR_MISSING"
    assert _user(CONTROL_REVIEWER_USERNAME).has_perm("grocery.publish_publication") is False
    assert PublicationRevision.objects.count() == 1
    assert not PublicationChannel.objects.exists()
    assert not PublicationActivation.objects.exists()


@pytest.mark.django_db
@override_settings(**_PRODUCTION_SETTINGS)
def test_actor_drift_error_is_redacted_and_approval_does_not_mutate() -> None:
    _run("bootstrap_control_plane_actors", expected_release_sha=_RELEASE_SHA)
    drift_marker = "private-review-drift@example.invalid"
    get_user_model()._default_manager.filter(username=CONTROL_REVIEWER_USERNAME).update(
        email=drift_marker
    )
    _source, parse_run = complete_generation()

    with pytest.raises(CommandError) as caught:
        _run(
            "approve_recent_generation",
            parse_run_id=parse_run.id,
            decision_id=uuid.uuid4(),
            acceptance_evidence_sha256=_ACCEPTANCE_HASH,
            expected_release_sha=_RELEASE_SHA,
        )

    assert str(caught.value) == "code=CONTROL_PLANE_ACTOR_CONFLICT"
    assert drift_marker not in str(caught.value)
    assert CONTROL_REVIEWER_USERNAME not in str(caught.value)
    assert _RELEASE_SHA not in str(caught.value)
    assert _ACCEPTANCE_HASH not in str(caught.value)
    assert not ReviewDecision.objects.exists()
