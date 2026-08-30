"""Inspect the recent-retail publication from one read-only database snapshot."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from grocery.models import (
    ParseRun,
    PublicationActivation,
    PublicationChannel,
    PublicationRevision,
    ReviewDecision,
    SourceConfiguration,
)
from grocery.public_read import RECENT_RETAIL_CHANNEL
from grocery.publication_facts import FACT_SET_HASH_VERSION, build_publication_fact_set

_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_REASON_CODE: Final = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_COPY_REVISION: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_READ_ONLY_SNAPSHOT: Final = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


class _FailureCode(StrEnum):
    FAILED = "RECENT_PUBLICATION_INSPECTION_FAILED"


class _ContractError(RuntimeError):
    """An intentionally detail-free publication contract failure."""


@dataclass(frozen=True, slots=True)
class _AvailableState:
    version: int
    current_revision_id: uuid.UUID
    typed_fact_set_sha256: str
    entry_count: int
    last_activation_id: uuid.UUID
    last_activation_operation: str
    last_activation_sequence: int


@dataclass(frozen=True, slots=True)
class _UnavailableState:
    version: int
    last_activation_id: uuid.UUID | None
    last_activation_operation: str | None
    last_activation_sequence: int | None


def _receipt(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
    )


_ERROR_RECEIPT: Final = _receipt(
    {
        "channel": RECENT_RETAIL_CHANNEL,
        "publication_state": "ERROR",
    }
)


def _unavailable_receipt(state: _UnavailableState) -> str:
    return _receipt(
        {
            "channel": RECENT_RETAIL_CHANNEL,
            "publication_state": "UNAVAILABLE",
            "version": state.version,
            "current_revision_id": "NONE",
            "last_activation_id": (
                None if state.last_activation_id is None else str(state.last_activation_id)
            ),
            "last_activation_operation": state.last_activation_operation,
            "last_activation_sequence": state.last_activation_sequence,
        }
    )


def _available_receipt(state: _AvailableState) -> str:
    return _receipt(
        {
            "channel": RECENT_RETAIL_CHANNEL,
            "publication_state": "AVAILABLE",
            "version": state.version,
            "current_revision_id": str(state.current_revision_id),
            "typed_fact_set_sha256": state.typed_fact_set_sha256,
            "entry_count": state.entry_count,
            "last_activation_id": str(state.last_activation_id),
            "last_activation_operation": state.last_activation_operation,
            "last_activation_sequence": state.last_activation_sequence,
        }
    )


def _contract(condition: bool) -> None:
    if not condition:
        raise _ContractError


def _assert_channel_history() -> None:
    """Run the migration-owned contiguous history assertion without changing state."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT grocery_publication_assert_channel_state(%s)",
            [RECENT_RETAIL_CHANNEL],
        )


def _latest_activation() -> PublicationActivation | None:
    return (
        PublicationActivation.objects.filter(channel_id=RECENT_RETAIL_CHANNEL)
        .order_by("-sequence")
        .first()
    )


def _validate_revision(revision: PublicationRevision) -> None:
    decision = revision.review_decision
    generation = revision.generation
    source = decision.source_configuration

    _contract(type(revision.id) is uuid.UUID)
    _contract(revision.channel == RECENT_RETAIL_CHANNEL)
    _contract(revision.mode == PublicationRevision.Mode.RECENT_COMPARISON)
    _contract(revision.sealed_at is not None and revision.sealed_at >= revision.created_at)
    _contract(revision.fact_hash_version == FACT_SET_HASH_VERSION)
    _contract(_SHA256.fullmatch(revision.typed_fact_set_sha256) is not None)
    _contract(type(revision.entry_count) is int and revision.entry_count > 0)
    _contract(revision.source_effective_date_min <= revision.source_effective_date_max)
    _contract(_COPY_REVISION.fullmatch(revision.public_copy_revision) is not None)

    _contract(decision.decision == ReviewDecision.Decision.APPROVE)
    _contract(not ReviewDecision.objects.filter(supersedes_id=decision.id).exists())
    _contract(decision.parse_run_id == generation.id)
    _contract(decision.source_configuration_id == source.id)
    _contract(decision.approved_mode == SourceConfiguration.PublicationMode.RECENT_COMPARISON)
    effective_state_changed_at, effective_rights_confirmed_at = source.effective_gate_timestamps()
    _contract(source.state == SourceConfiguration.State.ACTIVE)
    if effective_rights_confirmed_at is None:
        raise _ContractError
    _contract(effective_state_changed_at <= decision.decided_at)
    _contract(effective_rights_confirmed_at <= decision.decided_at)
    _contract(source.publication_mode == SourceConfiguration.PublicationMode.RECENT_COMPARISON)
    _contract(decision.approved_mode == source.publication_mode)
    _contract(decision.approved_coverage_identity == source.coverage_identity)
    _contract(decision.approved_coverage_evidence_revision == source.coverage_evidence_revision)
    _contract(generation.status == ParseRun.Status.VALIDATED)
    _contract(generation.completed_at is not None and bool(generation.result_hash))
    _contract(revision.parser_revision == generation.parser_revision)

    entries = list(
        revision.entries.select_related("snapshot__series")
        .prefetch_related("snapshot__reference_prices__change_fact")
        .order_by("ordinal")
    )
    _contract(len(entries) == revision.entry_count == generation.accepted_row_count)
    _contract([entry.ordinal for entry in entries] == list(range(1, len(entries) + 1)))
    _contract(
        all(
            entry.revision_id == revision.id
            and entry.snapshot.parse_run_id == generation.id
            and entry.snapshot.series.coverage_identity == decision.approved_coverage_identity
            and _SHA256.fullmatch(entry.fact_sha256) is not None
            for entry in entries
        )
    )

    fact_set = build_publication_fact_set([entry.snapshot for entry in entries])
    _contract(fact_set.typed_fact_set_sha256 == revision.typed_fact_set_sha256)
    _contract(fact_set.source_effective_date_min == revision.source_effective_date_min)
    _contract(fact_set.source_effective_date_max == revision.source_effective_date_max)
    _contract(len(fact_set.entries) == len(entries))
    _contract(
        all(
            (stored.ordinal, stored.snapshot_id, stored.fact_sha256)
            == (canonical.ordinal, canonical.snapshot_id, canonical.fact_sha256)
            for stored, canonical in zip(entries, fact_set.entries, strict=True)
        )
    )


