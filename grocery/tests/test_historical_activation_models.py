import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction

from grocery.historical_activation_models import (
    HistoricalRetailPublicationActivation,
    HistoricalRetailPublicationChannel,
)
from grocery.historical_activations import (
    _set_historical_transition_token,
    transition_historical_publication,
)
from grocery.historical_publications import seal_historical_publication
from grocery.tests.historical_bundle_factory import create_reviewed_historical_bundle


def test_historical_channel_is_fixed_and_rejects_direct_model_transition(db: None) -> None:
    with pytest.raises(ValidationError, match="service"):
        HistoricalRetailPublicationChannel.objects.create()
    with pytest.raises(DatabaseError), transaction.atomic():
        HistoricalRetailPublicationChannel.objects.bulk_create(
            [HistoricalRetailPublicationChannel()]
        )


def test_historical_activation_is_authorized_idempotent_cas(transactional_db: None) -> None:
    bundle = create_reviewed_historical_bundle()
    revision = seal_historical_publication(
        monthly_review_id=bundle.monthly_review.id,
        regional_review_id=bundle.regional_review.id,
        market_review_id=bundle.market_review.id,
        compatibility_report_sha256="2" * 64,
    )
    publisher = get_user_model().objects.create_user(username="historical-publisher")
    publisher.user_permissions.add(
        Permission.objects.get(codename="publish_historical_publication")
    )
    values = {
        "operation_id": uuid.uuid4(),
        "actor": publisher,
        "operation": HistoricalRetailPublicationActivation.Operation.ACTIVATE,
        "target_revision_id": revision.id,
        "expected_current_revision_id": None,
        "expected_version": 0,
        "reason_code": "INITIAL_PUBLICATION",
        "acceptance_evidence_sha256": "3" * 64,
    }

    activation, created = transition_historical_publication(**values)
    replay, replay_created = transition_historical_publication(**values)

    channel = HistoricalRetailPublicationChannel.objects.get()
    assert created is True and replay_created is False and replay.id == activation.id
    assert (channel.version, channel.current_revision_id) == (1, revision.id)
    assert HistoricalRetailPublicationActivation.objects.count() == 1

    stale_values = dict(values, operation_id=uuid.uuid4())
    with pytest.raises(ValidationError, match="stale"):
        transition_historical_publication(**stale_values)
    assert HistoricalRetailPublicationActivation.objects.count() == 1

    with pytest.raises(DatabaseError), transaction.atomic():
        _set_historical_transition_token(uuid.uuid4())
        orphan = HistoricalRetailPublicationActivation(
            channel=channel,
            operation=HistoricalRetailPublicationActivation.Operation.WITHDRAW,
            sequence=2,
            previous_revision=revision,
            target_revision=None,
            publisher=publisher,
            reason_code="ORPHAN_PROBE",
            acceptance_evidence_sha256="4" * 64,
        )
        orphan._transition_write = True
        _set_historical_transition_token(orphan.id)
        orphan.save()
    assert HistoricalRetailPublicationActivation.objects.count() == 1

    outsider = get_user_model().objects.create_user(username="historical-outsider")
    with pytest.raises(PermissionDenied):
        transition_historical_publication(
            operation_id=uuid.uuid4(),
            actor=outsider,
            operation=HistoricalRetailPublicationActivation.Operation.WITHDRAW,
            target_revision_id=None,
            expected_current_revision_id=revision.id,
            expected_version=1,
            reason_code="UNAUTHORIZED",
            acceptance_evidence_sha256="5" * 64,
        )

    with pytest.raises(DatabaseError), transaction.atomic():
        HistoricalRetailPublicationChannel.objects.filter(pk=channel.pk).update(
            version=2,
        )
