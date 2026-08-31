"""Database-free command-boundary tests for local Phase 0 operations."""

from __future__ import annotations

import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from grocery.management.local_phase0 import (
    LocalPhase0Code,
    LocalPhase0Error,
    require_local_phase0_environment,
)

_APPROVAL_HASH = "7" * 64


@override_settings(DEBUG=True, ADMIN_ENABLED=False)
def test_local_environment_gate_accepts_only_debug_with_admin_disabled() -> None:
    require_local_phase0_environment()


@pytest.mark.parametrize(
    ("debug", "admin_enabled"),
    ((False, False), (True, True), (False, True)),
)
def test_local_environment_gate_denies_every_nonlocal_combination(
    debug: bool,
    admin_enabled: bool,
) -> None:
    with (
        override_settings(DEBUG=debug, ADMIN_ENABLED=admin_enabled),
        pytest.raises(LocalPhase0Error) as caught,
    ):
        require_local_phase0_environment()

    assert caught.value.code is LocalPhase0Code.ENVIRONMENT_DENIED


def test_bootstrap_command_receipt_contains_only_status_actor_id_and_created() -> None:
    output = io.StringIO()
    actor = SimpleNamespace(pk=17)
    with (
        patch(
            "grocery.management.commands.bootstrap_local_phase0_operator.bootstrap_local_operator",
            return_value=(actor, True),
        ),
        patch(
            "grocery.management.commands.bootstrap_local_phase0_operator.canonical_actor_id",
            return_value=17,
        ),
    ):
        call_command("bootstrap_local_phase0_operator", stdout=output)

    assert output.getvalue().strip() == "status=READY actor_id=17 created=yes"
    assert "phase0-local-operator" not in output.getvalue()


def test_bootstrap_arbitrary_failure_is_not_reflected() -> None:
    marker = "private-environment-marker"
    with (
        patch(
            "grocery.management.commands.bootstrap_local_phase0_operator.bootstrap_local_operator",
            side_effect=RuntimeError(marker),
        ),
        pytest.raises(CommandError) as caught,
    ):
        call_command("bootstrap_local_phase0_operator")

    assert str(caught.value) == "code=LOCAL_PHASE0_PERSISTENCE_FAILED"
    assert marker not in str(caught.value)


def test_approve_command_passes_validated_identifiers_and_emits_bounded_receipt() -> None:
    output = io.StringIO()
    parse_run_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    source_id = uuid.uuid4()
    decision = SimpleNamespace(
        id=decision_id,
        parse_run_id=parse_run_id,
        source_artifact_id=artifact_id,
        source_configuration_id=source_id,
    )
    with patch(
        "grocery.management.commands.approve_recent_generation.Command._approve",
        return_value=(decision, True, 19),
    ) as approve:
        call_command(
            "approve_recent_generation",
            parse_run_id=parse_run_id,
            decision_id=decision_id,
            acceptance_evidence_sha256=_APPROVAL_HASH,
            stdout=output,
        )

    approve.assert_called_once_with(
        parse_run_id=parse_run_id,
        decision_id=decision_id,
        acceptance_hash=_APPROVAL_HASH,
    )
    assert output.getvalue().strip() == " ".join(
        (
            "status=APPROVED",
            f"decision_id={decision_id}",
            f"parse_run_id={parse_run_id}",
            f"artifact_id={artifact_id}",
            f"source_configuration_id={source_id}",
            "actor_id=19",
            "created=yes",
        )
    )
    assert _APPROVAL_HASH not in output.getvalue()


@pytest.mark.parametrize(
    ("parse_run_id", "decision_id", "acceptance_hash", "expected_code"),
    (
        ("private-parse-id", uuid.uuid4(), _APPROVAL_HASH, "LOCAL_PHASE0_UUID_INVALID"),
        (uuid.uuid4(), "private-decision-id", _APPROVAL_HASH, "LOCAL_PHASE0_UUID_INVALID"),
        (uuid.uuid4(), uuid.uuid4(), "private-hash", "LOCAL_PHASE0_SHA256_INVALID"),
    ),
)
def test_approve_invalid_input_fails_without_echo_before_service(
    parse_run_id: object,
    decision_id: object,
    acceptance_hash: object,
    expected_code: str,
) -> None:
    with (
        patch("grocery.management.commands.approve_recent_generation.Command._approve") as approve,
        pytest.raises(CommandError) as caught,
    ):
        call_command(
            "approve_recent_generation",
            parse_run_id=parse_run_id,
            decision_id=decision_id,
            acceptance_evidence_sha256=acceptance_hash,
        )

    approve.assert_not_called()
    assert str(caught.value) == f"code={expected_code}"
    assert "private" not in str(caught.value)


def test_approve_arbitrary_service_failure_is_not_reflected() -> None:
    marker = "private-review-marker"
    with (
        patch(
            "grocery.management.commands.approve_recent_generation.Command._approve",
            side_effect=RuntimeError(marker),
        ),
        pytest.raises(CommandError) as caught,
    ):
        call_command(
            "approve_recent_generation",
            parse_run_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            acceptance_evidence_sha256=_APPROVAL_HASH,
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_REVIEW_FAILED"
    assert marker not in str(caught.value)


def test_seal_command_accepts_only_allowlisted_copy_and_emits_bounded_receipt() -> None:
    output = io.StringIO()
    decision_id = uuid.uuid4()
    publication_id = uuid.uuid4()
    parse_run_id = uuid.uuid4()
    revision = SimpleNamespace(
        id=publication_id,
        review_decision_id=decision_id,
        generation_id=parse_run_id,
    )
    with patch(
        "grocery.management.commands.seal_recent_publication.Command._seal",
        return_value=(revision, False, 23),
    ) as seal:
        call_command(
            "seal_recent_publication",
            decision_id=decision_id,
            public_copy_revision="ko-v4",
            stdout=output,
        )

    seal.assert_called_once_with(decision_id=decision_id, copy_revision="ko-v4")
    assert output.getvalue().strip() == " ".join(
        (
            "status=SEALED",
            f"publication_id={publication_id}",
            f"decision_id={decision_id}",
            f"parse_run_id={parse_run_id}",
            "actor_id=23",
            "created=no",
        )
    )
    assert "ko-v4" not in output.getvalue()


@pytest.mark.parametrize("copy_revision", ("ko-v5", "KO-V1", "private-copy"))
def test_seal_rejects_nonallowlisted_copy_without_echo(
    copy_revision: str,
) -> None:
    with (
        patch("grocery.management.commands.seal_recent_publication.Command._seal") as seal,
        pytest.raises(CommandError) as caught,
    ):
        call_command(
            "seal_recent_publication",
            decision_id=uuid.uuid4(),
            public_copy_revision=copy_revision,
        )

    seal.assert_not_called()
    assert str(caught.value) == "code=LOCAL_PHASE0_COPY_REVISION_INVALID"
    assert copy_revision not in str(caught.value)


def test_seal_arbitrary_service_failure_is_not_reflected() -> None:
    marker = "private-publication-marker"
    with (
        patch(
            "grocery.management.commands.seal_recent_publication.Command._seal",
            side_effect=RuntimeError(marker),
        ),
        pytest.raises(CommandError) as caught,
    ):
        call_command(
            "seal_recent_publication",
            decision_id=uuid.uuid4(),
            public_copy_revision="ko-v1",
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_PUBLICATION_FAILED"
    assert marker not in str(caught.value)
