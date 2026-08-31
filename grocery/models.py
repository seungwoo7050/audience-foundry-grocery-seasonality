import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import connection, models, transaction
from django.db.models import F, Q
from django.utils import timezone

from grocery.pricing import (
    ComparisonPeriod as DomainComparisonPeriod,
)
from grocery.pricing import (
    PriceSnapshot as DomainPriceSnapshot,
)
from grocery.pricing import (
    PriceValidationError,
    compare_snapshot,
)
from grocery.pricing import (
    ReferenceDateStatus as DomainReferenceDateStatus,
)
from grocery.pricing import (
    ReferencePrice as DomainReferencePrice,
)
from grocery.pricing import (
    ValueStatus as DomainValueStatus,
)

SHA256_PATTERN = r"^[0-9a-f]{64}$"
DIGIT_CODE_PATTERN = r"^[0-9]+$"
SOURCE_GATE_EVIDENCE_COMMIT_SHA = "d23e5707e1fc3bf6e032d459b149b946b0451e00"
SOURCE_GATE_DECISION_RECORDED_AT = datetime(2026, 8, 30, 2, 23, 44, tzinfo=UTC)
SOURCE_GATE_ORIGINAL_DATE_TIMESTAMP = datetime(2026, 8, 29, 15, 0, 0, tzinfo=UTC)
SOURCE_GATE_CONFIGURATION_ID = uuid.UUID("e7c14e61-8c56-5758-b1ea-1ea87ea60a41")
SOURCE_GATE_DATASET_ID = "15156063"
SOURCE_GATE_CONFIGURATION_REVISION = "kamis-15156063-recent-comparison-v1"
sha256_validator = RegexValidator(
    regex=SHA256_PATTERN,
    message="Enter a lowercase 64-character SHA-256 digest.",
)


def validate_endpoint_host(value: str) -> None:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value):
        raise ValidationError("Endpoint host must be a lowercase DNS name without a port.")


def validate_endpoint_path(value: str) -> None:
    if not value.startswith("/") or any(character in value for character in "?#\r\n"):
        raise ValidationError("Endpoint path must be an absolute path without a query or fragment.")


def validate_redacted_request_shape(value: str) -> None:
    if "://" in value or "?" in value or "\r" in value or "\n" in value:
        raise ValidationError("Request shape must not contain a URL, query string, or newline.")


class SourceConfiguration(models.Model):
    class State(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        RIGHTS_APPROVED = "RIGHTS_APPROVED", "Rights approved"
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        REJECTED = "REJECTED", "Rejected"

    class PublicationMode(models.TextChoices):
        RECENT_COMPARISON = "RECENT_COMPARISON", "Recent comparison"
        CURRENT_ONLY = "CURRENT_ONLY", "Current only"
        STATIC_MONTHLY_FILE = "STATIC_MONTHLY_FILE", "Static monthly file"
        HISTORICAL_MONTHLY = "HISTORICAL_MONTHLY", "Historical monthly retail"
        HISTORICAL_REGIONAL = "HISTORICAL_REGIONAL", "Historical regional retail"
        HISTORICAL_MARKET = "HISTORICAL_MARKET", "Historical market retail"

    class EndpointScheme(models.TextChoices):
        HTTPS = "https", "HTTPS"

    class EndpointMethod(models.TextChoices):
        GET = "GET", "GET"

    class AuthenticationMode(models.TextChoices):
        NONE = "NONE", "None"
        DATA_GO_KR_SERVICE_KEY = "DATA_GO_KR_SERVICE_KEY", "data.go.kr service key"

    class RawRetention(models.TextChoices):
        HASH_ONLY = "HASH_ONLY", "Hash only"

    class QuotaPeriod(models.TextChoices):
        UNSPECIFIED = "UNSPECIFIED", "Provider did not specify"
        DAY = "DAY", "Per day"
        SECOND = "SECOND", "Per second"

    class RetryPolicy(models.TextChoices):
        BOUNDED_TRANSIENT_ONLY = "BOUNDED_TRANSIENT_ONLY", "Bounded transient failures only"

    class ScheduleExecutionMode(models.TextChoices):
        PLATFORM_SINGLETON = "PLATFORM_SINGLETON", "Platform-owned singleton job"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_owner_name = models.CharField(max_length=200)
    dataset_id = models.CharField(max_length=64)
    configuration_revision = models.CharField(max_length=64)
    interface_revision = models.CharField(max_length=64)
    state = models.CharField(max_length=32, choices=State.choices, default=State.DRAFT)
    state_changed_at = models.DateTimeField(default=timezone.now)
    publication_mode = models.CharField(max_length=32, choices=PublicationMode.choices)
    coverage_identity = models.CharField(max_length=128)
    coverage_evidence_revision = models.CharField(max_length=64)

    endpoint_scheme = models.CharField(
        max_length=8,
        choices=EndpointScheme.choices,
        default=EndpointScheme.HTTPS,
    )
    endpoint_host = models.CharField(max_length=253, validators=[validate_endpoint_host])
    endpoint_path = models.CharField(max_length=512, validators=[validate_endpoint_path])
    endpoint_method = models.CharField(
        max_length=8,
        choices=EndpointMethod.choices,
        default=EndpointMethod.GET,
    )
    authentication_mode = models.CharField(max_length=32, choices=AuthenticationMode.choices)
    logical_secret_name = models.CharField(
        max_length=128,
        blank=True,
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$")],
    )

    provider_quota_limit = models.PositiveIntegerField()
    provider_quota_period = models.CharField(max_length=16, choices=QuotaPeriod.choices)
    request_timeout_seconds = models.PositiveSmallIntegerField()
    retry_policy = models.CharField(max_length=32, choices=RetryPolicy.choices)
    schedule_execution_mode = models.CharField(
        max_length=32,
        choices=ScheduleExecutionMode.choices,
        default=ScheduleExecutionMode.PLATFORM_SINGLETON,
    )
    schedule_interval_hours = models.PositiveSmallIntegerField(default=24)
    max_retries = models.PositiveSmallIntegerField(default=0)
    max_requests_per_attempt = models.PositiveSmallIntegerField()
    max_pages_per_attempt = models.PositiveSmallIntegerField()
    max_page_bytes = models.PositiveIntegerField()

    rights_evidence_locator = models.URLField(max_length=500, blank=True)
    rights_evidence_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[sha256_validator],
    )
    rights_confirmed_at = models.DateTimeField(null=True, blank=True)
    raw_retention = models.CharField(
        max_length=16,
        choices=RawRetention.choices,
        default=RawRetention.HASH_ONLY,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("dataset_id", "configuration_revision"),
                name="grocery_source_dataset_revision_uniq",
            ),
            models.CheckConstraint(
                condition=Q(state__in=("DRAFT", "RIGHTS_APPROVED", "ACTIVE", "PAUSED", "REJECTED")),
                name="grocery_source_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    publication_mode__in=(
                        "RECENT_COMPARISON",
                        "CURRENT_ONLY",
                        "STATIC_MONTHLY_FILE",
                        "HISTORICAL_MONTHLY",
                        "HISTORICAL_REGIONAL",
                        "HISTORICAL_MARKET",
                    )
                ),
                name="grocery_source_publication_mode_valid",
            ),
            models.CheckConstraint(
                condition=Q(endpoint_scheme="https"),
                name="grocery_source_endpoint_scheme_valid",
            ),
            models.CheckConstraint(
                condition=Q(endpoint_host__regex=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$"),
                name="grocery_source_endpoint_host_valid",
            ),
            models.CheckConstraint(
                condition=Q(endpoint_path__startswith="/")
                & ~Q(endpoint_path__contains="?")
                & ~Q(endpoint_path__contains="#"),
                name="grocery_source_endpoint_path_valid",
            ),
            models.CheckConstraint(
                condition=Q(endpoint_method="GET"),
                name="grocery_source_endpoint_method_valid",
            ),
            models.CheckConstraint(
                condition=Q(authentication_mode__in=("NONE", "DATA_GO_KR_SERVICE_KEY")),
                name="grocery_source_auth_mode_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(authentication_mode="NONE", logical_secret_name="")
                    | (
                        Q(authentication_mode="DATA_GO_KR_SERVICE_KEY")
                        & Q(logical_secret_name__regex=r"^[A-Z][A-Z0-9_]*$")  # noqa: S106
                    )
                ),
                name="grocery_source_secret_reference_valid",
            ),
            models.CheckConstraint(
                condition=Q(provider_quota_limit__gt=0),
                name="grocery_source_quota_positive",
            ),
            models.CheckConstraint(
                condition=Q(provider_quota_period__in=("UNSPECIFIED", "DAY", "SECOND")),
                name="grocery_source_quota_period_valid",
            ),
            models.CheckConstraint(
                condition=Q(request_timeout_seconds__gt=0),
                name="grocery_source_timeout_positive",
            ),
            models.CheckConstraint(
                condition=Q(retry_policy="BOUNDED_TRANSIENT_ONLY"),
                name="grocery_source_retry_policy_valid",
            ),
            models.CheckConstraint(
                condition=Q(schedule_execution_mode="PLATFORM_SINGLETON"),
                name="grocery_source_schedule_mode_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    schedule_interval_hours__gt=0,
                    schedule_interval_hours__lte=168,
                ),
                name="grocery_source_schedule_interval_valid",
            ),
            models.CheckConstraint(
                condition=Q(max_retries__gte=0),
                name="grocery_source_retries_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(max_requests_per_attempt__gt=0),
                name="grocery_source_request_budget_positive",
            ),
            models.CheckConstraint(
                condition=Q(max_pages_per_attempt__gt=0),
                name="grocery_source_page_budget_positive",
            ),
            models.CheckConstraint(
                condition=Q(max_page_bytes__gt=0),
                name="grocery_source_byte_budget_positive",
            ),
            models.CheckConstraint(
                condition=Q(rights_evidence_sha256="")
                | Q(rights_evidence_sha256__regex=SHA256_PATTERN),
                name="grocery_source_rights_hash_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        rights_evidence_locator="",
                        rights_evidence_sha256="",
                        rights_confirmed_at__isnull=True,
                    )
                    | (
                        ~Q(rights_evidence_locator="")
                        & ~Q(rights_evidence_sha256="")
                        & Q(rights_confirmed_at__isnull=False)
                    )
                ),
                name="grocery_source_rights_evidence_complete",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(state__in=("RIGHTS_APPROVED", "ACTIVE", "PAUSED"))
                    | Q(rights_confirmed_at__isnull=False)
                ),
                name="grocery_source_approved_rights_present",
            ),
            models.CheckConstraint(
                condition=Q(raw_retention="HASH_ONLY"),
                name="grocery_source_retention_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dataset_id}@{self.configuration_revision}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding and self.pk:
            persisted = type(self).objects.filter(pk=self.pk).first()
            if persisted is not None and persisted.fetch_attempts.exists():
                changed = any(
                    getattr(self, field.attname) != getattr(persisted, field.attname)
                    for field in self._meta.concrete_fields
                    if not field.primary_key
                )
                if changed:
                    raise ValidationError(
                        "Source configuration revisions are immutable after the first fetch "
                        "attempt."
                    )
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.authentication_mode == self.AuthenticationMode.NONE:
            if self.logical_secret_name:
                raise ValidationError(
                    {"logical_secret_name": "Unauthenticated sources cannot reference a secret."}
                )
        elif not self.logical_secret_name:
            raise ValidationError(
                {"logical_secret_name": "Authenticated sources require a logical secret name."}
            )

        evidence_values = (
            bool(self.rights_evidence_locator),
            bool(self.rights_evidence_sha256),
            self.rights_confirmed_at is not None,
        )
        if len(set(evidence_values)) != 1:
            raise ValidationError(
                "Rights evidence locator, hash, and confirmation time are atomic."
            )
        if self.state in {
            self.State.RIGHTS_APPROVED,
            self.State.ACTIVE,
            self.State.PAUSED,
        } and not all(evidence_values):
            raise ValidationError("The selected state requires complete rights evidence.")

    @property
    def artifact_source_identity(self) -> str:
        return ":".join(
            (
                self.dataset_id,
                self.interface_revision,
                self.publication_mode,
                self.coverage_identity,
            )
        )

    def effective_gate_timestamps(self) -> tuple[datetime, datetime | None]:
        """Return the immutable base timestamps or their one reviewed correction."""

        try:
            correction = self.gate_timestamp_correction
        except SourceConfigurationGateTimestampCorrection.DoesNotExist:
            return self.state_changed_at, self.rights_confirmed_at
        if (
            correction.original_state_changed_at != self.state_changed_at
            or correction.original_rights_confirmed_at != self.rights_confirmed_at
        ):
            raise ValidationError("Source gate timestamp correction does not match its base row.")
        return (
            correction.effective_gate_decision_recorded_at,
            correction.effective_gate_decision_recorded_at,
        )


class SourceConfigurationGateTimestampCorrection(models.Model):
    """One append-only correction for the v1 gate timestamp's original date precision."""

    EVIDENCE_COMMIT_SHA = SOURCE_GATE_EVIDENCE_COMMIT_SHA

    class TimestampBasis(models.TextChoices):
        DURABLE_SOURCE_GATE_COMMIT = (
            "DURABLE_SOURCE_GATE_COMMIT",
            "Durable source-gate commit recorded-at upper bound",
        )

    class ReasonCode(models.TextChoices):
        GATE_TIMESTAMP_DATE_PRECISION_CORRECTED = (
            "GATE_TIMESTAMP_DATE_PRECISION_CORRECTED",
            "Gate timestamp date precision corrected",
        )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_configuration = models.OneToOneField(
        SourceConfiguration,
        on_delete=models.PROTECT,
        related_name="gate_timestamp_correction",
    )
    original_state_changed_at = models.DateTimeField()
    original_rights_confirmed_at = models.DateTimeField()
    effective_gate_decision_recorded_at = models.DateTimeField()
    timestamp_basis = models.CharField(
        max_length=40,
        choices=TimestampBasis.choices,
        default=TimestampBasis.DURABLE_SOURCE_GATE_COMMIT,
    )
    evidence_commit_sha = models.CharField(
        max_length=40,
        default=EVIDENCE_COMMIT_SHA,
        validators=[RegexValidator(r"^[0-9a-f]{40}$")],
    )
    reason_code = models.CharField(
        max_length=48,
        choices=ReasonCode.choices,
        default=ReasonCode.GATE_TIMESTAMP_DATE_PRECISION_CORRECTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(source_configuration_id=SOURCE_GATE_CONFIGURATION_ID),
                name="grocery_gate_time_source_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    original_state_changed_at=SOURCE_GATE_ORIGINAL_DATE_TIMESTAMP,
                    original_rights_confirmed_at=SOURCE_GATE_ORIGINAL_DATE_TIMESTAMP,
                ),
                name="grocery_gate_time_original_valid",
            ),
            models.CheckConstraint(
                condition=Q(timestamp_basis="DURABLE_SOURCE_GATE_COMMIT"),
                name="grocery_gate_time_basis_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    reason_code="GATE_TIMESTAMP_DATE_PRECISION_CORRECTED",
                ),
                name="grocery_gate_time_reason_valid",
            ),
            models.CheckConstraint(
                condition=Q(evidence_commit_sha=SOURCE_GATE_EVIDENCE_COMMIT_SHA),
                name="grocery_gate_time_commit_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    effective_gate_decision_recorded_at=SOURCE_GATE_DECISION_RECORDED_AT,
                ),
                name="grocery_gate_time_effective_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(effective_gate_decision_recorded_at__gt=F("original_state_changed_at"))
                    & Q(effective_gate_decision_recorded_at__gt=F("original_rights_confirmed_at"))
                ),
                name="grocery_gate_time_chronology_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_configuration_id}:gate-timestamp-correction"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Source gate timestamp corrections are append-only.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Source gate timestamp corrections are append-only.")

    def clean(self) -> None:
        super().clean()
        if not self.source_configuration_id:
            return
        source = self.source_configuration
        if (
            source.id != SOURCE_GATE_CONFIGURATION_ID
            or source.dataset_id != SOURCE_GATE_DATASET_ID
            or source.configuration_revision != SOURCE_GATE_CONFIGURATION_REVISION
            or self.original_state_changed_at != SOURCE_GATE_ORIGINAL_DATE_TIMESTAMP
            or self.original_rights_confirmed_at != SOURCE_GATE_ORIGINAL_DATE_TIMESTAMP
            or self.effective_gate_decision_recorded_at != SOURCE_GATE_DECISION_RECORDED_AT
            or self.original_state_changed_at != source.state_changed_at
            or self.original_rights_confirmed_at != source.rights_confirmed_at
        ):
            raise ValidationError("Source gate timestamp correction must match its base row.")


