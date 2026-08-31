"""Shared immutable identity fields for typed historical price facts."""

from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models

from grocery.historical_collection_models import (
    HistoricalSourceCollection,
    HistoricalSourceCollectionPart,
)
from grocery.historical_identity_models import HistoricalRetailSeriesKey, RetailRegionKey
from grocery.models import sha256_validator


class HistoricalPriceFact(models.Model):
    class Currency(models.TextChoices):
        KRW = "KRW", "South Korean won"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(HistoricalSourceCollection, on_delete=models.PROTECT)
    collection_part = models.ForeignKey(HistoricalSourceCollectionPart, on_delete=models.PROTECT)
    series = models.ForeignKey(HistoricalRetailSeriesKey, on_delete=models.PROTECT)
    region = models.ForeignKey(RetailRegionKey, on_delete=models.PROTECT)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.KRW)
    source_row_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    source_contract_revision = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Historical price facts are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Historical price facts are immutable.")

    def clean(self) -> None:
        super().clean()
        if self.collection_part_id and self.collection_part.collection_id != self.collection_id:
            raise ValidationError("Historical fact collection and part do not match.")
        if (
            self.collection_id
            and self.collection.state != HistoricalSourceCollection.State.STARTED
        ):
            raise ValidationError("Historical facts can only be attached to a started collection.")
