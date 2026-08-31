"""CAS transition for the independent historical retail publication channel."""

from __future__ import annotations

import re
import uuid
from enum import StrEnum

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from grocery.historical_activation_models import HistoricalRetailPublicationActivation
from grocery.historical_activations import transition_historical_publication
from grocery.management.control_plane import (
    ControlPlaneCode,
    ControlPlaneError,
    preflight_operation,
    resolve_operation_actor,
)
from grocery.management.local_phase0 import (
    LocalPhase0Error,
    require_sha256,
    require_uuid,
)

_NONE = "NONE"
_NONNEGATIVE_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_REASON_CODES = {
    False: {
        "ACTIVATE": "LOCAL_HISTORICAL_PUBLICATION_ACTIVATED",
        "ROLLBACK": "LOCAL_HISTORICAL_PUBLICATION_ROLLED_BACK",
        "WITHDRAW": "LOCAL_HISTORICAL_PUBLICATION_WITHDRAWN",
    },
    True: {
        "ACTIVATE": "CONTROL_PLANE_HISTORICAL_PUBLICATION_ACTIVATED",
        "ROLLBACK": "CONTROL_PLANE_HISTORICAL_PUBLICATION_ROLLED_BACK",
        "WITHDRAW": "CONTROL_PLANE_HISTORICAL_PUBLICATION_WITHDRAWN",
    },
}
_STATUS_CODES = {
    "ACTIVATE": "ACTIVATED",
    "ROLLBACK": "ROLLED_BACK",
    "WITHDRAW": "WITHDRAWN",
}


class _Code(StrEnum):
    INPUT_INVALID = "HISTORICAL_PUBLICATION_INPUT_INVALID"
    TRANSITION_FAILED = "HISTORICAL_PUBLICATION_TRANSITION_FAILED"


class _CommandFailure(RuntimeError):
    def __init__(self, code: _Code) -> None:
        self.code = code
        super().__init__(code.value)


def _operation(value: object) -> str:
    if isinstance(value, str) and value in _STATUS_CODES:
        return value
    raise _CommandFailure(_Code.INPUT_INVALID)


def _expected_version(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str) and _NONNEGATIVE_INTEGER.fullmatch(value):
        parsed = int(value)
    else:
        raise _CommandFailure(_Code.INPUT_INVALID)
    if parsed < 0 or parsed > (2**63) - 2:
        raise _CommandFailure(_Code.INPUT_INVALID)
    return parsed


def _revision(value: object) -> uuid.UUID | None:
    return None if value == _NONE else require_uuid(value)


def _target(operation: str, value: object) -> uuid.UUID | None:
    if operation == HistoricalRetailPublicationActivation.Operation.WITHDRAW:
        if value not in (None, _NONE):
            raise _CommandFailure(_Code.INPUT_INVALID)
        return None
    if value in (None, _NONE):
        raise _CommandFailure(_Code.INPUT_INVALID)
    return require_uuid(value)


class Command(BaseCommand):
    help = (
        "Activate, roll back, or withdraw the historical retail publication. Production "
        "requires an external-MFA private job; the control-plane flag is not authentication."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--operation", required=True)
        parser.add_argument("--operation-id", required=True)
        parser.add_argument("--acceptance-evidence-sha256", required=True)
        parser.add_argument("--expected-version", required=True)
        parser.add_argument("--expected-current-revision", required=True)
        parser.add_argument("--target-revision")
        parser.add_argument("--expected-release-sha")

    def handle(self, *args: object, **options: object) -> None:
        del args
        expected_release_sha = options.get("expected_release_sha")
        production = (
            getattr(settings, "CONTROL_PLANE_OPERATIONS_ENABLED", False) is True
            or expected_release_sha is not None
        )
        try:
            preflight_operation(expected_release_sha)
            operation = _operation(options.get("operation"))
            operation_id = require_uuid(options.get("operation_id"))
            evidence_hash = require_sha256(options.get("acceptance_evidence_sha256"))
            expected_version = _expected_version(options.get("expected_version"))
            expected_current = _revision(options.get("expected_current_revision"))
            target_revision = _target(operation, options.get("target_revision"))
            activation, created, actor_id = self._transition(
                operation_id=operation_id,
                operation=operation,
                target_revision_id=target_revision,
                expected_current_revision_id=expected_current,
                expected_version=expected_version,
                reason_code=_REASON_CODES[production][operation],
                evidence_hash=evidence_hash,
                expected_release_sha=expected_release_sha,
            )
        except ControlPlaneError as error:
            raise CommandError(f"code={error.code.value}") from None
        except LocalPhase0Error as error:
            raise CommandError(f"code={error.code.value}") from None
        except _CommandFailure as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            code = (
                ControlPlaneCode.TRANSITION_FAILED.value
                if production
                else _Code.TRANSITION_FAILED.value
            )
            raise CommandError(f"code={code}") from None

        receipt = [
            f"status={_STATUS_CODES[operation]}",
            f"operation_id={activation.id}",
            f"previous_revision_id={activation.previous_revision_id or _NONE}",
            f"target_revision_id={activation.target_revision_id or _NONE}",
        ]
        if not production:
            receipt.append(f"actor_id={actor_id}")
        receipt.extend(
            (f"resulting_version={activation.sequence}", f"created={'yes' if created else 'no'}")
        )
        self.stdout.write(" ".join(receipt))

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
        expected_release_sha: object = None,
    ) -> tuple[HistoricalRetailPublicationActivation, bool, int]:
        authority = resolve_operation_actor(
            role="publisher",
            expected_release_sha=expected_release_sha,
            lock=True,
        )
        activation, created = transition_historical_publication(
            operation_id=operation_id,
            actor=authority.actor,
            operation=operation,
            target_revision_id=target_revision_id,
            expected_current_revision_id=expected_current_revision_id,
            expected_version=expected_version,
            reason_code=reason_code,
            acceptance_evidence_sha256=evidence_hash,
        )
        return activation, created, authority.actor_id