class SourceArtifact(models.Model):
    class RetentionMode(models.TextChoices):
        HASH_ONLY = "HASH_ONLY", "Hash only"

    class MediaType(models.TextChoices):
        JSON = "application/json", "JSON"

    class Encoding(models.TextChoices):
        UTF_8 = "utf-8", "UTF-8"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_identity = models.CharField(max_length=512)
    ordered_manifest_sha256 = models.CharField(
        max_length=64,
        validators=[sha256_validator],
    )
    page_count = models.PositiveIntegerField()
    total_bytes = models.PositiveBigIntegerField()
    media_type = models.CharField(
        max_length=32,
        choices=MediaType.choices,
        default=MediaType.JSON,
    )
    encoding = models.CharField(
        max_length=16,
        choices=Encoding.choices,
        default=Encoding.UTF_8,
    )
    retention_mode = models.CharField(
        max_length=16,
        choices=RetentionMode.choices,
        default=RetentionMode.HASH_ONLY,
    )
    first_seen_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("source_identity", "ordered_manifest_sha256"),
                name="grocery_artifact_source_manifest_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(source_identity=""),
                name="grocery_artifact_source_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(ordered_manifest_sha256__regex=SHA256_PATTERN),
                name="grocery_artifact_manifest_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(page_count__gt=0),
                name="grocery_artifact_page_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(total_bytes__gte=0),
                name="grocery_artifact_bytes_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(media_type="application/json"),
                name="grocery_artifact_media_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(encoding="utf-8"),
                name="grocery_artifact_encoding_valid",
            ),
            models.CheckConstraint(
                condition=Q(retention_mode="HASH_ONLY"),
                name="grocery_artifact_retention_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_identity}:{self.ordered_manifest_sha256}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Source artifacts are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)


class FetchAttempt(models.Model):
    class State(models.TextChoices):
        STARTED = "STARTED", "Started"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        RETRYABLE_FAILED = "RETRYABLE_FAILED", "Retryable failed"
        TERMINAL_FAILED = "TERMINAL_FAILED", "Terminal failed"

    class FailureClass(models.TextChoices):
        TIMEOUT = "TIMEOUT", "Timeout"
        NETWORK = "NETWORK", "Network"
        HTTP_429 = "HTTP_429", "HTTP 429"
        HTTP_5XX = "HTTP_5XX", "HTTP 5xx"
        PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT", "Provider transient"
        AUTHENTICATION = "AUTHENTICATION", "Authentication"
        INVALID_REQUEST = "INVALID_REQUEST", "Invalid request"
        RESPONSE_LIMIT = "RESPONSE_LIMIT", "Response limit"
        SCHEMA = "SCHEMA", "Schema"
        IDENTITY = "IDENTITY", "Identity"
        RECONCILIATION = "RECONCILIATION", "Reconciliation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_configuration = models.ForeignKey(
        SourceConfiguration,
        on_delete=models.PROTECT,
        related_name="fetch_attempts",
    )
    artifact = models.ForeignKey(
        SourceArtifact,
        on_delete=models.PROTECT,
        related_name="fetch_attempts",
        null=True,
        blank=True,
    )
    acquisition_run_id = models.UUIDField()
    attempt_ordinal = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=24, choices=State.choices, default=State.STARTED)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    redacted_request_shape = models.CharField(
        max_length=512,
        validators=[validate_redacted_request_shape],
    )
    request_scope_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[sha256_validator],
    )
    received_page_count = models.PositiveIntegerField(default=0)
    received_row_count = models.PositiveIntegerField(default=0)
    received_byte_count = models.PositiveBigIntegerField(default=0)
    failure_class = models.CharField(
        max_length=32,
        choices=FailureClass.choices,
        blank=True,
        default="",
    )
    failure_code = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("acquisition_run_id", "attempt_ordinal"),
                name="grocery_fetch_run_attempt_uniq",
            ),
            models.CheckConstraint(
                condition=Q(attempt_ordinal__gt=0),
                name="grocery_fetch_attempt_ordinal_positive",
            ),
            models.CheckConstraint(
                condition=Q(
                    state__in=("STARTED", "SUCCEEDED", "RETRYABLE_FAILED", "TERMINAL_FAILED")
                ),
                name="grocery_fetch_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(received_page_count__gte=0)
                & Q(received_row_count__gte=0)
                & Q(received_byte_count__gte=0),
                name="grocery_fetch_counts_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(request_scope_sha256="")
                | Q(request_scope_sha256__regex=SHA256_PATTERN),
                name="grocery_fetch_scope_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(failure_class="")
                | Q(
                    failure_class__in=(
                        "TIMEOUT",
                        "NETWORK",
                        "HTTP_429",
                        "HTTP_5XX",
                        "PROVIDER_TRANSIENT",
                        "AUTHENTICATION",
                        "INVALID_REQUEST",
                        "RESPONSE_LIMIT",
                        "SCHEMA",
                        "IDENTITY",
                        "RECONCILIATION",
                    )
                ),
                name="grocery_fetch_failure_class_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state="STARTED",
                        completed_at__isnull=True,
                        failure_class="",
                        failure_code="",
                    )
                    | Q(
                        state="SUCCEEDED",
                        completed_at__isnull=False,
                        failure_class="",
                        failure_code="",
                    )
                    | (
                        Q(state__in=("RETRYABLE_FAILED", "TERMINAL_FAILED"))
                        & Q(completed_at__isnull=False)
                        & ~Q(failure_class="")
                    )
                ),
                name="grocery_fetch_state_outcome_valid",
            ),
            models.CheckConstraint(
                condition=Q(completed_at__isnull=True) | Q(completed_at__gte=F("started_at")),
                name="grocery_fetch_time_order_valid",
            ),
            models.CheckConstraint(
                condition=Q(artifact__isnull=True) | Q(state="SUCCEEDED"),
                name="grocery_fetch_artifact_success_only",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.acquisition_run_id}:{self.attempt_ordinal}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        artifact = self.artifact
        if artifact is None:
            return
        if self.state != self.State.SUCCEEDED:
            raise ValidationError(
                {"artifact": "Only a succeeded attempt can reference an artifact."}
            )
        if artifact.source_identity != self.artifact_source_identity:
            raise ValidationError({"artifact": "Artifact and attempt source identities differ."})

    @property
    def artifact_source_identity(self) -> str:
        base = self.source_configuration.artifact_source_identity
        if not self.request_scope_sha256:
            return base
        return f"{base}:scope-sha256:{self.request_scope_sha256}"


