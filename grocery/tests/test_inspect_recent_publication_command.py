"""Tests for the read-only recent-retail publication inspection receipt."""

from __future__ import annotations

import io
import json
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from grocery.management.commands import inspect_recent_publication as inspection
from grocery.models import (
    PublicationActivation,
    PublicationChannel,
    PublicationEntry,
    PublicationRevision,
)
from grocery.publication_facts import build_publication_fact_set
from grocery.tests.test_publication_activation_models import create_publisher, transition
from grocery.tests.test_publication_revision_models import (
    create_approved_generation,
    seal_recent_publication,
)

pytestmark = pytest.mark.django_db(transaction=True)

_COMMAND = "inspect_recent_publication"
_EVIDENCE_MARKER = "8" * 64
_AVAILABLE_KEYS = {
    "channel",
    "publication_state",
    "version",
    "current_revision_id",
    "typed_fact_set_sha256",
    "entry_count",
    "last_activation_id",
    "last_activation_operation",
    "last_activation_sequence",
}


def _invoke() -> tuple[io.StringIO, object]:
    output = io.StringIO()
    result = call_command(_COMMAND, stdout=output)
    return output, result


def _parsed(output: io.StringIO) -> dict[str, object]:
    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    return value


def _activate_rollback_state() -> tuple[PublicationRevision, PublicationActivation]:
    decision, _snapshots, _reviewer = create_approved_generation()
    v1 = seal_recent_publication(decision.id, "ko-v1")
    v2 = seal_recent_publication(decision.id, "ko-v2")
    publisher = create_publisher()
    transition(
        publisher=publisher,
        operation=PublicationActivation.Operation.ACTIVATE,
        target=v1,
        expected_current=None,
        expected_version=0,
    )
    transition(
        publisher=publisher,
        operation=PublicationActivation.Operation.ACTIVATE,
        target=v2,
        expected_current=v1,
        expected_version=1,
    )
    rollback, _created = transition(
        publisher=publisher,
        operation=PublicationActivation.Operation.ROLLBACK,
        target=v1,
        expected_current=v2,
        expected_version=2,
    )
    return v1, rollback


