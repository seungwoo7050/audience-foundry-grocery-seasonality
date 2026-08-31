"""Immutable publication bundle over three independently reviewed source collections."""

from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q

from grocery.historical_identity_models import YEAR_MONTH_PATTERN
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.models import SHA256_PATTERN, sha256_validator


class HistoricalRetailPublicationRevision(models.Model):
    FACT_HASH_VERSION = "historical-retail-bundle-v1"
    COPY_REVISION = "ko-v4"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    monthly_review = models.ForeignKey(
        HistoricalCollectionReviewDecision,
        on_delete=models.PROTECT,
        related_name="monthly_publication_revisions",
    )
    regional_review = models.ForeignKey(
        HistoricalCollectionReviewDecision,
        on_delete=models.PROTECT,
        related_name="regional_publication_revisions",
    )
    market_review = models.ForeignKey(
        HistoricalCollectionReviewDecision,
        on_delete=models.PROTECT,
        related_name="market_publication_revisions",
    )
    code_manifest_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    compatibility_report_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    fact_hash_version = models.CharField(max_length=64, default=FACT_HASH_VERSION, editable=False)
    typed_fact_set_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    series_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    monthly_fact_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    regional_fact_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    market_fact_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    month_min = models.CharField(max_length=6, validators=[RegexValidator(YEAR_MONTH_PATTERN)])
    month_max = models.CharField(max_length=6, validators=[RegexValidator(YEAR_MONTH_PATTERN)])
    date_min = models.DateField()
    date_max = models.DateField()
    public_copy_revision = models.CharField(max_length=16, default=COPY_REVISION)
    created_at = models.DateTimeField(auto_now_add=True)
    sealed_at = models.DateTimeField(null=True, blank=True)

    _seal_write = False

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("typed_fact_set_sha256", "public_copy_revision"),
                name="grocery_history_publication_set_copy_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(monthly_review=F("regional_review"))
                & ~Q(monthly_review=F("market_review"))
                & ~Q(regional_review=F("market_review")),
                name="grocery_history_publication_reviews_distinct",
            ),
            models.CheckConstraint(
                condition=Q(code_manifest_sha256__regex=SHA256_PATTERN)
                & Q(compatibility_report_sha256__regex=SHA256_PATTERN)
                & Q(typed_fact_set_sha256__regex=SHA256_PATTERN),
                name="grocery_history_publication_hashes_valid",
            ),
            models.CheckConstraint(
                condition=Q(fact_hash_version="historical-retail-bundle-v1"),
                name="grocery_history_publication_hash_version",
            ),
            models.CheckConstraint(
                condition=Q(series_count__gt=0)
                & Q(monthly_fact_count__gt=0)
                & Q(regional_fact_count__gt=0)
                & Q(market_fact_count__gt=0),
                name="grocery_history_publication_counts_positive",
            ),
            models.CheckConstraint(
                condition=Q(month_min__regex=YEAR_MONTH_PATTERN)
                & Q(month_max__regex=YEAR_MONTH_PATTERN)
                & Q(month_max__gte=F("month_min")),
                name="grocery_history_publication_months_valid",
            ),
            models.CheckConstraint(
                condition=Q(date_max__gte=F("date_min")),
                name="grocery_history_publication_dates_valid",
            ),
            models.CheckConstraint(
                condition=Q(public_copy_revision="ko-v4"),
                name="grocery_history_publication_copy_valid",
            ),
            models.CheckConstraint(
                condition=Q(sealed_at__isnull=True) | Q(sealed_at__gte=F("created_at")),
                name="grocery_history_publication_seal_time",
            ),
        ]

    def __str__(self) -> str:
        return f"HISTORICAL_RETAIL:{self.typed_fact_set_sha256}:{self.public_copy_revision}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding or not self._seal_write or self.sealed_at is not None:
            raise ValidationError("Historical publication revisions use the seal service.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Historical publication revisions are immutable.")

    def clean(self) -> None:
        super().clean()
        reviews = (
            (self.monthly_review_id, "monthly_review", "MONTHLY"),
            (self.regional_review_id, "regional_review", "REGIONAL_DAILY"),
            (self.market_review_id, "market_review", "MARKET_DAILY"),
        )
        review_ids = [review_id for review_id, _name, _kind in reviews if review_id]
        if len(review_ids) != len(set(review_ids)):
            raise ValidationError("Historical publication reviews must be distinct.")
        for review_id, attribute, expected_kind in reviews:
            if not review_id:
                continue
            review = getattr(self, attribute)
            collection = review.collection
            if (
                review.decision != HistoricalCollectionReviewDecision.Decision.APPROVE
                or collection.kind != expected_kind
                or collection.state != "VALIDATED"
                or review.approved_result_sha256 != collection.result_sha256
                or review.approved_partition_manifest_sha256
                != collection.partition_manifest_sha256
                or collection.code_manifest_sha256 != self.code_manifest_sha256
                or HistoricalCollectionReviewDecision.objects.filter(
                    supersedes_id=review.id
                ).exists()
            ):
                raise ValidationError(
                    "Historical publication requires current exact approved reviews."
                )