class PageReceipt(models.Model):
    class BodyState(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        NOT_RECEIVED = "NOT_RECEIVED", "Not received"

    class BodyAbsenceReason(models.TextChoices):
        TIMEOUT = "TIMEOUT", "Timeout"
        NETWORK = "NETWORK", "Network"
        REJECTED_BEFORE_BODY = "REJECTED_BEFORE_BODY", "Rejected before body"

    class MediaType(models.TextChoices):
        JSON = "application/json", "JSON"
        XML = "application/xml", "XML"

    class Encoding(models.TextChoices):
        UTF_8 = "utf-8", "UTF-8"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fetch_attempt = models.ForeignKey(
        FetchAttempt,
        on_delete=models.PROTECT,
        related_name="page_receipts",
    )
    request_ordinal = models.PositiveSmallIntegerField()
    page_number = models.PositiveIntegerField()
    received_at = models.DateTimeField(default=timezone.now)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    provider_result_code = models.CharField(max_length=32, blank=True)
    declared_total_count = models.PositiveIntegerField(null=True, blank=True)
    received_row_count = models.PositiveIntegerField(default=0)
    body_state = models.CharField(max_length=16, choices=BodyState.choices)
    body_byte_length = models.PositiveIntegerField(default=0)
    body_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[sha256_validator],
    )
    body_absence_reason = models.CharField(
        max_length=32,
        choices=BodyAbsenceReason.choices,
        blank=True,
        default="",
    )
    media_type = models.CharField(
        max_length=32,
        choices=MediaType.choices,
        blank=True,
        default="",
    )
    encoding = models.CharField(
        max_length=16,
        choices=Encoding.choices,
        blank=True,
        default="",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("fetch_attempt", "request_ordinal"),
                name="grocery_page_attempt_ordinal_uniq",
            ),
            models.UniqueConstraint(
                fields=("fetch_attempt", "page_number"),
                name="grocery_page_attempt_number_uniq",
            ),
            models.CheckConstraint(
                condition=Q(request_ordinal__gt=0),
                name="grocery_page_request_ordinal_positive",
            ),
            models.CheckConstraint(
                condition=Q(page_number__gt=0),
                name="grocery_page_number_positive",
            ),
            models.CheckConstraint(
                condition=Q(http_status__isnull=True)
                | (Q(http_status__gte=100) & Q(http_status__lte=599)),
                name="grocery_page_http_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(declared_total_count__isnull=True) | Q(declared_total_count__gte=0),
                name="grocery_page_declared_total_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(received_row_count__gte=0) & Q(body_byte_length__gte=0),
                name="grocery_page_counts_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(body_state__in=("RECEIVED", "NOT_RECEIVED")),
                name="grocery_page_body_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(body_absence_reason="")
                | Q(body_absence_reason__in=("TIMEOUT", "NETWORK", "REJECTED_BEFORE_BODY")),
                name="grocery_page_absence_reason_valid",
            ),
            models.CheckConstraint(
                condition=Q(media_type="")
                | Q(media_type__in=("application/json", "application/xml")),
                name="grocery_page_media_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(encoding="") | Q(encoding="utf-8"),
                name="grocery_page_encoding_valid",
            ),
            models.CheckConstraint(
                condition=Q(body_sha256="") | Q(body_sha256__regex=SHA256_PATTERN),
                name="grocery_page_body_hash_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        body_state="RECEIVED",
                        http_status__isnull=False,
                        body_absence_reason="",
                    )
                    & ~Q(body_sha256="")
                    & ~Q(media_type="")
                    & ~Q(encoding="")
                    | Q(
                        body_state="NOT_RECEIVED",
                        received_row_count=0,
                        body_byte_length=0,
                        body_sha256="",
                        media_type="",
                        encoding="",
                    )
                    & ~Q(body_absence_reason="")
                ),
                name="grocery_page_body_fields_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.fetch_attempt_id}:{self.request_ordinal}/{self.page_number}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Page receipts are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self._state.adding and self.fetch_attempt.state != FetchAttempt.State.STARTED:
            raise ValidationError("Page receipts can only be added to a started fetch attempt.")


