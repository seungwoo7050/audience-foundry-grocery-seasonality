"""Append-only human review decisions for complete historical collections."""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.models import SHA256_PATTERN, SourceConfiguration, sha256_validator


class HistoricalCollectionReviewDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        HistoricalSourceCollection,
        on_delete=models.PROTECT,
        related_name="review_decisions",
    )
    decision = models.CharField(max_length=8, choices=Decision.choices)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="grocery_historical_review_decisions",
    )
    decided_at = models.DateTimeField(default=timezone.now)
    reconciliation_report_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    acceptance_evidence_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    reason_code = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$")],
    )
    approved_result_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[sha256_validator],
    )
    approved_partition_manifest_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[sha256_validator],
    )
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="superseding_decision",
        null=True,
        blank=True,
    )

    class Meta:
        permissions = [("review_historical_collection", "Can review historical collections")]
        constraints = [
            models.CheckConstraint(
                condition=Q(decision__in=("APPROVE", "REJECT")),
                name="grocery_history_review_decision_valid",
            ),
            models.CheckConstraint(
                condition=Q(reconciliation_report_sha256__regex=SHA256_PATTERN)
                & Q(acceptance_evidence_sha256__regex=SHA256_PATTERN),
                name="grocery_history_review_evidence_valid",
            ),
            models.CheckConstraint(
                condition=Q(reason_code__regex=r"^[A-Z][A-Z0-9_]*$"),
                name="grocery_history_review_reason_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        decision="APPROVE",
                        approved_result_sha256__regex=SHA256_PATTERN,
                        approved_partition_manifest_sha256__regex=SHA256_PATTERN,
                    )
                    | Q(
                        decision="REJECT",
                        approved_result_sha256="",
                        approved_partition_manifest_sha256="",
                    )
                ),
                name="grocery_history_review_approval_valid",
            ),
            models.CheckConstraint(
                condition=Q(supersedes__isnull=True) | ~Q(id=F("supersedes_id")),
                name="grocery_history_review_not_self",
            ),
            models.UniqueConstraint(
                fields=("collection",),
                condition=Q(supersedes__isnull=True),
                name="grocery_history_review_root_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.collection_id}:{self.decision}:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Historical review decisions are append-only.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Historical review decisions are append-only.")

    def clean(self) -> None:
        super().clean()
        if not self.collection_id:
            return
        collection = self.collection
        expected_mode = {
            HistoricalSourceCollection.Kind.MONTHLY: (
                SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY
            ),
            HistoricalSourceCollection.Kind.REGIONAL_DAILY: (
                SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL
            ),
            HistoricalSourceCollection.Kind.MARKET_DAILY: (
                SourceConfiguration.PublicationMode.HISTORICAL_MARKET
            ),
        }[collection.kind]
        if (
            collection.source_configuration.state != SourceConfiguration.State.ACTIVE
            or collection.source_configuration.publication_mode != expected_mode
        ):
            raise ValidationError("Historical review requires the matching active source.")
        if collection.completed_at is None or collection.completed_at > self.decided_at:
            raise ValidationError("Historical review requires an already completed collection.")
        if self.supersedes_id:
            previous = type(self).objects.filter(pk=self.supersedes_id).first()
            if previous is None or previous.collection_id != collection.id:
                raise ValidationError("A review can only supersede this collection's current tail.")
            if type(self).objects.filter(supersedes_id=previous.id).exists():
                raise ValidationError("Only the current historical review tail can be superseded.")
        if self.decision == self.Decision.APPROVE:
            if collection.state != HistoricalSourceCollection.State.VALIDATED:
                raise ValidationError("Approval requires a validated historical collection.")
            if (
                self.approved_result_sha256 != collection.result_sha256
                or self.approved_partition_manifest_sha256
                != collection.partition_manifest_sha256
            ):
                raise ValidationError("Approved hashes must match the reviewed collection.")
