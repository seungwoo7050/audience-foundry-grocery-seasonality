"""Auditable partition collections for historical KAMIS source generations."""

from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from grocery.historical_identity_models import YEAR_MONTH_PATTERN
from grocery.models import (
    SHA256_PATTERN,
    ParseRun,
    SourceConfiguration,
    sha256_validator,
)


class HistoricalSourceCollection(models.Model):
    class Kind(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly regional retail"
        REGIONAL_DAILY = "REGIONAL_DAILY", "Regional daily retail"
        MARKET_DAILY = "MARKET_DAILY", "Market daily retail"

    class State(models.TextChoices):
        STARTED = "STARTED", "Started"
        VALIDATED = "VALIDATED", "Validated"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    source_configuration = models.ForeignKey(
        SourceConfiguration,
        on_delete=models.PROTECT,
        related_name="historical_collections",
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.STARTED)
    code_manifest_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    partition_manifest_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    expected_part_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    month_min = models.CharField(
        max_length=6,
        blank=True,
        validators=[RegexValidator(YEAR_MONTH_PATTERN)],
    )
    month_max = models.CharField(
        max_length=6,
        blank=True,
        validators=[RegexValidator(YEAR_MONTH_PATTERN)],
    )
    date_min = models.DateField(null=True, blank=True)
    date_max = models.DateField(null=True, blank=True)
    accepted_row_count = models.PositiveIntegerField(default=0)
    out_of_scope_row_count = models.PositiveIntegerField(default=0)
    quarantined_row_count = models.PositiveIntegerField(default=0)
    result_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[sha256_validator],
    )
    failure_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$")],
    )
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=("MONTHLY", "REGIONAL_DAILY", "MARKET_DAILY")),
                name="grocery_history_collection_kind_valid",
            ),
            models.CheckConstraint(
                condition=Q(state__in=("STARTED", "VALIDATED", "FAILED")),
                name="grocery_history_collection_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(code_manifest_sha256__regex=SHA256_PATTERN)
                & Q(partition_manifest_sha256__regex=SHA256_PATTERN),
                name="grocery_history_collection_hashes_valid",
            ),
            models.CheckConstraint(
                condition=Q(expected_part_count__gt=0),
                name="grocery_history_collection_parts_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="MONTHLY",
                        month_min__regex=YEAR_MONTH_PATTERN,
                        month_max__regex=YEAR_MONTH_PATTERN,
                        date_min__isnull=True,
                        date_max__isnull=True,
                    )
                    | (
                        Q(kind__in=("REGIONAL_DAILY", "MARKET_DAILY"), month_min="", month_max="")
                        & Q(date_min__isnull=False, date_max__isnull=False)
                    )
                ),
                name="grocery_history_collection_window_valid",
            ),
            models.CheckConstraint(
                condition=(Q(kind="MONTHLY", month_max__gte=F("month_min")) | ~Q(kind="MONTHLY"))
                & (Q(date_max__gte=F("date_min")) | Q(date_min__isnull=True)),
                name="grocery_history_collection_window_order",
            ),
            models.CheckConstraint(
                condition=Q(result_sha256="") | Q(result_sha256__regex=SHA256_PATTERN),
                name="grocery_history_collection_result_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state="STARTED",
                        completed_at__isnull=True,
                        result_sha256="",
                        failure_code="",
                    )
                    | (
                        Q(
                            state="VALIDATED",
                            completed_at__isnull=False,
                            failure_code="",
                            quarantined_row_count=0,
                        )
                        & ~Q(result_sha256="")
                    )
                    | (
                        Q(state="FAILED", completed_at__isnull=False, result_sha256="")
                        & ~Q(failure_code="")
                    )
                ),
                name="grocery_history_collection_outcome_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}:{self.state}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            persisted = type(self).objects.filter(pk=self.pk).first()
            if persisted is not None and persisted.state != self.State.STARTED:
                raise ValidationError("Completed historical collections are immutable.")
            immutable = (
                "kind",
                "source_configuration_id",
                "code_manifest_sha256",
                "partition_manifest_sha256",
                "expected_part_count",
                "month_min",
                "month_max",
                "date_min",
                "date_max",
                "started_at",
            )
            if persisted is not None and any(
                getattr(self, field) != getattr(persisted, field) for field in immutable
            ):
                raise ValidationError("Historical collection identity is immutable.")
        self.full_clean()
        super().save(*args, **kwargs)


class HistoricalSourceCollectionPart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        HistoricalSourceCollection,
        on_delete=models.PROTECT,
        related_name="parts",
    )
    ordinal = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    partition_scope_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    parse_run = models.OneToOneField(
        ParseRun,
        on_delete=models.PROTECT,
        related_name="historical_collection_part",
    )
    fact_count = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "ordinal"),
                name="grocery_history_part_ordinal_uniq",
            ),
            models.UniqueConstraint(
                fields=("collection", "partition_scope_sha256"),
                name="grocery_history_part_scope_uniq",
            ),
            models.CheckConstraint(
                condition=Q(ordinal__gt=0),
                name="grocery_history_part_ordinal_positive",
            ),
            models.CheckConstraint(
                condition=Q(partition_scope_sha256__regex=SHA256_PATTERN),
                name="grocery_history_part_scope_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.collection_id}:{self.ordinal}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Historical collection parts are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Historical collection parts are immutable.")

    def clean(self) -> None:
        super().clean()
        if self.collection_id and self.collection.state != HistoricalSourceCollection.State.STARTED:
            raise ValidationError("Parts can only be attached to a started collection.")
        if self.parse_run_id and self.parse_run.status != ParseRun.Status.VALIDATED:
            raise ValidationError("Collection parts require a validated parse run.")