class ParseRun(models.Model):
    class Status(models.TextChoices):
        STARTED = "STARTED", "Started"
        VALIDATED = "VALIDATED", "Validated"
        QUARANTINED = "QUARANTINED", "Quarantined"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact = models.ForeignKey(
        SourceArtifact,
        on_delete=models.PROTECT,
        related_name="parse_runs",
    )
    parser_revision = models.CharField(max_length=64)
    configuration_hash = models.CharField(max_length=64, validators=[sha256_validator])
    result_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[sha256_validator],
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STARTED)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_row_count = models.PositiveIntegerField(default=0)
    accepted_row_count = models.PositiveIntegerField(default=0)
    missing_reference_row_count = models.PositiveIntegerField(default=0)
    out_of_scope_row_count = models.PositiveIntegerField(default=0)
    quarantined_row_count = models.PositiveIntegerField(default=0)
    failure_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$")],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("artifact", "parser_revision", "configuration_hash"),
                name="grocery_parse_artifact_revision_config_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(parser_revision=""),
                name="grocery_parse_revision_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(configuration_hash__regex=SHA256_PATTERN),
                name="grocery_parse_configuration_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(result_hash="") | Q(result_hash__regex=SHA256_PATTERN),
                name="grocery_parse_result_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=("STARTED", "VALIDATED", "QUARANTINED", "FAILED")),
                name="grocery_parse_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(total_row_count__gte=0)
                    & Q(accepted_row_count__gte=0)
                    & Q(missing_reference_row_count__gte=0)
                    & Q(out_of_scope_row_count__gte=0)
                    & Q(quarantined_row_count__gte=0)
                ),
                name="grocery_parse_counts_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(
                    total_row_count=F("accepted_row_count")
                    + F("out_of_scope_row_count")
                    + F("quarantined_row_count")
                ),
                name="grocery_parse_total_reconciled",
            ),
            models.CheckConstraint(
                condition=Q(missing_reference_row_count__lte=F("accepted_row_count")),
                name="grocery_parse_missing_within_accepted",
            ),
            models.CheckConstraint(
                condition=Q(failure_code="") | Q(failure_code__regex=r"^[A-Z][A-Z0-9_]*$"),
                name="grocery_parse_failure_code_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="STARTED",
                        completed_at__isnull=True,
                        result_hash="",
                        failure_code="",
                    )
                    | (
                        Q(
                            status="VALIDATED",
                            completed_at__isnull=False,
                            quarantined_row_count=0,
                            failure_code="",
                        )
                        & ~Q(result_hash="")
                    )
                    | (
                        Q(
                            status="QUARANTINED",
                            completed_at__isnull=False,
                            result_hash="",
                            quarantined_row_count__gt=0,
                        )
                        & ~Q(failure_code="")
                    )
                    | (
                        Q(
                            status="FAILED",
                            completed_at__isnull=False,
                            result_hash="",
                        )
                        & ~Q(failure_code="")
                    )
                ),
                name="grocery_parse_status_outcome_valid",
            ),
            models.CheckConstraint(
                condition=Q(completed_at__isnull=True) | Q(completed_at__gte=F("started_at")),
                name="grocery_parse_time_order_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.artifact_id}:{self.parser_revision}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            persisted = type(self).objects.filter(pk=self.pk).first()
            if persisted is not None:
                immutable_identity = (
                    "artifact_id",
                    "parser_revision",
                    "configuration_hash",
                    "started_at",
                )
                if any(
                    getattr(self, field_name) != getattr(persisted, field_name)
                    for field_name in immutable_identity
                ):
                    raise ValidationError("Parse run identity fields are immutable.")
                if persisted.status != self.Status.STARTED:
                    raise ValidationError("Completed parse runs are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)


class PriceSeriesKey(models.Model):
    """Immutable semantic identity for one reviewed KAMIS retail price series."""

    class ProductClass(models.TextChoices):
        RETAIL = "01", "Retail"

    class Category(models.TextChoices):
        VEGETABLE = "200", "Vegetables"
        FRUIT = "400", "Fruit"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_class_code = models.CharField(
        max_length=2,
        choices=ProductClass.choices,
        default=ProductClass.RETAIL,
    )
    product_class_name = models.CharField(max_length=100)
    category_code = models.CharField(max_length=3, choices=Category.choices)
    category_name = models.CharField(max_length=100)
    item_code = models.CharField(
        max_length=32,
        validators=[RegexValidator(DIGIT_CODE_PATTERN)],
    )
    item_name = models.CharField(max_length=200)
    variety_code = models.CharField(
        max_length=32,
        validators=[RegexValidator(DIGIT_CODE_PATTERN)],
    )
    variety_name = models.CharField(max_length=200)
    grade_code = models.CharField(
        max_length=32,
        validators=[RegexValidator(DIGIT_CODE_PATTERN)],
    )
    grade_name = models.CharField(max_length=200)
    raw_unit = models.CharField(max_length=64)
    raw_unit_size = models.CharField(max_length=64)
    coverage_identity = models.CharField(max_length=128)
    identity_evidence_revision = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "product_class_code",
                    "category_code",
                    "item_code",
                    "variety_code",
                    "grade_code",
                    "raw_unit",
                    "raw_unit_size",
                    "coverage_identity",
                ),
                name="grocery_series_semantic_identity_uniq",
            ),
            models.CheckConstraint(
                condition=Q(product_class_code="01"),
                name="grocery_series_product_class_valid",
            ),
            models.CheckConstraint(
                condition=Q(category_code__in=("200", "400")),
                name="grocery_series_category_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(item_code__regex=DIGIT_CODE_PATTERN)
                    & Q(variety_code__regex=DIGIT_CODE_PATTERN)
                    & Q(grade_code__regex=DIGIT_CODE_PATTERN)
                ),
                name="grocery_series_codes_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(product_class_name="")
                    & ~Q(category_name="")
                    & ~Q(item_name="")
                    & ~Q(variety_name="")
                    & ~Q(grade_name="")
                ),
                name="grocery_series_names_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(raw_unit="") & ~Q(raw_unit_size=""),
                name="grocery_series_raw_unit_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(coverage_identity="") & ~Q(identity_evidence_revision=""),
                name="grocery_series_evidence_nonempty",
            ),
        ]

    def __str__(self) -> str:
        return ":".join(
            (
                self.product_class_code,
                self.category_code,
                self.item_code,
                self.variety_code,
                self.grade_code,
                self.raw_unit,
                self.raw_unit_size,
                self.coverage_identity,
            )
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Price series keys are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Price series keys are immutable.")

    @classmethod
    @transaction.atomic
    def get_or_validate(
        cls,
        *,
        product_class_code: str,
        product_class_name: str,
        category_code: str,
        category_name: str,
        item_code: str,
        item_name: str,
        variety_code: str,
        variety_name: str,
        grade_code: str,
        grade_name: str,
        raw_unit: str,
        raw_unit_size: str,
        coverage_identity: str,
        identity_evidence_revision: str,
    ) -> PriceSeriesKey:
        identity = {
            "product_class_code": product_class_code,
            "category_code": category_code,
            "item_code": item_code,
            "variety_code": variety_code,
            "grade_code": grade_code,
            "raw_unit": raw_unit,
            "raw_unit_size": raw_unit_size,
            "coverage_identity": coverage_identity,
        }
        reviewed_fields = {
            "product_class_name": product_class_name,
            "category_name": category_name,
            "item_name": item_name,
            "variety_name": variety_name,
            "grade_name": grade_name,
            "identity_evidence_revision": identity_evidence_revision,
        }
        series, _ = cls.objects.get_or_create(**identity, defaults=reviewed_fields)
        if any(getattr(series, field) != value for field, value in reviewed_fields.items()):
            raise ValidationError(
                "Price series display identity or evidence revision drifted for an existing "
                "semantic identity."
            )
        return series


class RetailPriceSnapshot(models.Model):
    """Immutable current price from one validated recent-source parse generation."""

    class Currency(models.TextChoices):
        KRW = "KRW", "South Korean won"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parse_run = models.ForeignKey(
        ParseRun,
        on_delete=models.PROTECT,
        related_name="retail_price_snapshots",
    )
    series = models.ForeignKey(
        PriceSeriesKey,
        on_delete=models.PROTECT,
        related_name="retail_price_snapshots",
    )
    source_effective_date = models.DateField()
    source_recorded_at = models.DateTimeField(null=True, blank=True)
    current_price = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        validators=[MinValueValidator(Decimal("1"))],
    )
    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.KRW,
    )
    source_row_sha256 = models.CharField(max_length=64, validators=[sha256_validator])
    source_contract_revision = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("parse_run", "series", "source_effective_date"),
                name="grocery_snapshot_run_series_date_uniq",
            ),
            models.UniqueConstraint(
                fields=("parse_run", "series"),
                name="grocery_snapshot_run_series_uniq",
            ),
            models.CheckConstraint(
                condition=Q(current_price__gt=0),
                name="grocery_snapshot_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(currency="KRW"),
                name="grocery_snapshot_currency_valid",
            ),
            models.CheckConstraint(
                condition=Q(source_row_sha256__regex=SHA256_PATTERN),
                name="grocery_snapshot_row_hash_valid",
            ),
            models.CheckConstraint(
                condition=~Q(source_contract_revision=""),
                name="grocery_snapshot_contract_nonempty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.parse_run_id}:{self.series_id}:{self.source_effective_date}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Retail price snapshots are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Retail price snapshots are immutable.")

    def clean(self) -> None:
        super().clean()
        if self.parse_run_id and self.parse_run.status != ParseRun.Status.VALIDATED:
            raise ValidationError(
                {"parse_run": "Retail price snapshots require a validated parse run."}
            )

    @classmethod
    @transaction.atomic
    def get_or_validate(
        cls,
        *,
        parse_run_id: uuid.UUID,
        series_id: uuid.UUID,
        source_effective_date: date,
        source_recorded_at: datetime | None,
        current_price: Decimal,
        source_row_sha256: str,
        source_contract_revision: str,
    ) -> RetailPriceSnapshot:
        parse_run = ParseRun.objects.select_for_update().get(pk=parse_run_id)
        series = PriceSeriesKey.objects.select_for_update().get(pk=series_id)
        if parse_run.status != ParseRun.Status.VALIDATED:
            raise ValidationError("Retail price snapshots require a validated parse run.")

        semantic_fields: dict[str, object] = {
            "source_effective_date": source_effective_date,
            "source_recorded_at": source_recorded_at,
            "current_price": current_price,
            "currency": cls.Currency.KRW,
            "source_row_sha256": source_row_sha256,
            "source_contract_revision": source_contract_revision,
        }
        candidate = cls(parse_run=parse_run, series=series, **semantic_fields)
        candidate.full_clean(validate_unique=False, validate_constraints=False)

        existing = (
            cls.objects.select_for_update().filter(parse_run=parse_run, series=series).first()
        )
        if existing is not None:
            if any(
                getattr(existing, field_name) != value
                for field_name, value in semantic_fields.items()
            ):
                raise ValidationError(
                    "Retail price snapshot replay conflicts with the existing generation series."
                )
            return existing

        candidate.save()
        return candidate


