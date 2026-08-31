from __future__ import annotations

import io
import uuid

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.management.commands.approve_historical_collection import (
    Command as ApproveCommand,
)
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle

_HASH = "8" * 64


def _run(command: str, **options: object) -> str:
    output = io.StringIO()
    call_command(command, stdout=output, **options)
    return output.getvalue().strip()


def test_historical_review_command_is_default_off_and_has_no_actor_override() -> None:
    parser = ApproveCommand().create_parser("manage.py", "approve_historical_collection")
    destinations = {action.dest for action in parser._actions}
    assert "expected_release_sha" in destinations
    assert not {"actor", "actor_id", "username"} & destinations

    with (
        override_settings(
            DEBUG=False,
            ADMIN_ENABLED=False,
            QA_STATE_PREVIEWS_ENABLED=False,
            CONTROL_PLANE_OPERATIONS_ENABLED=False,
        ),
        pytest.raises(CommandError, match="LOCAL_PHASE0_ENVIRONMENT_DENIED"),
    ):
        _run(
            "approve_historical_collection",
            collection_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            reconciliation_report_sha256=_HASH,
            acceptance_evidence_sha256=_HASH,
        )

@pytest.mark.django_db
@override_settings(
    DEBUG=True,
    ADMIN_ENABLED=False,
    QA_STATE_PREVIEWS_ENABLED=False,
    CONTROL_PLANE_OPERATIONS_ENABLED=False,
)
def test_local_review_command_records_only_one_explicit_collection_decision() -> None:
    _run("bootstrap_local_phase0_operator")
    bundle = create_reviewed_historical_bundle()
    collection = bundle.monthly_review.collection
    replacement_id = uuid.uuid4()

    approved = _run(
        "approve_historical_collection",
        collection_id=collection.id,
        decision_id=replacement_id,
        reconciliation_report_sha256=_HASH,
        acceptance_evidence_sha256="9" * 64,
        supersedes_decision=bundle.monthly_review.id,
    )
    assert f"decision_id={replacement_id}" in approved
    assert "status=APPROVED" in approved and "created=yes" in approved
    assert not HistoricalRetailPublicationRevision.objects.exists()
