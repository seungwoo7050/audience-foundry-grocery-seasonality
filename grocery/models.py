import re
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

SHA256_PATTERN = r"^[0-9a-f]{64}$"
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
    acquisition_run_id = models.UUIDField()
    attempt_ordinal = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=24, choices=State.choices, default=State.STARTED)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    redacted_request_shape = models.CharField(
        max_length=512,
        validators=[validate_redacted_request_shape],
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
        ]

    def __str__(self) -> str:
        return f"{self.acquisition_run_id}:{self.attempt_ordinal}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


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