class ReferencePrice(models.Model):
    """Immutable provider reference value attached to one current-price snapshot."""

    class Period(models.TextChoices):
        WEEK = "WEEK", "One week"
        MONTH = "MONTH", "One month"
        YEAR = "YEAR", "One year"

    class ValueStatus(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        UNAVAILABLE = "UNAVAILABLE", "Unavailable"

    class UnavailableReason(models.TextChoices):
        SOURCE_VALUE_MISSING = "SOURCE_VALUE_MISSING", "Source value missing"

    class ReferenceDateStatus(models.TextChoices):
        SOURCE_REFERENCE_DATE_UNAVAILABLE = (
            "SOURCE_REFERENCE_DATE_UNAVAILABLE",
            "Source reference date unavailable",
        )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(
        RetailPriceSnapshot,
        on_delete=models.PROTECT,
        related_name="reference_prices",
    )
    period = models.CharField(max_length=8, choices=Period.choices)
    value_status = models.CharField(max_length=16, choices=ValueStatus.choices)
    value = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("1"))],
    )
    unavailable_reason = models.CharField(  # noqa: DJ001 - NULL means no missing-value reason.
        max_length=32,
        choices=UnavailableReason.choices,
        null=True,
        blank=True,
    )
    reference_date_status = models.CharField(
        max_length=40,
        choices=ReferenceDateStatus.choices,
        default=ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE,
    )
    source_reference_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "period"),
                name="grocery_reference_snapshot_period_uniq",
            ),
            models.CheckConstraint(
                condition=Q(period__in=("WEEK", "MONTH", "YEAR")),
                name="grocery_reference_period_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        value_status="AVAILABLE",
                        value__isnull=False,
                        unavailable_reason__isnull=True,
                    )
                    & Q(value__gt=0)
                    | Q(
                        value_status="UNAVAILABLE",
                        value__isnull=True,
                        unavailable_reason__isnull=False,
                        unavailable_reason="SOURCE_VALUE_MISSING",
                    )
                ),
                name="grocery_reference_value_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    reference_date_status="SOURCE_REFERENCE_DATE_UNAVAILABLE",
                    source_reference_date__isnull=True,
                ),
                name="grocery_reference_date_state_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.snapshot_id}:{self.period}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Reference prices are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Reference prices are immutable.")

    def clean(self) -> None:
        super().clean()
        if self.value_status == self.ValueStatus.AVAILABLE:
            if self.value is None or self.unavailable_reason is not None:
                raise ValidationError(
                    "Available reference prices require a value and forbid an unavailable reason."
                )
        elif self.value_status == self.ValueStatus.UNAVAILABLE:
            if (
                self.value is not None
                or self.unavailable_reason != self.UnavailableReason.SOURCE_VALUE_MISSING
            ):
                raise ValidationError(
                    "Unavailable reference prices require SOURCE_VALUE_MISSING and forbid a value."
                )

        if (
            self.reference_date_status != self.ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
            or self.source_reference_date is not None
        ):
            raise ValidationError(
                "The source does not provide reference dates; dates must remain unavailable."
            )

    @classmethod
    @transaction.atomic
    def get_or_validate(
        cls,
        *,
        snapshot_id: uuid.UUID,
        period: str,
        value_status: str,
        value: Decimal | None,
        unavailable_reason: str | None,
        reference_date_status: str = ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE,
        source_reference_date: date | None = None,
    ) -> ReferencePrice:
        snapshot = RetailPriceSnapshot.objects.select_for_update().get(pk=snapshot_id)
        semantic_fields: dict[str, object] = {
            "value_status": value_status,
            "value": value,
            "unavailable_reason": unavailable_reason,
            "reference_date_status": reference_date_status,
            "source_reference_date": source_reference_date,
        }
        candidate = cls(snapshot=snapshot, period=period, **semantic_fields)
        candidate.full_clean(validate_unique=False, validate_constraints=False)

        existing = cls.objects.select_for_update().filter(snapshot=snapshot, period=period).first()
        if existing is not None:
            if any(
                getattr(existing, field_name) != field_value
                for field_name, field_value in semantic_fields.items()
            ):
                raise ValidationError(
                    "Reference price replay conflicts with the existing snapshot period."
                )
            return existing

        candidate.save()
        return candidate


class PriceChangeFact(models.Model):
    """Immutable, reproducible arithmetic derived from one reference price."""

    CALCULATION_REVISION = "PRICE_COMPARISON_V1"

    class Direction(models.TextChoices):
        LOWER = "LOWER", "Lower"
        EQUAL = "EQUAL", "Equal"
        HIGHER = "HIGHER", "Higher"
        UNAVAILABLE = "UNAVAILABLE", "Unavailable"

    class RoundingMode(models.TextChoices):
        ROUND_HALF_UP = "ROUND_HALF_UP", "Round half up"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference_price = models.OneToOneField(
        ReferencePrice,
        on_delete=models.PROTECT,
        related_name="change_fact",
    )
    direction = models.CharField(max_length=16, choices=Direction.choices)
    signed_difference = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        null=True,
        blank=True,
    )
    signed_percentage = models.DecimalField(
        max_digits=16,
        decimal_places=1,
        null=True,
        blank=True,
    )
    calculation_revision = models.CharField(
        max_length=64,
        default=CALCULATION_REVISION,
        editable=False,
    )
    rounding_mode = models.CharField(
        max_length=16,
        choices=RoundingMode.choices,
        default=RoundingMode.ROUND_HALF_UP,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(direction__in=("LOWER", "EQUAL", "HIGHER", "UNAVAILABLE")),
                name="grocery_change_direction_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        direction="UNAVAILABLE",
                        signed_difference__isnull=True,
                        signed_percentage__isnull=True,
                    )
                    | (
                        Q(signed_difference__isnull=False, signed_percentage__isnull=False)
                        & (
                            Q(direction="LOWER", signed_difference__lt=0)
                            | Q(direction="EQUAL", signed_difference=0)
                            | Q(direction="HIGHER", signed_difference__gt=0)
                        )
                    )
                ),
                name="grocery_change_value_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(calculation_revision="PRICE_COMPARISON_V1"),
                name="grocery_change_revision_valid",
            ),
            models.CheckConstraint(
                condition=Q(rounding_mode="ROUND_HALF_UP"),
                name="grocery_change_rounding_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference_price_id}:{self.calculation_revision}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Price change facts are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Price change facts are immutable.")

    @classmethod
    def _expected_fields(cls, reference_price: ReferencePrice) -> dict[str, object]:
        stored_references = list(
            ReferencePrice.objects.filter(snapshot_id=reference_price.snapshot_id)
        )
        if len(stored_references) != len(DomainComparisonPeriod) or {
            stored.period for stored in stored_references
        } != {period.value for period in DomainComparisonPeriod}:
            raise ValidationError(
                "Price change calculation requires exactly one WEEK, MONTH, and YEAR reference."
            )

        try:
            domain_references = tuple(
                DomainReferencePrice(
                    period=DomainComparisonPeriod(stored.period),
                    value_status=DomainValueStatus(stored.value_status),
                    value=stored.value,
                    unavailable_reason=stored.unavailable_reason,
                    reference_date_status=DomainReferenceDateStatus(stored.reference_date_status),
                    reference_date=stored.source_reference_date,
                )
                for stored in stored_references
            )
            comparisons = compare_snapshot(
                DomainPriceSnapshot(
                    current_value=reference_price.snapshot.current_price,
                    references=domain_references,
                )
            )
        except PriceValidationError as error:
            raise ValidationError(
                "Stored price values violate the calculation contract."
            ) from error

        comparison = next(
            item for item in comparisons if item.period.value == reference_price.period
        )
        return {
            "direction": comparison.direction.value,
            "signed_difference": comparison.difference,
            "signed_percentage": comparison.percentage,
            "calculation_revision": cls.CALCULATION_REVISION,
            "rounding_mode": cls.RoundingMode.ROUND_HALF_UP,
        }

    def clean(self) -> None:
        super().clean()
        if not self.reference_price_id:
            return
        expected = type(self)._expected_fields(self.reference_price)
        if any(getattr(self, field_name) != value for field_name, value in expected.items()):
            raise ValidationError(
                "Price change fact does not match the deterministic calculation contract."
            )

    @classmethod
    @transaction.atomic
    def get_or_validate(cls, *, reference_price_id: uuid.UUID) -> PriceChangeFact:
        reference_price = (
            ReferencePrice.objects.select_for_update()
            .select_related("snapshot")
            .get(pk=reference_price_id)
        )
        list(
            ReferencePrice.objects.select_for_update().filter(
                snapshot_id=reference_price.snapshot_id
            )
        )
        expected = cls._expected_fields(reference_price)
        candidate = cls(reference_price=reference_price, **expected)
        candidate.full_clean(validate_unique=False, validate_constraints=False)

        existing = cls.objects.select_for_update().filter(reference_price=reference_price).first()
        if existing is not None:
            if any(
                getattr(existing, field_name) != field_value
                for field_name, field_value in expected.items()
            ):
                raise ValidationError(
                    "Price change replay conflicts with the stored deterministic fact."
                )
            return existing

        candidate.save()
        return candidate


@transaction.atomic
def persist_reference_price_facts(
    *,
    snapshot_id: uuid.UUID,
    reference_values: Mapping[str, Decimal | None],
) -> tuple[PriceChangeFact, ...]:
    """Persist exactly three provider references and their deterministic comparisons."""

    periods = tuple(period.value for period in DomainComparisonPeriod)
    if set(reference_values) != set(periods) or len(reference_values) != len(periods):
        raise ValidationError("Reference input requires exactly WEEK, MONTH, and YEAR.")

    RetailPriceSnapshot.objects.select_for_update().get(pk=snapshot_id)
    references = tuple(
        ReferencePrice.get_or_validate(
            snapshot_id=snapshot_id,
            period=period,
            value_status=(
                ReferencePrice.ValueStatus.UNAVAILABLE
                if reference_values[period] is None
                else ReferencePrice.ValueStatus.AVAILABLE
            ),
            value=reference_values[period],
            unavailable_reason=(
                ReferencePrice.UnavailableReason.SOURCE_VALUE_MISSING
                if reference_values[period] is None
                else None
            ),
        )
        for period in periods
    )
    return tuple(
        PriceChangeFact.get_or_validate(reference_price_id=reference.id) for reference in references
    )