@override_settings(DEBUG=False, ADMIN_ENABLED=False)
def test_available_receipt_is_canonical_bounded_and_read_only_in_production_settings() -> None:
    revision, rollback = _activate_rollback_state()
    model_counts = {
        "channels": PublicationChannel.objects.count(),
        "activations": PublicationActivation.objects.count(),
        "revisions": PublicationRevision.objects.count(),
        "entries": PublicationEntry.objects.count(),
    }

    with (
        CaptureQueriesContext(connection) as queries,
        patch("grocery.source.client.KamisHttpClient.fetch_recent_prices") as source_fetch,
    ):
        output, result = _invoke()

    assert result is None
    source_fetch.assert_not_called()
    assert _parsed(output) == {
        "channel": "RECENT_RETAIL",
        "publication_state": "AVAILABLE",
        "version": 3,
        "current_revision_id": str(revision.id),
        "typed_fact_set_sha256": revision.typed_fact_set_sha256,
        "entry_count": revision.entry_count,
        "last_activation_id": str(rollback.id),
        "last_activation_operation": "ROLLBACK",
        "last_activation_sequence": 3,
    }
    assert set(_parsed(output)) == _AVAILABLE_KEYS
    receipt = output.getvalue()
    assert _EVIDENCE_MARKER not in receipt
    assert rollback.reason_code not in receipt
    assert rollback.publisher.username not in receipt

    sql = [query["sql"].strip().upper() for query in queries.captured_queries]
    assert any(
        statement.startswith("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for statement in sql
    )
    assert not any(
        statement.startswith(("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ", "CREATE "))
        for statement in sql
    )
    assert {
        "channels": PublicationChannel.objects.count(),
        "activations": PublicationActivation.objects.count(),
        "revisions": PublicationRevision.objects.count(),
        "entries": PublicationEntry.objects.count(),
    } == model_counts


def test_missing_channel_is_a_successful_fixed_unavailable_state() -> None:
    with CaptureQueriesContext(connection) as queries:
        output, result = _invoke()

    assert result is None
    assert _parsed(output) == {
        "channel": "RECENT_RETAIL",
        "publication_state": "UNAVAILABLE",
        "version": 0,
        "current_revision_id": "NONE",
        "last_activation_id": None,
        "last_activation_operation": None,
        "last_activation_sequence": None,
    }
    assert any(
        query["sql"]
        .strip()
        .upper()
        .startswith("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for query in queries.captured_queries
    )
    assert not PublicationChannel.objects.exists()
    assert not PublicationActivation.objects.exists()


def test_withdrawn_channel_is_a_successful_fixed_unavailable_state() -> None:
    decision, _snapshots, _reviewer = create_approved_generation()
    revision = seal_recent_publication(decision.id, "ko-v1")
    publisher = create_publisher()
    transition(
        publisher=publisher,
        operation=PublicationActivation.Operation.ACTIVATE,
        target=revision,
        expected_current=None,
        expected_version=0,
    )
    withdrawn, _created = transition(
        publisher=publisher,
        operation=PublicationActivation.Operation.WITHDRAW,
        target=None,
        expected_current=revision,
        expected_version=1,
    )

    output, result = _invoke()

    assert result is None
    assert _parsed(output) == {
        "channel": "RECENT_RETAIL",
        "publication_state": "UNAVAILABLE",
        "version": 2,
        "current_revision_id": "NONE",
        "last_activation_id": str(withdrawn.id),
        "last_activation_operation": "WITHDRAW",
        "last_activation_sequence": 2,
    }
    channel = PublicationChannel.objects.get(pk="RECENT_RETAIL")
    assert (channel.version, channel.current_revision_id) == (2, None)
    assert PublicationActivation.objects.count() == 2


def test_canonical_fact_corruption_fails_closed_without_reflecting_evidence() -> None:
    revision, _rollback = _activate_rollback_state()
    fact_set = build_publication_fact_set(
        [entry.snapshot for entry in revision.entries.select_related("snapshot__series")]
    )
    corrupted = replace(fact_set, typed_fact_set_sha256="0" * 64)
    output = io.StringIO()

    with (
        patch.object(inspection, "build_publication_fact_set", return_value=corrupted),
        pytest.raises(CommandError) as caught,
    ):
        call_command(_COMMAND, stdout=output)

    assert caught.value.returncode != 0
    assert str(caught.value) == "code=RECENT_PUBLICATION_INSPECTION_FAILED"
    assert _parsed(output) == {
        "channel": "RECENT_RETAIL",
        "publication_state": "ERROR",
    }
    assert revision.typed_fact_set_sha256 not in output.getvalue()
    assert "0" * 64 not in output.getvalue()


def test_latest_activation_inconsistency_fails_closed() -> None:
    revision, rollback = _activate_rollback_state()
    corrupted = SimpleNamespace(
        id=uuid.uuid4(),
        channel_id="RECENT_RETAIL",
        operation="WITHDRAW",
        sequence=rollback.sequence,
        previous_revision_id=revision.id,
        target_revision_id=None,
        publisher_id=rollback.publisher_id,
        reason_code="PRIVATE_CORRUPTION_MARKER",
        acceptance_evidence_sha256="9" * 64,
    )
    output = io.StringIO()

    with (
        patch.object(inspection, "_latest_activation", return_value=corrupted),
        pytest.raises(CommandError) as caught,
    ):
        call_command(_COMMAND, stdout=output)

    assert str(caught.value) == "code=RECENT_PUBLICATION_INSPECTION_FAILED"
    assert _parsed(output) == {
        "channel": "RECENT_RETAIL",
        "publication_state": "ERROR",
    }
    combined = output.getvalue() + str(caught.value)
    assert "PRIVATE_CORRUPTION_MARKER" not in combined
    assert "9" * 64 not in combined
    assert str(corrupted.id) not in combined


@pytest.mark.parametrize(
    "failure",
    (
        DatabaseError("SELECT secret FROM raw_rows WHERE serviceKey='private-value'"),
        RuntimeError("actor=42 query=https://provider.invalid/?serviceKey=private-value"),
    ),
)
def test_database_or_contract_error_has_one_fixed_nonzero_redacted_receipt(
    failure: Exception,
) -> None:
    output = io.StringIO()
    with (
        patch.object(inspection, "_inspect_in_read_only_snapshot", side_effect=failure),
        pytest.raises(CommandError) as caught,
    ):
        call_command(_COMMAND, stdout=output)

    assert caught.value.returncode != 0
    assert str(caught.value) == "code=RECENT_PUBLICATION_INSPECTION_FAILED"
    assert _parsed(output) == {
        "channel": "RECENT_RETAIL",
        "publication_state": "ERROR",
    }
    combined = output.getvalue() + str(caught.value) + repr(caught.value)
    assert "private-value" not in combined
    assert "raw_rows" not in combined
    assert "actor=42" not in combined
    assert "provider.invalid" not in combined
