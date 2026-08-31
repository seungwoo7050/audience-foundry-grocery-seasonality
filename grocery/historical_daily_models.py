"""Immutable daily regional ranges and market observations from KAMIS."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_fact_base import HistoricalPriceFact
from grocery.historical_identity_models import RetailMarketKey
from grocery.models import SHA256_PATTERN


class DailyRegionalRetailPrice(HistoricalPriceFact):
    survey_date = models.DateField()
    provider_mean = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    provider_low = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    provider_high = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "series", "region", "survey_date"),
                name="grocery_regional_series_region_date_uniq",
            ),
            models.CheckConstraint(
                condition=Q(provider_low__gt=0)
                & Q(provider_mean__gte=F("provider_low"))
                & Q(provider_high__gte=F("provider_mean")),
                name="grocery_regional_price_range_valid",
            ),
            models.CheckConstraint(condition=Q(currency="KRW"), name="grocery_regional_currency"),
            models.CheckConstraint(
                condition=Q(source_row_sha256__regex=SHA256_PATTERN),
                name="grocery_regional_row_hash_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.series_id}:{self.region_id}:{self.survey_date}"

    def clean(self) -> None:
        super().clean()
        if (
            self.collection_id
            and self.collection.kind != HistoricalSourceCollection.Kind.REGIONAL_DAILY
        ):
            raise ValidationError("Regional facts require a regional-daily collection.")
        if self.collection_id and not (
            self.collection.date_min <= self.survey_date <= self.collection.date_max
        ):
            raise ValidationError("Regional fact is outside its collection window.")


class DailyMarketRetailPrice(HistoricalPriceFact):
    market = models.ForeignKey(RetailMarketKey, on_delete=models.PROTECT)
    survey_date = models.DateField()
    provider_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    source_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "series", "region", "market", "survey_date"),
                name="grocery_market_series_region_market_date_uniq",
            ),
            models.CheckConstraint(
                condition=Q(provider_price__gt=0),
                name="grocery_market_price_positive",
            ),
            models.CheckConstraint(condition=Q(currency="KRW"), name="grocery_market_currency"),
            models.CheckConstraint(
                condition=Q(source_row_sha256__regex=SHA256_PATTERN),
                name="grocery_market_row_hash_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.series_id}:{self.region_id}:{self.market_id}:{self.survey_date}"

    def clean(self) -> None:
        super().clean()
        if (
            self.collection_id
            and self.collection.kind != HistoricalSourceCollection.Kind.MARKET_DAILY
        ):
            raise ValidationError("Market facts require a market-daily collection.")
        if self.market_id and self.region_id and self.market.region_id != self.region_id:
            raise ValidationError("Market and fact region do not match.")
        if self.collection_id and not (
            self.collection.date_min <= self.survey_date <= self.collection.date_max
        ):
            raise ValidationError("Market fact is outside its collection window.")