class ReviewDecision(models.Model):
    """Append-only human decision over one complete parsed source generation."""

    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    decision = models.CharField(max_length=8, choices=Decision.choices)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="grocery_review_decisions",
    )
    decided_at = models.DateTimeField(default=timezone.now)
    source_configuration = models.ForeignKey(
        SourceConfiguration,
        on_delete=models.PROTECT,
        related_name="review_decisions",
    )
    source_artifact = models.ForeignKey(
        SourceArtifact,
        on_delete=models.PROTECT,
        related_name="review_decisions",
    )
    parse_run = models.ForeignKey(
        ParseRun,
        on_delete=models.PROTECT,
        related_name="review_decisions",
    )
    reconciliation_report_sha256 = models.CharField(
        max_length=64,
        validators=[sha256_validator],
    )
    acceptance_evidence_sha256 = models.CharField(
        max_length=64,
        validators=[sha256_validator],
    )
    reason_code = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$")],
    )
    approved_mode = models.CharField(
        max_length=32,
        choices=SourceConfiguration.PublicationMode.choices,
        blank=True,
        default="",
    )
    approved_coverage_identity = models.CharField(max_length=128, blank=True, default="")
    approved_coverage_evidence_revision = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="superseding_decision",
        null=True,
        blank=True,
    )

    class Meta:
        permissions = [
            ("review_generation", "Can review a parsed grocery source generation"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(decision__in=("APPROVE", "REJECT")),
                name="grocery_review_decision_valid",
            ),
            models.CheckConstraint(
                condition=Q(reconciliation_report_sha256__regex=SHA256_PATTERN),
                name="grocery_review_report_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(acceptance_evidence_sha256__regex=SHA256_PATTERN),
                name="grocery_review_acceptance_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(reason_code__regex=r"^[A-Z][A-Z0-9_]*$"),
                name="grocery_review_reason_code_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(decision="APPROVE", approved_mode="RECENT_COMPARISON")
                    & ~Q(approved_coverage_identity="")
                    & ~Q(approved_coverage_evidence_revision="")
                    | Q(
                        decision="REJECT",
                        approved_mode="",
                        approved_coverage_identity="",
                        approved_coverage_evidence_revision="",
                    )
                ),
                name="grocery_review_approval_fields_valid",
            ),
            models.CheckConstraint(
                condition=Q(supersedes__isnull=True) | ~Q(id=F("supersedes_id")),
                name="grocery_review_not_self_supersede",
            ),
            models.UniqueConstraint(
                fields=("source_configuration", "source_artifact", "parse_run"),
                condition=Q(supersedes__isnull=True),
                name="grocery_review_generation_root_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.parse_run_id}:{self.decision}:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Review decisions are append-only.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Review decisions are append-only.")

    def clean(self) -> None:
        super().clean()
        if not all(
            (
                self.source_configuration_id,
                self.source_artifact_id,
                self.parse_run_id,
            )
        ):
            return

        source_configuration = self.source_configuration
        source_artifact = self.source_artifact
        parse_run = self.parse_run
        effective_state_changed_at, effective_rights_confirmed_at = (
            source_configuration.effective_gate_timestamps()
        )
        rights_complete = all(
            (
                source_configuration.rights_evidence_locator,
                source_configuration.rights_evidence_sha256,
                effective_rights_confirmed_at,
            )
        )
        if source_configuration.state != SourceConfiguration.State.ACTIVE or not rights_complete:
            raise ValidationError("Review requires an active source with complete rights evidence.")
        if (
            effective_state_changed_at > self.decided_at
            or effective_rights_confirmed_at is None
            or effective_rights_confirmed_at > self.decided_at
        ):
            raise ValidationError("Review cannot precede the effective source gate decision.")
        if not FetchAttempt.objects.filter(
            source_configuration=source_configuration,
            artifact=source_artifact,
            state=FetchAttempt.State.SUCCEEDED,
        ).exists():
            raise ValidationError(
                "Review artifact must be linked to the source by a succeeded fetch attempt."
            )
        if parse_run.artifact_id != source_artifact.id:
            raise ValidationError("Review parse run and source artifact do not match.")

        if self.supersedes_id:
            superseded = type(self).objects.filter(pk=self.supersedes_id).first()
            if superseded is None:
                raise ValidationError("Superseded review decision does not exist.")
            if superseded.id == self.id:
                raise ValidationError("A review decision cannot supersede itself.")
            if (
                superseded.source_configuration_id,
                superseded.source_artifact_id,
                superseded.parse_run_id,
            ) != (
                self.source_configuration_id,
                self.source_artifact_id,
                self.parse_run_id,
            ):
                raise ValidationError("A review decision can only supersede the same generation.")
            if type(self).objects.filter(supersedes_id=superseded.id).exclude(pk=self.pk).exists():
                raise ValidationError("Only the current review tail can be superseded.")
        elif (
            self._state.adding
            and type(self)
            .objects.filter(
                source_configuration_id=self.source_configuration_id,
                source_artifact_id=self.source_artifact_id,
                parse_run_id=self.parse_run_id,
                supersedes__isnull=True,
            )
            .exclude(pk=self.pk)
            .exists()
        ):
            raise ValidationError("The review generation already has a root decision.")

        if self.decision == self.Decision.REJECT:
            if parse_run.completed_at is None or parse_run.status == ParseRun.Status.STARTED:
                raise ValidationError("Rejected review decisions require a completed parse run.")
            return

        if self.decision != self.Decision.APPROVE:
            return
        if (
            parse_run.status != ParseRun.Status.VALIDATED
            or parse_run.completed_at is None
            or not parse_run.result_hash
            or parse_run.quarantined_row_count != 0
        ):
            raise ValidationError(
                "Approved review decisions require a reconciled, validated parse run."
            )
        if (
            source_configuration.publication_mode
            != SourceConfiguration.PublicationMode.RECENT_COMPARISON
            or self.approved_mode != source_configuration.publication_mode
            or self.approved_coverage_identity != source_configuration.coverage_identity
            or self.approved_coverage_evidence_revision
            != source_configuration.coverage_evidence_revision
        ):
            raise ValidationError(
                "Approved mode and coverage must exactly match the active source configuration."
            )

        snapshots = list(
            RetailPriceSnapshot.objects.filter(parse_run=parse_run).prefetch_related(
                "reference_prices__change_fact"
            )
        )
        if len(snapshots) != parse_run.accepted_row_count:
            raise ValidationError("Snapshot count must equal the parse accepted-row count.")
        required_periods = {period.value for period in DomainComparisonPeriod}
        for snapshot in snapshots:
            references = list(snapshot.reference_prices.all())
            if (
                len(references) != 3
                or {reference.period for reference in references} != required_periods
            ):
                raise ValidationError(
                    "Every approved snapshot requires exact WEEK, MONTH, and YEAR references."
                )
            if any(not hasattr(reference, "change_fact") for reference in references):
                raise ValidationError(
                    "Every approved reference requires a deterministic price change fact."
                )


@transaction.atomic
def record_review_decision(
    *,
    decision_id: uuid.UUID,
    actor: Any,
    decision: str,
    source_configuration_id: uuid.UUID,
    source_artifact_id: uuid.UUID,
    parse_run_id: uuid.UUID,
    reconciliation_report_sha256: str,
    acceptance_evidence_sha256: str,
    reason_code: str,
    approved_mode: str = "",
    approved_coverage_identity: str = "",
    approved_coverage_evidence_revision: str = "",
    supersedes_id: uuid.UUID | None = None,
) -> tuple[ReviewDecision, bool]:
    """Record one authorized decision, returning exact UUID replays idempotently."""

    has_permission = getattr(actor, "has_perm", None)
    if (
        getattr(actor, "pk", None) is None
        or not bool(getattr(actor, "is_authenticated", False))
        or not bool(getattr(actor, "is_active", False))
        or not callable(has_permission)
        or not has_permission("grocery.review_generation")
    ):
        raise PermissionDenied("An active reviewer with review_generation permission is required.")

    semantic_fields: dict[str, object] = {
        "reviewer_id": actor.pk,
        "decision": decision,
        "source_configuration_id": source_configuration_id,
        "source_artifact_id": source_artifact_id,
        "parse_run_id": parse_run_id,
        "reconciliation_report_sha256": reconciliation_report_sha256,
        "acceptance_evidence_sha256": acceptance_evidence_sha256,
        "reason_code": reason_code,
        "approved_mode": approved_mode,
        "approved_coverage_identity": approved_coverage_identity,
        "approved_coverage_evidence_revision": approved_coverage_evidence_revision,
        "supersedes_id": supersedes_id,
    }
    existing = ReviewDecision.objects.select_for_update().filter(pk=decision_id).first()
    if existing is not None:
        if any(
            getattr(existing, field_name) != field_value
            for field_name, field_value in semantic_fields.items()
        ):
            raise ValidationError("Review decision UUID replay conflicts with stored evidence.")
        return existing, False

    SourceConfiguration.objects.select_for_update().get(pk=source_configuration_id)
    SourceArtifact.objects.select_for_update().get(pk=source_artifact_id)
    ParseRun.objects.select_for_update().get(pk=parse_run_id)
    list(
        ReviewDecision.objects.select_for_update().filter(
            source_configuration_id=source_configuration_id,
            source_artifact_id=source_artifact_id,
            parse_run_id=parse_run_id,
        )
    )
    candidate = ReviewDecision(id=decision_id, **semantic_fields)
    candidate.save()
    return candidate, True


class PublicationRevision(models.Model):
    """Immutable, sealed recent-retail publication over one approved generation."""

    FACT_HASH_VERSION = "recent-retail-fact-set-v1"

    class Channel(models.TextChoices):
        RECENT_RETAIL = "RECENT_RETAIL", "Recent retail"

    class Mode(models.TextChoices):
        RECENT_COMPARISON = "RECENT_COMPARISON", "Recent comparison"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.CharField(
        max_length=32,
        choices=Channel.choices,
        default=Channel.RECENT_RETAIL,
    )
    mode = models.CharField(
        max_length=32,
        choices=Mode.choices,
        default=Mode.RECENT_COMPARISON,
    )
    review_decision = models.ForeignKey(
        ReviewDecision,
        on_delete=models.PROTECT,
        related_name="publication_revisions",
    )
    generation = models.ForeignKey(
        ParseRun,
        on_delete=models.PROTECT,
        related_name="publication_revisions",
    )
    parser_revision = models.CharField(max_length=64)
    fact_hash_version = models.CharField(
        max_length=64,
        default=FACT_HASH_VERSION,
        editable=False,
    )
    typed_fact_set_sha256 = models.CharField(
        max_length=64,
        validators=[sha256_validator],
    )
    entry_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    source_effective_date_min = models.DateField()
    source_effective_date_max = models.DateField()
    public_copy_revision = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[a-z0-9][a-z0-9._-]{0,63}$")],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sealed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("channel", "typed_fact_set_sha256", "public_copy_revision"),
                name="grocery_publication_set_copy_uniq",
            ),
            models.CheckConstraint(
                condition=Q(channel="RECENT_RETAIL"),
                name="grocery_publication_channel_valid",
            ),
            models.CheckConstraint(
                condition=Q(mode="RECENT_COMPARISON"),
                name="grocery_publication_mode_valid",
            ),
            models.CheckConstraint(
                condition=Q(fact_hash_version="recent-retail-fact-set-v1"),
                name="grocery_publication_hash_version_valid",
            ),
            models.CheckConstraint(
                condition=Q(typed_fact_set_sha256__regex=SHA256_PATTERN),
                name="grocery_publication_set_hash_valid",
            ),
            models.CheckConstraint(
                condition=Q(entry_count__gt=0),
                name="grocery_publication_entry_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(source_effective_date_max__gte=F("source_effective_date_min")),
                name="grocery_publication_date_range_valid",
            ),
            models.CheckConstraint(
                condition=Q(public_copy_revision__regex=r"^[a-z0-9][a-z0-9._-]{0,63}$"),
                name="grocery_publication_copy_revision_valid",
            ),
            models.CheckConstraint(
                condition=Q(sealed_at__isnull=True) | Q(sealed_at__gte=F("created_at")),
                name="grocery_publication_seal_time_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.typed_fact_set_sha256}:{self.public_copy_revision}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Publication revisions are immutable after construction.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Publication revisions are immutable after construction.")


