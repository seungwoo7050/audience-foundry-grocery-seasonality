"""Database-free boundary tests for the local publication transition command."""

from __future__ import annotations

import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

_EVIDENCE_HASH = "8" * 64
_COMMAND = "grocery.management.commands.transition_recent_publication.Command._transition"


def _run(**options: object) -> str:
    output = io.StringIO()
    call_command("transition_recent_publication", stdout=output, **options)
    return output.getvalue().strip()


@pytest.mark.parametrize(
    (
        "operation",
        "expected_current",
        "target",
        "previous_id",
        "target_id",
        "reason_code",
        "status",
    ),
    (
        (
            "ACTIVATE",
            "NONE",
            uuid.uuid4(),
            None,
            None,
            "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
            "ACTIVATED",
        ),
        (
            "ROLLBACK",
            uuid.uuid4(),
            uuid.uuid4(),
            None,
            None,
            "LOCAL_PHASE0_PUBLICATION_ROLLED_BACK",
            "ROLLED_BACK",
        ),
        (
            "WITHDRAW",
            uuid.uuid4(),
            None,
            None,
            None,
            "LOCAL_PHASE0_PUBLICATION_WITHDRAWN",
            "WITHDRAWN",
        ),
    ),
)
def test_valid_operation_uses_fixed_reason_and_emits_only_safe_identity_receipt(
    operation: str,
    expected_current: str | uuid.UUID,
    target: uuid.UUID | None,
    previous_id: uuid.UUID | None,
    target_id: uuid.UUID | None,
    reason_code: str,
    status: str,
) -> None:
    operation_id = uuid.uuid4()
    parsed_expected = None if expected_current == "NONE" else expected_current
    parsed_target = target
    activation = SimpleNamespace(
        id=operation_id,
        previous_revision_id=parsed_expected if previous_id is None else previous_id,
        target_revision_id=parsed_target if target_id is None else target_id,
        sequence=4,
    )
    options: dict[str, object] = {
        "operation": operation,
        "operation_id": operation_id,
        "acceptance_evidence_sha256": _EVIDENCE_HASH,
        "expected_version": 3,
        "expected_current_revision": expected_current,
    }
    if target is not None:
        options["target_revision"] = target

    with patch(_COMMAND, return_value=(activation, True, 31)) as transition:
        receipt = _run(**options)

    transition.assert_called_once_with(
        operation_id=operation_id,
        operation=operation,
        target_revision_id=parsed_target,
        expected_current_revision_id=parsed_expected,
        expected_version=3,
        reason_code=reason_code,
        evidence_hash=_EVIDENCE_HASH,
    )
    assert receipt == " ".join(
        (
            f"status={status}",
            f"operation_id={operation_id}",
            f"previous_revision_id={'NONE' if parsed_expected is None else parsed_expected}",
            f"target_revision_id={'NONE' if parsed_target is None else parsed_target}",
            "actor_id=31",
            "resulting_version=4",
            "created=yes",
        )
    )
    assert _EVIDENCE_HASH not in receipt
    assert reason_code not in receipt


def test_exact_operation_replay_is_explicit() -> None:
    operation_id = uuid.uuid4()
    target_id = uuid.uuid4()
    activation = SimpleNamespace(
        id=operation_id,
        previous_revision_id=None,
        target_revision_id=target_id,
        sequence=1,
    )
    with patch(_COMMAND, return_value=(activation, False, 37)):
        receipt = _run(
            operation="ACTIVATE",
            operation_id=operation_id,
            acceptance_evidence_sha256=_EVIDENCE_HASH,
            expected_version="0",
            expected_current_revision="NONE",
            target_revision=target_id,
        )

    assert receipt.endswith("resulting_version=1 created=no")


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    (
        ({"operation": "private-operation"}, "LOCAL_PHASE0_OPERATION_INVALID"),
        ({"operation_id": "private-operation-id"}, "LOCAL_PHASE0_UUID_INVALID"),
        ({"acceptance_evidence_sha256": "private-hash"}, "LOCAL_PHASE0_SHA256_INVALID"),
        ({"expected_version": -1}, "LOCAL_PHASE0_EXPECTED_VERSION_INVALID"),
        ({"expected_version": "01"}, "LOCAL_PHASE0_EXPECTED_VERSION_INVALID"),
        (
            {"expected_current_revision": "private-current"},
            "LOCAL_PHASE0_EXPECTED_CURRENT_INVALID",
        ),
        ({"target_revision": None}, "LOCAL_PHASE0_TARGET_REQUIRED"),
        ({"target_revision": "NONE"}, "LOCAL_PHASE0_TARGET_REQUIRED"),
        ({"target_revision": "private-target"}, "LOCAL_PHASE0_TARGET_INVALID"),
    ),
)
def test_invalid_activate_input_fails_before_service_without_echo(
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    options: dict[str, object] = {
        "operation": "ACTIVATE",
        "operation_id": uuid.uuid4(),
        "acceptance_evidence_sha256": _EVIDENCE_HASH,
        "expected_version": 0,
        "expected_current_revision": "NONE",
        "target_revision": uuid.uuid4(),
    }
    options.update(overrides)
    with patch(_COMMAND) as transition, pytest.raises(CommandError) as caught:
        _run(**options)

    transition.assert_not_called()
    assert str(caught.value) == f"code={expected_code}"
    assert "private" not in str(caught.value)


def test_withdraw_rejects_target_revision_before_service() -> None:
    with patch(_COMMAND) as transition, pytest.raises(CommandError) as caught:
        _run(
            operation="WITHDRAW",
            operation_id=uuid.uuid4(),
            acceptance_evidence_sha256=_EVIDENCE_HASH,
            expected_version=1,
            expected_current_revision=uuid.uuid4(),
            target_revision=uuid.uuid4(),
        )

    transition.assert_not_called()
    assert str(caught.value) == "code=LOCAL_PHASE0_TARGET_FORBIDDEN"


def test_arbitrary_transition_failure_is_not_reflected() -> None:
    marker = "private-secret-or-database-marker"
    with (
        patch(_COMMAND, side_effect=RuntimeError(marker)),
        pytest.raises(CommandError) as caught,
    ):
        _run(
            operation="ACTIVATE",
            operation_id=uuid.uuid4(),
            acceptance_evidence_sha256=_EVIDENCE_HASH,
            expected_version=0,
            expected_current_revision="NONE",
            target_revision=uuid.uuid4(),
        )

    assert str(caught.value) == "code=LOCAL_PHASE0_TRANSITION_FAILED"
    assert marker not in str(caught.value)
