"""Immutable provider monthly ranges from KAMIS dataset 15156060."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_fact_base import HistoricalPriceFact
from grocery.historical_identity_models import YEAR_MONTH_PATTERN
from grocery.models import SHA256_PATTERN


class MonthlyRegionalRetailPrice(HistoricalPriceFact):
    year_month = models.CharField(max_length=6, validators=[RegexValidator(YEAR_MONTH_PATTERN)])
    provider_mean = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        validators=[MinValueValidator(Decimal("1"))],
    )
    provider_low = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        validators=[MinValueValidator(Decimal("1"))],
    )
    provider_high = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        validators=[MinValueValidator(Decimal("1"))],
    )
    source_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "series", "region", "year_month"),
                name="grocery_monthly_series_region_month_uniq",
            ),
            models.CheckConstraint(
                condition=Q(year_month__regex=YEAR_MONTH_PATTERN),
                name="grocery_monthly_year_month_valid",
            ),
            models.CheckConstraint(
                condition=Q(provider_low__gt=0)
                & Q(provider_mean__gte=F("provider_low"))
                & Q(provider_high__gte=F("provider_mean")),
                name="grocery_monthly_price_range_valid",
            ),
            models.CheckConstraint(condition=Q(currency="KRW"), name="grocery_monthly_currency"),
            models.CheckConstraint(
                condition=Q(source_row_sha256__regex=SHA256_PATTERN),
                name="grocery_monthly_row_hash_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.series_id}:{self.region_id}:{self.year_month}"

    def clean(self) -> None:
        super().clean()
        if self.collection_id and self.collection.kind != HistoricalSourceCollection.Kind.MONTHLY:
            raise ValidationError("Monthly facts require a monthly collection.")