class PublicationEntry(models.Model):
    """Immutable ordered snapshot membership in one publication revision."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(
        PublicationRevision,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    snapshot = models.ForeignKey(
        RetailPriceSnapshot,
        on_delete=models.PROTECT,
        related_name="publication_entries",
    )
    ordinal = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    fact_sha256 = models.CharField(max_length=64, validators=[sha256_validator])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("revision", "ordinal"),
                name="grocery_publication_entry_ordinal_uniq",
            ),
            models.UniqueConstraint(
                fields=("revision", "snapshot"),
                name="grocery_publication_entry_snapshot_uniq",
            ),
            models.CheckConstraint(
                condition=Q(ordinal__gt=0),
                name="grocery_publication_entry_ordinal_positive",
            ),
            models.CheckConstraint(
                condition=Q(fact_sha256__regex=SHA256_PATTERN),
                name="grocery_publication_entry_hash_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.revision_id}:{self.ordinal}:{self.snapshot_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Publication entries are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Publication entries are immutable.")

    def clean(self) -> None:
        super().clean()
        if not self.revision_id or not self.snapshot_id:
            return
        if self.revision.sealed_at is not None:
            raise ValidationError("Publication entries can only be added before sealing.")
        if self.snapshot.parse_run_id != self.revision.generation_id:
            raise ValidationError("Publication entries must belong to the revision generation.")


class PublicationChannel(models.Model):
    """The guarded current pointer for the one approved recent-retail channel."""

    channel = models.CharField(
        primary_key=True,
        max_length=32,
        choices=PublicationRevision.Channel.choices,
        default=PublicationRevision.Channel.RECENT_RETAIL,
        editable=False,
    )
    current_revision = models.ForeignKey(
        PublicationRevision,
        on_delete=models.PROTECT,
        related_name="current_for_channels",
        null=True,
        blank=True,
    )
    version = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(channel="RECENT_RETAIL"),
                name="grocery_publication_pointer_channel_valid",
            ),
            models.CheckConstraint(
                condition=(Q(version=0, current_revision__isnull=True) | Q(version__gt=0)),
                name="grocery_publication_pointer_initial_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.version}:{self.current_revision_id or 'WITHDRAWN'}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        raise ValidationError(
            "Publication channels can only be changed by the publication transition service."
        )

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Publication channels cannot be deleted.")


class PublicationActivation(models.Model):
    """Append-only evidence for one atomic recent-retail pointer transition."""

    class Operation(models.TextChoices):
        ACTIVATE = "ACTIVATE", "Activate"
        ROLLBACK = "ROLLBACK", "Rollback"
        WITHDRAW = "WITHDRAW", "Withdraw"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        PublicationChannel,
        on_delete=models.PROTECT,
        related_name="activations",
    )
    operation = models.CharField(max_length=16, choices=Operation.choices)
    sequence = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    previous_revision = models.ForeignKey(
        PublicationRevision,
        on_delete=models.PROTECT,
        related_name="previous_publication_activations",
        null=True,
        blank=True,
    )
    target_revision = models.ForeignKey(
        PublicationRevision,
        on_delete=models.PROTECT,
        related_name="target_publication_activations",
        null=True,
        blank=True,
    )
    publisher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="grocery_publication_activations",
    )
    reason_code = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$")],
    )
    acceptance_evidence_sha256 = models.CharField(
        max_length=64,
        validators=[sha256_validator],
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    _transition_write = False

    class Meta:
        permissions = [
            ("publish_publication", "Can transition a grocery publication"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(channel_id="RECENT_RETAIL"),
                name="grocery_activation_channel_valid",
            ),
            models.CheckConstraint(
                condition=Q(operation__in=("ACTIVATE", "ROLLBACK", "WITHDRAW")),
                name="grocery_activation_operation_valid",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gt=0),
                name="grocery_activation_sequence_positive",
            ),
            models.CheckConstraint(
                condition=Q(reason_code__regex=r"^[A-Z][A-Z0-9_]*$"),
                name="grocery_activation_reason_code_valid",
            ),
            models.CheckConstraint(
                condition=Q(acceptance_evidence_sha256__regex=SHA256_PATTERN),
                name="grocery_activation_evidence_hash_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(operation__in=("ACTIVATE", "ROLLBACK"), target_revision__isnull=False)
                    | Q(operation="WITHDRAW", target_revision__isnull=True)
                ),
                name="grocery_activation_target_shape_valid",
            ),
            models.UniqueConstraint(
                fields=("channel", "sequence"),
                name="grocery_activation_channel_sequence_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.sequence}:{self.operation}:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Publication activations are append-only.")
        if not self._transition_write:
            raise ValidationError(
                "Publication activations can only be added by the transition service."
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Publication activations are append-only.")


def _set_publication_transition_token(operation_id: uuid.UUID | None) -> None:
    token = "" if operation_id is None else str(operation_id)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('grocery.publication_transition_id', %s, true)",
            [token],
        )


def _bootstrap_recent_publication_channel(operation_id: uuid.UUID) -> None:
    _set_publication_transition_token(operation_id)
    PublicationChannel.objects.bulk_create(
        [
            PublicationChannel(
                channel=PublicationRevision.Channel.RECENT_RETAIL,
                current_revision_id=None,
                version=0,
            )
        ],
        ignore_conflicts=True,
    )


def _update_recent_publication_pointer(
    *,
    expected_current_revision_id: uuid.UUID | None,
    expected_version: int,
    target_revision_id: uuid.UUID | None,
) -> int:
    return PublicationChannel.objects.filter(
        pk=PublicationRevision.Channel.RECENT_RETAIL,
        current_revision_id=expected_current_revision_id,
        version=expected_version,
    ).update(
        current_revision_id=target_revision_id,
        version=expected_version + 1,
    )


@transaction.atomic
def transition_recent_publication(
    *,
    operation_id: uuid.UUID,
    actor: Any,
    operation: str,
    target_revision_id: uuid.UUID | None,
    expected_current_revision_id: uuid.UUID | None,
    expected_version: int,
    reason_code: str,
    acceptance_evidence_sha256: str,
) -> tuple[PublicationActivation, bool]:
    """Append one authorized event and atomically advance the fixed current pointer."""

    has_permission = getattr(actor, "has_perm", None)
    if (
        getattr(actor, "pk", None) is None
        or not bool(getattr(actor, "is_authenticated", False))
        or not bool(getattr(actor, "is_active", False))
        or not callable(has_permission)
        or not has_permission("grocery.publish_publication")
    ):
        raise PermissionDenied(
            "An active publisher with publish_publication permission is required."
        )
    if type(operation_id) is not uuid.UUID:
        raise ValidationError("Publication operation ID must be a UUID.")
    if type(expected_version) is not int or expected_version < 0:
        raise ValidationError("Expected publication version must be a non-negative integer.")

    _bootstrap_recent_publication_channel(operation_id)
    channel = PublicationChannel.objects.select_for_update().get(
        pk=PublicationRevision.Channel.RECENT_RETAIL
    )

    semantic_fields: dict[str, object] = {
        "channel_id": PublicationRevision.Channel.RECENT_RETAIL,
        "operation": operation,
        "sequence": expected_version + 1,
        "previous_revision_id": expected_current_revision_id,
        "target_revision_id": target_revision_id,
        "publisher_id": actor.pk,
        "reason_code": reason_code,
        "acceptance_evidence_sha256": acceptance_evidence_sha256,
    }
    existing = PublicationActivation.objects.select_for_update().filter(pk=operation_id).first()
    if existing is not None:
        if any(
            getattr(existing, field_name) != field_value
            for field_name, field_value in semantic_fields.items()
        ):
            raise ValidationError("Publication operation UUID conflicts with stored evidence.")
        _set_publication_transition_token(None)
        return existing, False

    if (
        channel.version != expected_version
        or channel.current_revision_id != expected_current_revision_id
    ):
        raise ValidationError("Publication expectation is stale.")
    if operation not in PublicationActivation.Operation.values:
        raise ValidationError("Publication operation is invalid.")
    if operation == PublicationActivation.Operation.WITHDRAW:
        if target_revision_id is not None or channel.current_revision_id is None:
            raise ValidationError("Withdrawal requires one current revision and no target.")
        target_revision = None
    else:
        if target_revision_id is None or target_revision_id == channel.current_revision_id:
            raise ValidationError("Publication transition requires a different target revision.")
        target_revision = (
            PublicationRevision.objects.select_for_update()
            .select_related("review_decision")
            .filter(pk=target_revision_id)
            .first()
        )
        if (
            target_revision is None
            or target_revision.channel != PublicationRevision.Channel.RECENT_RETAIL
            or target_revision.sealed_at is None
            or target_revision.review_decision.decision != ReviewDecision.Decision.APPROVE
            or ReviewDecision.objects.filter(
                supersedes_id=target_revision.review_decision_id
            ).exists()
        ):
            raise ValidationError("Publication target must be a sealed approved revision.")
        if (
            operation == PublicationActivation.Operation.ROLLBACK
            and not PublicationActivation.objects.filter(
                channel=channel,
                sequence__lte=expected_version,
                operation__in=(
                    PublicationActivation.Operation.ACTIVATE,
                    PublicationActivation.Operation.ROLLBACK,
                ),
                target_revision_id=target_revision.id,
            ).exists()
        ):
            raise ValidationError("Rollback target was not a previously current revision.")

    activation = PublicationActivation(
        id=operation_id,
        channel=channel,
        operation=operation,
        sequence=expected_version + 1,
        previous_revision_id=expected_current_revision_id,
        target_revision=target_revision,
        publisher_id=actor.pk,
        reason_code=reason_code,
        acceptance_evidence_sha256=acceptance_evidence_sha256,
    )
    activation._transition_write = True
    activation.save()
    updated = _update_recent_publication_pointer(
        expected_current_revision_id=expected_current_revision_id,
        expected_version=expected_version,
        target_revision_id=target_revision_id,
    )
    if updated != 1:
        raise ValidationError("Publication pointer transition did not affect exactly one channel.")
    _set_publication_transition_token(None)
    activation.refresh_from_db()
    return activation, True


@transaction.atomic
def seal_recent_publication(
    decision_id: uuid.UUID,
    public_copy_revision: str,
) -> PublicationRevision:
    """Seal the complete typed fact set approved by the current review tail."""

    from grocery.publication_facts import (  # Avoid a models/publication_facts import cycle.
        FACT_SET_HASH_VERSION,
        build_publication_fact_set,
    )

    decision = (
        ReviewDecision.objects.select_for_update()
        .select_related("source_configuration", "parse_run")
        .get(pk=decision_id)
    )
    generation_decisions = list(
        ReviewDecision.objects.select_for_update().filter(
            source_configuration_id=decision.source_configuration_id,
            source_artifact_id=decision.source_artifact_id,
            parse_run_id=decision.parse_run_id,
        )
    )
    tails = [
        candidate
        for candidate in generation_decisions
        if not any(other.supersedes_id == candidate.id for other in generation_decisions)
    ]
    if len(tails) != 1 or tails[0].id != decision.id:
        raise ValidationError("Publication requires the latest review decision tail.")
    if decision.decision != ReviewDecision.Decision.APPROVE:
        raise ValidationError("Publication requires a latest APPROVE review decision.")

    source_configuration = SourceConfiguration.objects.select_for_update().get(
        pk=decision.source_configuration_id
    )
    generation = ParseRun.objects.select_for_update().get(pk=decision.parse_run_id)
    if (
        decision.parse_run_id != generation.id
        or decision.approved_mode != SourceConfiguration.PublicationMode.RECENT_COMPARISON
        or source_configuration.publication_mode
        != SourceConfiguration.PublicationMode.RECENT_COMPARISON
        or decision.approved_mode != source_configuration.publication_mode
        or decision.approved_coverage_identity != source_configuration.coverage_identity
        or decision.approved_coverage_evidence_revision
        != source_configuration.coverage_evidence_revision
        or generation.status != ParseRun.Status.VALIDATED
        or not generation.parser_revision
    ):
        raise ValidationError(
            "Publication decision, generation, parser, mode, and coverage must match exactly."
        )

    snapshots = list(
        RetailPriceSnapshot.objects.select_for_update()
        .filter(parse_run=generation)
        .select_related("series")
        .prefetch_related("reference_prices__change_fact")
    )
    list(ReferencePrice.objects.select_for_update().filter(snapshot__parse_run=generation))
    list(
        PriceChangeFact.objects.select_for_update().filter(
            reference_price__snapshot__parse_run=generation
        )
    )
    if len(snapshots) != generation.accepted_row_count:
        raise ValidationError("Publication membership must equal the accepted generation count.")
    if any(
        snapshot.series.coverage_identity != decision.approved_coverage_identity
        for snapshot in snapshots
    ):
        raise ValidationError("Publication snapshot coverage must match the approved coverage.")

    fact_set = build_publication_fact_set(snapshots)
    semantic_fields: dict[str, object] = {
        "channel": PublicationRevision.Channel.RECENT_RETAIL,
        "mode": PublicationRevision.Mode.RECENT_COMPARISON,
        "review_decision_id": decision.id,
        "generation_id": generation.id,
        "parser_revision": generation.parser_revision,
        "fact_hash_version": FACT_SET_HASH_VERSION,
        "typed_fact_set_sha256": fact_set.typed_fact_set_sha256,
        "entry_count": len(fact_set.entries),
        "source_effective_date_min": fact_set.source_effective_date_min,
        "source_effective_date_max": fact_set.source_effective_date_max,
        "public_copy_revision": public_copy_revision,
    }
    candidate = PublicationRevision(**semantic_fields)
    candidate.full_clean(validate_unique=False, validate_constraints=False)

    existing = (
        PublicationRevision.objects.select_for_update()
        .filter(
            channel=PublicationRevision.Channel.RECENT_RETAIL,
            typed_fact_set_sha256=fact_set.typed_fact_set_sha256,
            public_copy_revision=public_copy_revision,
        )
        .first()
    )
    if existing is not None:
        if existing.sealed_at is None or any(
            getattr(existing, field_name) != field_value
            for field_name, field_value in semantic_fields.items()
        ):
            raise ValidationError("Publication replay conflicts with stored sealed evidence.")
        stored_entries = list(existing.entries.order_by("ordinal"))
        expected_entries = list(fact_set.entries)
        if len(stored_entries) != len(expected_entries) or any(
            (
                stored.ordinal,
                stored.snapshot_id,
                stored.fact_sha256,
            )
            != (
                expected.ordinal,
                expected.snapshot_id,
                expected.fact_sha256,
            )
            for stored, expected in zip(stored_entries, expected_entries, strict=True)
        ):
            raise ValidationError("Publication replay conflicts with stored membership.")
        return existing

    candidate.save()
    for entry in fact_set.entries:
        PublicationEntry.objects.create(
            revision=candidate,
            snapshot_id=entry.snapshot_id,
            ordinal=entry.ordinal,
            fact_sha256=entry.fact_sha256,
        )

    sealed_at = timezone.now()
    updated = PublicationRevision.objects.filter(
        pk=candidate.pk,
        sealed_at__isnull=True,
    ).update(sealed_at=sealed_at)
    if updated != 1:
        raise ValidationError("Publication seal transition did not affect exactly one revision.")
    candidate.refresh_from_db()
    return candidate


def ordered_page_manifest_sha256(receipts: Sequence[PageReceipt]) -> str:
    manifest = [receipt.body_sha256 for receipt in receipts]
    canonical = json.dumps(manifest, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


@transaction.atomic
def build_source_artifact(attempt_id: uuid.UUID) -> tuple[SourceArtifact, bool]:
    attempt = (
        FetchAttempt.objects.select_for_update()
        .select_related("source_configuration")
        .get(pk=attempt_id)
    )
    if attempt.state != FetchAttempt.State.SUCCEEDED:
        raise ValidationError("Only one completed, succeeded attempt can build an artifact.")

    receipts = list(
        PageReceipt.objects.select_for_update()
        .filter(fetch_attempt=attempt)
        .order_by("request_ordinal")
    )
    expected_ordinals = list(range(1, len(receipts) + 1))
    if not receipts:
        raise ValidationError("A source artifact requires at least one page receipt.")
    if [receipt.request_ordinal for receipt in receipts] != expected_ordinals:
        raise ValidationError("Page receipt request ordinals must be contiguous from one.")
    if [receipt.page_number for receipt in receipts] != expected_ordinals:
        raise ValidationError("Page receipt page numbers must be contiguous from one.")
    if any(
        receipt.body_state != PageReceipt.BodyState.RECEIVED
        or receipt.http_status != 200
        or receipt.provider_result_code != "0"
        or receipt.media_type != PageReceipt.MediaType.JSON
        or receipt.encoding != PageReceipt.Encoding.UTF_8
        for receipt in receipts
    ):
        raise ValidationError("Every artifact page must be a successful JSON UTF-8 receipt.")

    declared_totals = {receipt.declared_total_count for receipt in receipts}
    if None in declared_totals or len(declared_totals) != 1:
        raise ValidationError("Every page must declare the same total row count.")
    declared_total = next(iter(declared_totals))
    received_rows = sum(receipt.received_row_count for receipt in receipts)
    received_bytes = sum(receipt.body_byte_length for receipt in receipts)
    if received_rows != declared_total:
        raise ValidationError("Received rows do not reconcile with the declared total.")
    if (
        attempt.received_page_count != len(receipts)
        or attempt.received_row_count != received_rows
        or attempt.received_byte_count != received_bytes
    ):
        raise ValidationError("Attempt counters do not reconcile with its page receipts.")

    manifest_sha256 = ordered_page_manifest_sha256(receipts)
    source_identity = attempt.artifact_source_identity
    defaults: dict[str, Any] = {
        "page_count": len(receipts),
        "total_bytes": received_bytes,
        "media_type": SourceArtifact.MediaType.JSON,
        "encoding": SourceArtifact.Encoding.UTF_8,
        "retention_mode": SourceArtifact.RetentionMode.HASH_ONLY,
        "first_seen_at": attempt.completed_at,
    }
    artifact, created = SourceArtifact.objects.get_or_create(
        source_identity=source_identity,
        ordered_manifest_sha256=manifest_sha256,
        defaults=defaults,
    )
    expected_fields = ("page_count", "total_bytes", "media_type", "encoding", "retention_mode")
    if any(getattr(artifact, field_name) != defaults[field_name] for field_name in expected_fields):
        raise ValidationError("An existing artifact conflicts with the reconciled manifest.")
    if attempt.artifact_id not in (None, artifact.id):
        raise ValidationError("The attempt already references a different artifact.")
    if attempt.artifact_id is None:
        attempt.artifact = artifact
        attempt.save(update_fields=["artifact"])
    return artifact, created


# Load separately bounded vNext models into Django's app registry.
from grocery import historical_collection_models as _historical_collection_models  # noqa: E402,F401
from grocery import historical_daily_models as _historical_daily_models  # noqa: E402,F401
from grocery import historical_identity_models as _historical_identity_models  # noqa: E402,F401
from grocery import historical_monthly_models as _historical_monthly_models  # noqa: E402,F401