def _validate_available(
    channel: PublicationChannel,
    latest: PublicationActivation | None,
) -> _AvailableState:
    revision = channel.current_revision
    if revision is None:
        raise _ContractError
    _contract(channel.channel == RECENT_RETAIL_CHANNEL)
    _contract(type(channel.version) is int and channel.version > 0)
    _contract(channel.current_revision_id == revision.id)
    _validate_revision(revision)

    if latest is None:
        raise _ContractError
    _contract(type(latest.id) is uuid.UUID)
    _contract(latest.channel_id == RECENT_RETAIL_CHANNEL)
    _contract(latest.operation in {"ACTIVATE", "ROLLBACK"})
    _contract(type(latest.sequence) is int and latest.sequence == channel.version)
    _contract(latest.target_revision_id == revision.id)
    _contract(latest.previous_revision_id != latest.target_revision_id)
    _contract(latest.publisher_id is not None)
    _contract(_REASON_CODE.fullmatch(latest.reason_code) is not None)
    _contract(_SHA256.fullmatch(latest.acceptance_evidence_sha256) is not None)

    return _AvailableState(
        version=channel.version,
        current_revision_id=revision.id,
        typed_fact_set_sha256=revision.typed_fact_set_sha256,
        entry_count=revision.entry_count,
        last_activation_id=latest.id,
        last_activation_operation=latest.operation,
        last_activation_sequence=latest.sequence,
    )


def _validate_unavailable(
    channel: PublicationChannel,
    latest: PublicationActivation | None,
) -> _UnavailableState:
    _contract(channel.channel == RECENT_RETAIL_CHANNEL)
    _contract(channel.current_revision_id is None)
    _contract(type(channel.version) is int and channel.version >= 0)
    if channel.version == 0:
        _contract(latest is None)
        return _UnavailableState(
            version=0,
            last_activation_id=None,
            last_activation_operation=None,
            last_activation_sequence=None,
        )

    if latest is None:
        raise _ContractError
    _contract(type(latest.id) is uuid.UUID)
    _contract(latest.channel_id == RECENT_RETAIL_CHANNEL)
    _contract(latest.operation == PublicationActivation.Operation.WITHDRAW)
    _contract(latest.sequence == channel.version)
    _contract(latest.previous_revision_id is not None)
    _contract(latest.target_revision_id is None)
    _contract(latest.publisher_id is not None)
    _contract(_REASON_CODE.fullmatch(latest.reason_code) is not None)
    _contract(_SHA256.fullmatch(latest.acceptance_evidence_sha256) is not None)
    return _UnavailableState(
        version=channel.version,
        last_activation_id=latest.id,
        last_activation_operation=latest.operation,
        last_activation_sequence=latest.sequence,
    )


def _inspect_in_read_only_snapshot() -> _AvailableState | _UnavailableState:
    with transaction.atomic(durable=True):
        with connection.cursor() as cursor:
            cursor.execute(_READ_ONLY_SNAPSHOT)

        channel = (
            PublicationChannel.objects.select_related(
                "current_revision__generation",
                "current_revision__review_decision__source_configuration",
                "current_revision__review_decision__source_configuration__gate_timestamp_correction",
            )
            .filter(pk=RECENT_RETAIL_CHANNEL)
            .first()
        )
        if channel is None:
            _contract(_latest_activation() is None)
            return _UnavailableState(
                version=0,
                last_activation_id=None,
                last_activation_operation=None,
                last_activation_sequence=None,
            )

        _assert_channel_history()
        latest = _latest_activation()
        if channel.current_revision_id is None:
            return _validate_unavailable(channel, latest)
        return _validate_available(channel, latest)


class Command(BaseCommand):
    help = "Inspect the recent-retail publication using a read-only repeatable-read snapshot."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        try:
            state = _inspect_in_read_only_snapshot()
        except Exception:
            self.stdout.write(_ERROR_RECEIPT)
            raise CommandError(f"code={_FailureCode.FAILED.value}") from None

        if isinstance(state, _UnavailableState):
            self.stdout.write(_unavailable_receipt(state))
        else:
            self.stdout.write(_available_receipt(state))
