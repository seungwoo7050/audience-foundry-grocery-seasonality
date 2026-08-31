"""Independent current pointer and append-only audit events for historical retail."""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.models import SHA256_PATTERN, sha256_validator


class HistoricalRetailPublicationChannel(models.Model):
    CHANNEL = "HISTORICAL_RETAIL"

    channel = models.CharField(max_length=32, primary_key=True, default=CHANNEL, editable=False)
    current_revision = models.ForeignKey(
        HistoricalRetailPublicationRevision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_for_channels",
    )
    version = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        permissions = [
            ("publish_historical_publication", "Can publish historical retail revisions")
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(channel="HISTORICAL_RETAIL"),
                name="grocery_history_channel_fixed",
            ),
            models.CheckConstraint(
                condition=Q(version=0, current_revision__isnull=True) | Q(version__gt=0),
                name="grocery_history_channel_initial_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.version}:{self.current_revision_id or 'WITHDRAWN'}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        raise ValidationError("Historical publication channels use the transition service.")

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Historical publication channels cannot be deleted.")


class HistoricalRetailPublicationActivation(models.Model):
    class Operation(models.TextChoices):
        ACTIVATE = "ACTIVATE", "Activate"
        ROLLBACK = "ROLLBACK", "Rollback"
        WITHDRAW = "WITHDRAW", "Withdraw"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        HistoricalRetailPublicationChannel,
        on_delete=models.PROTECT,
        related_name="activations",
    )
    operation = models.CharField(max_length=16, choices=Operation.choices)
    sequence = models.PositiveBigIntegerField()
    previous_revision = models.ForeignKey(
        HistoricalRetailPublicationRevision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historical_transition_sources",
    )
    target_revision = models.ForeignKey(
        HistoricalRetailPublicationRevision,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historical_transition_targets",
    )
    publisher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="grocery_historical_publication_activations",
    )
    reason_code = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$")],
    )
    acceptance_evidence_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("channel", "sequence"),
                name="grocery_history_activation_sequence_uniq",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gt=0),
                name="grocery_history_activation_sequence_positive",
            ),
            models.CheckConstraint(
                condition=Q(operation__in=("ACTIVATE", "ROLLBACK", "WITHDRAW")),
                name="grocery_history_activation_operation_valid",
            ),
            models.CheckConstraint(
                condition=Q(reason_code__regex=r"^[A-Z][A-Z0-9_]*$")
                & Q(acceptance_evidence_sha256__regex=SHA256_PATTERN),
                name="grocery_history_activation_evidence_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(operation="WITHDRAW", target_revision__isnull=True)
                    & Q(previous_revision__isnull=False)
                    | Q(operation__in=("ACTIVATE", "ROLLBACK"), target_revision__isnull=False)
                    & ~Q(previous_revision=F("target_revision"))
                ),
                name="grocery_history_activation_shape_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.sequence}:{self.operation}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding or not getattr(self, "_transition_write", False):
            raise ValidationError("Historical publication events use the transition service.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Historical publication events are append-only.")
