"""Typed price-comparison facts independent of persistence and presentation."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum


class ComparisonPeriod(StrEnum):
    """Provider-defined comparison periods, in public display order."""

    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"


class ValueStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ReferenceDateStatus(StrEnum):
    PROVIDED = "PROVIDED"
    SOURCE_REFERENCE_DATE_UNAVAILABLE = "SOURCE_REFERENCE_DATE_UNAVAILABLE"


class Direction(StrEnum):
    """The current value's arithmetic direction relative to a reference value."""

    LOWER = "LOWER"
    EQUAL = "EQUAL"
    HIGHER = "HIGHER"
    UNAVAILABLE = "UNAVAILABLE"


class PriceValidationError(ValueError):
    """Raised when a price fact violates the source contract."""


def _require_positive_scale_zero(value: Decimal, *, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise PriceValidationError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise PriceValidationError(f"{field_name} must be finite")
    if value.as_tuple().exponent != 0:
        raise PriceValidationError(f"{field_name} must have Decimal scale 0")
    if value <= 0:
        raise PriceValidationError(f"{field_name} must be greater than zero")


@dataclass(frozen=True, slots=True)
class ReferencePrice:
    period: ComparisonPeriod
    value_status: ValueStatus
    value: Decimal | None
    unavailable_reason: str | None
    reference_date_status: ReferenceDateStatus
    reference_date: date | None

    def __post_init__(self) -> None:
        if not isinstance(self.period, ComparisonPeriod):
            raise PriceValidationError("period must be a ComparisonPeriod")
        if not isinstance(self.value_status, ValueStatus):
            raise PriceValidationError("value_status must be a ValueStatus")
        if not isinstance(self.reference_date_status, ReferenceDateStatus):
            raise PriceValidationError("reference_date_status must be a ReferenceDateStatus")

        if self.value_status is ValueStatus.AVAILABLE:
            if self.value is None or self.unavailable_reason is not None:
                raise PriceValidationError(
                    "an available reference requires value and forbids unavailable_reason"
                )
            _require_positive_scale_zero(self.value, field_name="reference value")
        else:
            if self.value is not None or not isinstance(self.unavailable_reason, str):
                raise PriceValidationError(
                    "an unavailable reference forbids value and requires unavailable_reason"
                )
            if not self.unavailable_reason.strip():
                raise PriceValidationError("unavailable_reason must not be blank")

        has_reference_date = self.reference_date is not None
        date_is_provided = self.reference_date_status is ReferenceDateStatus.PROVIDED
        if has_reference_date != date_is_provided:
            raise PriceValidationError(
                "reference_date is required only when reference_date_status is PROVIDED"
            )
        if has_reference_date and type(self.reference_date) is not date:
            raise PriceValidationError("reference_date must be a date")


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    current_value: Decimal
    references: tuple[ReferencePrice, ...]

    def __post_init__(self) -> None:
        _require_positive_scale_zero(self.current_value, field_name="current_value")
        if not isinstance(self.references, tuple):
            raise PriceValidationError("references must be a tuple")
        if not all(isinstance(reference, ReferencePrice) for reference in self.references):
            raise PriceValidationError("references must contain only ReferencePrice values")

        periods = {reference.period for reference in self.references}
        required_periods = set(ComparisonPeriod)
        if len(self.references) != len(required_periods) or periods != required_periods:
            raise PriceValidationError(
                "a snapshot requires exactly one WEEK, MONTH, and YEAR reference"
            )


@dataclass(frozen=True, slots=True)
class PriceComparison:
    period: ComparisonPeriod
    current_value: Decimal
    reference_value: Decimal | None
    difference: Decimal | None
    percentage: Decimal | None
    direction: Direction
    unavailable_reason: str | None
    reference_date_status: ReferenceDateStatus
    reference_date: date | None


_PERCENT_QUANTUM = Decimal("0.1")


def _compare_reference(current_value: Decimal, reference: ReferencePrice) -> PriceComparison:
    if reference.value_status is ValueStatus.UNAVAILABLE:
        return PriceComparison(
            period=reference.period,
            current_value=current_value,
            reference_value=None,
            difference=None,
            percentage=None,
            direction=Direction.UNAVAILABLE,
            unavailable_reason=reference.unavailable_reason,
            reference_date_status=reference.reference_date_status,
            reference_date=reference.reference_date,
        )

    reference_value = reference.value
    if reference_value is None:  # Defensive narrowing; ReferencePrice rejects this state.
        raise PriceValidationError("available reference is missing its value")

    difference = current_value - reference_value
    if difference < 0:
        direction = Direction.LOWER
    elif difference > 0:
        direction = Direction.HIGHER
    else:
        direction = Direction.EQUAL

    precision = max(len(current_value.as_tuple().digits), len(reference_value.as_tuple().digits))
    with localcontext() as context:
        context.prec = precision + 16
        percentage = ((difference / reference_value) * Decimal(100)).quantize(
            _PERCENT_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    return PriceComparison(
        period=reference.period,
        current_value=current_value,
        reference_value=reference_value,
        difference=difference,
        percentage=percentage,
        direction=direction,
        unavailable_reason=None,
        reference_date_status=reference.reference_date_status,
        reference_date=reference.reference_date,
    )


def compare_snapshot(snapshot: PriceSnapshot) -> tuple[PriceComparison, ...]:
    """Calculate all comparison facts in stable WEEK, MONTH, YEAR order."""

    by_period = {reference.period: reference for reference in snapshot.references}
    return tuple(
        _compare_reference(snapshot.current_value, by_period[period]) for period in ComparisonPeriod
    )
