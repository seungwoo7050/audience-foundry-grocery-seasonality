"""Reviewed exact identities shared by recent and historical retail sources."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from grocery.models import (
    DIGIT_CODE_PATTERN,
    SHA256_PATTERN,
    PriceSeriesKey,
    sha256_validator,
)

YEAR_MONTH_PATTERN = r"^[0-9]{4}(0[1-9]|1[0-2])$"


def price_series_identity_sha256(series: PriceSeriesKey) -> str:
    """Hash exact source identity without the recent aggregate coverage."""

    value = {
        "product_class_code": series.product_class_code,
        "category_code": series.category_code,
        "item_code": series.item_code,
        "variety_code": series.variety_code,
        "grade_code": series.grade_code,
        "raw_unit": series.raw_unit,
        "raw_unit_size": series.raw_unit_size,
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class HistoricalRetailSeriesKey(models.Model):
    """Reviewed bridge from a recent series to the three historical APIs."""

    recent_series = models.OneToOneField(
        PriceSeriesKey,
        on_delete=models.PROTECT,
        primary_key=True,
        related_name="historical_identity",
    )
    series_identity_sha256 = models.CharField(
        max_length=64,
        unique=True,
        validators=[sha256_validator],
    )
    cross_source_evidence_revision = models.CharField(max_length=128)
    code_manifest_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(series_identity_sha256__regex=SHA256_PATTERN),
                name="grocery_history_series_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(code_manifest_sha256__regex=SHA256_PATTERN),
                name="grocery_history_series_manifest_valid",
            ),
            models.CheckConstraint(
                condition=~Q(cross_source_evidence_revision=""),
                name="grocery_history_series_evidence_nonempty",
            ),
        ]

    def __str__(self) -> str:
        return self.series_identity_sha256

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Historical retail series keys are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Historical retail series keys are immutable.")

    def clean(self) -> None:
        super().clean()
        if self.recent_series_id and self.series_identity_sha256 != price_series_identity_sha256(
            self.recent_series
        ):
            raise ValidationError("Historical series hash does not match its recent exact series.")


class RetailRegionKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    region_code = models.CharField(
        max_length=32,
        unique=True,
        validators=[RegexValidator(DIGIT_CODE_PATTERN)],
    )
    region_name = models.CharField(max_length=200)
    identity_evidence_revision = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(region_code__regex=DIGIT_CODE_PATTERN),
                name="grocery_region_code_valid",
            ),
            models.CheckConstraint(
                condition=~Q(region_name="") & ~Q(identity_evidence_revision=""),
                name="grocery_region_identity_complete",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.region_code}:{self.region_name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Retail region keys are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Retail region keys are immutable.")


class RetailMarketKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    region = models.ForeignKey(
        RetailRegionKey,
        on_delete=models.PROTECT,
        related_name="markets",
    )
    market_code = models.CharField(
        max_length=32,
        validators=[RegexValidator(DIGIT_CODE_PATTERN)],
    )
    market_name = models.CharField(max_length=200)
    identity_evidence_revision = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("region", "market_code"),
                name="grocery_market_region_code_uniq",
            ),
            models.CheckConstraint(
                condition=Q(market_code__regex=DIGIT_CODE_PATTERN),
                name="grocery_market_code_valid",
            ),
            models.CheckConstraint(
                condition=~Q(market_name="") & ~Q(identity_evidence_revision=""),
                name="grocery_market_identity_complete",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.region_id}:{self.market_code}:{self.market_name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Retail market keys are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Retail market keys are immutable.")
