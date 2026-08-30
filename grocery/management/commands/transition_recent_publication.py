"""Atomically transition the local recent-retail publication pointer."""

from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import Final

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from grocery.management.local_phase0 import (
    LocalPhase0Error,
    canonical_actor_id,
    get_local_operator,
    require_sha256,
    require_uuid,
)
from grocery.models import PublicationActivation, transition_recent_publication

_NONE: Final = "NONE"
_NONNEGATIVE_INTEGER: Final = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_MAX_EXPECTED_VERSION: Final = (2**63) - 2

_REASON_CODES: Final[dict[str, str]] = {
    PublicationActivation.Operation.ACTIVATE: "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
    PublicationActivation.Operation.ROLLBACK: "LOCAL_PHASE0_PUBLICATION_ROLLED_BACK",
    PublicationActivation.Operation.WITHDRAW: "LOCAL_PHASE0_PUBLICATION_WITHDRAWN",
}
_STATUS_CODES: Final[dict[str, str]] = {
    PublicationActivation.Operation.ACTIVATE: "ACTIVATED",
    PublicationActivation.Operation.ROLLBACK: "ROLLED_BACK",
    PublicationActivation.Operation.WITHDRAW: "WITHDRAWN",
}


class _TransitionCode(StrEnum):
    OPERATION_INVALID = "LOCAL_PHASE0_OPERATION_INVALID"
    EXPECTED_VERSION_INVALID = "LOCAL_PHASE0_EXPECTED_VERSION_INVALID"
    EXPECTED_CURRENT_INVALID = "LOCAL_PHASE0_EXPECTED_CURRENT_INVALID"
    TARGET_INVALID = "LOCAL_PHASE0_TARGET_INVALID"
    TARGET_REQUIRED = "LOCAL_PHASE0_TARGET_REQUIRED"
    TARGET_FORBIDDEN = "LOCAL_PHASE0_TARGET_FORBIDDEN"
    TRANSITION_FAILED = "LOCAL_PHASE0_TRANSITION_FAILED"


class _TransitionError(RuntimeError):
    def __init__(self, code: _TransitionCode) -> None:
        self.code = code
        super().__init__(code.value)


def _operation(value: object) -> str:
    if isinstance(value, str) and value in _REASON_CODES:
        return value
    raise _TransitionError(_TransitionCode.OPERATION_INVALID)


def _expected_version(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str) and _NONNEGATIVE_INTEGER.fullmatch(value) is not None:
        parsed = int(value)
    else:
        raise _TransitionError(_TransitionCode.EXPECTED_VERSION_INVALID)
    if parsed < 0 or parsed > _MAX_EXPECTED_VERSION:
        raise _TransitionError(_TransitionCode.EXPECTED_VERSION_INVALID)
    return parsed


def _canonical_revision_or_none(
    value: object,
    *,
    invalid_code: _TransitionCode,
) -> uuid.UUID | None:
    if value == _NONE:
        return None
    try:
        return require_uuid(value)
    except LocalPhase0Error:
        raise _TransitionError(invalid_code) from None


def _target_revision(operation: str, value: object) -> uuid.UUID | None:
    if operation == PublicationActivation.Operation.WITHDRAW:
        if value not in {None, _NONE}:
            raise _TransitionError(_TransitionCode.TARGET_FORBIDDEN)
        return None
    if value in {None, _NONE}:
        raise _TransitionError(_TransitionCode.TARGET_REQUIRED)
    return _canonical_revision_or_none(value, invalid_code=_TransitionCode.TARGET_INVALID)


def _revision_text(value: uuid.UUID | None) -> str:
    return _NONE if value is None else str(value)


class Command(BaseCommand):
    help = "Activate, roll back, or withdraw the local recent-retail publication pointer."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--operation", required=True)
        parser.add_argument("--operation-id", required=True)
        parser.add_argument("--acceptance-evidence-sha256", required=True)
        parser.add_argument("--expected-version", required=True)
        parser.add_argument("--expected-current-revision", required=True)
        parser.add_argument("--target-revision")

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            operation = _operation(options.get("operation"))
            operation_id = require_uuid(options.get("operation_id"))
            evidence_hash = require_sha256(options.get("acceptance_evidence_sha256"))
            expected_version = _expected_version(options.get("expected_version"))
            expected_current = _canonical_revision_or_none(
                options.get("expected_current_revision"),
                invalid_code=_TransitionCode.EXPECTED_CURRENT_INVALID,
            )
            target = _target_revision(operation, options.get("target_revision"))
            activation, created, actor_id = self._transition(
                operation_id=operation_id,
                operation=operation,
                target_revision_id=target,
                expected_current_revision_id=expected_current,
                expected_version=expected_version,
                reason_code=_REASON_CODES[operation],
                evidence_hash=evidence_hash,
            )
        except LocalPhase0Error as error:
            raise CommandError(f"code={error.code.value}") from None
        except _TransitionError as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            raise CommandError(f"code={_TransitionCode.TRANSITION_FAILED.value}") from None

        self.stdout.write(
            " ".join(
                (
                    f"status={_STATUS_CODES[operation]}",
                    f"operation_id={activation.id}",
                    f"previous_revision_id={_revision_text(activation.previous_revision_id)}",
                    f"target_revision_id={_revision_text(activation.target_revision_id)}",
                    f"actor_id={actor_id}",
                    f"resulting_version={activation.sequence}",
                    f"created={'yes' if created else 'no'}",
                )
            )
        )

    @staticmethod
    @transaction.atomic
    def _transition(
        *,
        operation_id: uuid.UUID,
        operation: str,
        target_revision_id: uuid.UUID | None,
        expected_current_revision_id: uuid.UUID | None,
        expected_version: int,
        reason_code: str,
        evidence_hash: str,
    ) -> tuple[PublicationActivation, bool, int]:
        actor = get_local_operator(lock=True)
        try:
            activation, created = transition_recent_publication(
                operation_id=operation_id,
                actor=actor,
                operation=operation,
                target_revision_id=target_revision_id,
                expected_current_revision_id=expected_current_revision_id,
                expected_version=expected_version,
                reason_code=reason_code,
                acceptance_evidence_sha256=evidence_hash,
            )
        except Exception:
            raise _TransitionError(_TransitionCode.TRANSITION_FAILED) from None
        return activation, created, canonical_actor_id(actor)
