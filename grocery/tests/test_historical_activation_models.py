import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction

from grocery.historical_activation_models import HistoricalRetailPublicationChannel


def test_historical_channel_is_fixed_and_rejects_direct_model_transition(db: None) -> None:
    channel = HistoricalRetailPublicationChannel.objects.create()
    assert (channel.channel, channel.version, channel.current_revision_id) == (
        "HISTORICAL_RETAIL",
        0,
        None,
    )

    channel.version = 1
    with pytest.raises(ValidationError, match="events"):
        channel.save()


def test_database_rejects_a_channel_update_without_matching_event(db: None) -> None:
    HistoricalRetailPublicationChannel.objects.create()

    with pytest.raises(DatabaseError, match="matching event"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE grocery_historicalretailpublicationchannel "
                "SET version = 1 WHERE channel = 'HISTORICAL_RETAIL'"
            )
