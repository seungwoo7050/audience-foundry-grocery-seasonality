import uuid

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import grocery.models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SourceConfiguration",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("source_owner_name", models.CharField(max_length=200)),
                ("dataset_id", models.CharField(max_length=64)),
                ("configuration_revision", models.CharField(max_length=64)),
                ("interface_revision", models.CharField(max_length=64)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("DRAFT", "Draft"),
                            ("RIGHTS_APPROVED", "Rights approved"),
                            ("ACTIVE", "Active"),
                            ("PAUSED", "Paused"),
                            ("REJECTED", "Rejected"),
                        ],
                        default="DRAFT",
                        max_length=32,
                    ),
                ),
                ("state_changed_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "publication_mode",
                    models.CharField(
                        choices=[
                            ("RECENT_COMPARISON", "Recent comparison"),
                            ("CURRENT_ONLY", "Current only"),
                            ("STATIC_MONTHLY_FILE", "Static monthly file"),
                        ],
                        max_length=32,
                    ),
                ),
                ("coverage_identity", models.CharField(max_length=128)),
                ("coverage_evidence_revision", models.CharField(max_length=64)),
                (
                    "endpoint_scheme",
                    models.CharField(choices=[("https", "HTTPS")], default="https", max_length=8),
                ),
                (
                    "endpoint_host",
                    models.CharField(
                        max_length=253, validators=[grocery.models.validate_endpoint_host]
                    ),
                ),
                (
                    "endpoint_path",
                    models.CharField(
                        max_length=512, validators=[grocery.models.validate_endpoint_path]
                    ),
                ),
                (
                    "endpoint_method",
                    models.CharField(choices=[("GET", "GET")], default="GET", max_length=8),
                ),
                (
                    "authentication_mode",
                    models.CharField(
                        choices=[
                            ("NONE", "None"),
                            ("DATA_GO_KR_SERVICE_KEY", "data.go.kr service key"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "logical_secret_name",
                    models.CharField(
                        blank=True,
                        max_length=128,
                        validators=[django.core.validators.RegexValidator("^[A-Z][A-Z0-9_]*$")],
                    ),
                ),
                ("provider_quota_limit", models.PositiveIntegerField()),
                (
                    "provider_quota_period",
                    models.CharField(
                        choices=[
                            ("UNSPECIFIED", "Provider did not specify"),
                            ("DAY", "Per day"),
                            ("SECOND", "Per second"),
                        ],
                        max_length=16,
                    ),
                ),
                ("request_timeout_seconds", models.PositiveSmallIntegerField()),
                (
                    "retry_policy",
                    models.CharField(
                        choices=[("BOUNDED_TRANSIENT_ONLY", "Bounded transient failures only")],
                        max_length=32,
                    ),
                ),
                ("max_retries", models.PositiveSmallIntegerField(default=0)),
                ("max_requests_per_attempt", models.PositiveSmallIntegerField()),
                ("max_pages_per_attempt", models.PositiveSmallIntegerField()),
                ("max_page_bytes", models.PositiveIntegerField()),
                ("rights_evidence_locator", models.URLField(blank=True, max_length=500)),
                (
                    "rights_evidence_sha256",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=64,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Enter a lowercase 64-character SHA-256 digest.",
                                regex="^[0-9a-f]{64}$",
                            )
                        ],
                    ),
                ),
                ("rights_confirmed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "raw_retention",
                    models.CharField(
                        choices=[("HASH_ONLY", "Hash only")], default="HASH_ONLY", max_length=16
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("dataset_id", "configuration_revision"),
                        name="grocery_source_dataset_revision_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "state__in",
                                ("DRAFT", "RIGHTS_APPROVED", "ACTIVE", "PAUSED", "REJECTED"),
                            )
                        ),
                        name="grocery_source_state_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "publication_mode__in",
                                ("RECENT_COMPARISON", "CURRENT_ONLY", "STATIC_MONTHLY_FILE"),
                            )
                        ),
                        name="grocery_source_publication_mode_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("endpoint_scheme", "https")),
                        name="grocery_source_endpoint_scheme_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("endpoint_host__regex", "^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
                        ),
                        name="grocery_source_endpoint_host_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("endpoint_path__startswith", "/"),
                            models.Q(("endpoint_path__contains", "?"), _negated=True),
                            models.Q(("endpoint_path__contains", "#"), _negated=True),
                        ),
                        name="grocery_source_endpoint_path_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("endpoint_method", "GET")),
                        name="grocery_source_endpoint_method_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("authentication_mode__in", ("NONE", "DATA_GO_KR_SERVICE_KEY"))
                        ),
                        name="grocery_source_auth_mode_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("authentication_mode", "NONE"), ("logical_secret_name", "")),
                            models.Q(
                                ("authentication_mode", "DATA_GO_KR_SERVICE_KEY"),
                                ("logical_secret_name__regex", "^[A-Z][A-Z0-9_]*$"),
                            ),
                            _connector="OR",
                        ),
                        name="grocery_source_secret_reference_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("provider_quota_limit__gt", 0)),
                        name="grocery_source_quota_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("provider_quota_period__in", ("UNSPECIFIED", "DAY", "SECOND"))
                        ),
                        name="grocery_source_quota_period_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("request_timeout_seconds__gt", 0)),
                        name="grocery_source_timeout_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("retry_policy", "BOUNDED_TRANSIENT_ONLY")),
                        name="grocery_source_retry_policy_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("max_retries__gte", 0)),
                        name="grocery_source_retries_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("max_requests_per_attempt__gt", 0)),
                        name="grocery_source_request_budget_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("max_pages_per_attempt__gt", 0)),
                        name="grocery_source_page_budget_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("max_page_bytes__gt", 0)),
                        name="grocery_source_byte_budget_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("rights_evidence_sha256", ""),
                            ("rights_evidence_sha256__regex", "^[0-9a-f]{64}$"),
                            _connector="OR",
                        ),
                        name="grocery_source_rights_hash_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("rights_confirmed_at__isnull", True),
                                ("rights_evidence_locator", ""),
                                ("rights_evidence_sha256", ""),
                            ),
                            models.Q(
                                models.Q(("rights_evidence_locator", ""), _negated=True),
                                models.Q(("rights_evidence_sha256", ""), _negated=True),
                                ("rights_confirmed_at__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="grocery_source_rights_evidence_complete",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("state__in", ("RIGHTS_APPROVED", "ACTIVE", "PAUSED")),
                                _negated=True,
                            ),
                            ("rights_confirmed_at__isnull", False),
                            _connector="OR",
                        ),
                        name="grocery_source_approved_rights_present",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("raw_retention", "HASH_ONLY")),
                        name="grocery_source_retention_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="FetchAttempt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("acquisition_run_id", models.UUIDField()),
                ("attempt_ordinal", models.PositiveSmallIntegerField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("STARTED", "Started"),
                            ("SUCCEEDED", "Succeeded"),
                            ("RETRYABLE_FAILED", "Retryable failed"),
                            ("TERMINAL_FAILED", "Terminal failed"),
                        ],
                        default="STARTED",
                        max_length=24,
                    ),
                ),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "redacted_request_shape",
                    models.CharField(
                        max_length=512, validators=[grocery.models.validate_redacted_request_shape]
                    ),
                ),
                ("received_page_count", models.PositiveIntegerField(default=0)),
                ("received_row_count", models.PositiveIntegerField(default=0)),
                ("received_byte_count", models.PositiveBigIntegerField(default=0)),
                (
                    "failure_class",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("TIMEOUT", "Timeout"),
                            ("NETWORK", "Network"),
                            ("HTTP_429", "HTTP 429"),
                            ("HTTP_5XX", "HTTP 5xx"),
                            ("PROVIDER_TRANSIENT", "Provider transient"),
                            ("AUTHENTICATION", "Authentication"),
                            ("INVALID_REQUEST", "Invalid request"),
                            ("RESPONSE_LIMIT", "Response limit"),
                            ("SCHEMA", "Schema"),
                            ("IDENTITY", "Identity"),
                            ("RECONCILIATION", "Reconciliation"),
                        ],
                        default="",
                        max_length=32,
                    ),
                ),
                ("failure_code", models.CharField(blank=True, max_length=64)),
                (
                    "source_configuration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fetch_attempts",
                        to="grocery.sourceconfiguration",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PageReceipt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("request_ordinal", models.PositiveSmallIntegerField()),
                ("page_number", models.PositiveIntegerField()),
                ("received_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("http_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("provider_result_code", models.CharField(blank=True, max_length=32)),
                ("declared_total_count", models.PositiveIntegerField(blank=True, null=True)),
                ("received_row_count", models.PositiveIntegerField(default=0)),
                (
                    "body_state",
                    models.CharField(
                        choices=[("RECEIVED", "Received"), ("NOT_RECEIVED", "Not received")],
                        max_length=16,
                    ),
                ),
                ("body_byte_length", models.PositiveIntegerField(default=0)),
                (
                    "body_sha256",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=64,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Enter a lowercase 64-character SHA-256 digest.",
                                regex="^[0-9a-f]{64}$",
                            )
                        ],
                    ),
                ),
                (
                    "body_absence_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("TIMEOUT", "Timeout"),
                            ("NETWORK", "Network"),
                            ("REJECTED_BEFORE_BODY", "Rejected before body"),
                        ],
                        default="",
                        max_length=32,
                    ),
                ),
                (
                    "media_type",
                    models.CharField(
                        blank=True,
                        choices=[("application/json", "JSON"), ("application/xml", "XML")],
                        default="",
                        max_length=32,
                    ),
                ),
                (
                    "encoding",
                    models.CharField(
                        blank=True, choices=[("utf-8", "UTF-8")], default="", max_length=16
                    ),
                ),
                (
                    "fetch_attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="page_receipts",
                        to="grocery.fetchattempt",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("fetch_attempt", "request_ordinal"),
                        name="grocery_page_attempt_ordinal_uniq",
                    ),
                    models.UniqueConstraint(
                        fields=("fetch_attempt", "page_number"),
                        name="grocery_page_attempt_number_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("request_ordinal__gt", 0)),
                        name="grocery_page_request_ordinal_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("page_number__gt", 0)),
                        name="grocery_page_number_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("http_status__isnull", True),
                            models.Q(("http_status__gte", 100), ("http_status__lte", 599)),
                            _connector="OR",
                        ),
                        name="grocery_page_http_status_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("declared_total_count__isnull", True),
                            ("declared_total_count__gte", 0),
                            _connector="OR",
                        ),
                        name="grocery_page_declared_total_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("received_row_count__gte", 0), ("body_byte_length__gte", 0)
                        ),
                        name="grocery_page_counts_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("body_state__in", ("RECEIVED", "NOT_RECEIVED"))),
                        name="grocery_page_body_state_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("body_absence_reason", ""),
                            (
                                "body_absence_reason__in",
                                ("TIMEOUT", "NETWORK", "REJECTED_BEFORE_BODY"),
                            ),
                            _connector="OR",
                        ),
                        name="grocery_page_absence_reason_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("media_type", ""),
                            ("media_type__in", ("application/json", "application/xml")),
                            _connector="OR",
                        ),
                        name="grocery_page_media_type_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("encoding", ""), ("encoding", "utf-8"), _connector="OR"
                        ),
                        name="grocery_page_encoding_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("body_sha256", ""),
                            ("body_sha256__regex", "^[0-9a-f]{64}$"),
                            _connector="OR",
                        ),
                        name="grocery_page_body_hash_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("body_absence_reason", ""),
                                ("body_state", "RECEIVED"),
                                ("http_status__isnull", False),
                                models.Q(("body_sha256", ""), _negated=True),
                                models.Q(("media_type", ""), _negated=True),
                                models.Q(("encoding", ""), _negated=True),
                            ),
                            models.Q(
                                ("body_byte_length", 0),
                                ("body_sha256", ""),
                                ("body_state", "NOT_RECEIVED"),
                                ("encoding", ""),
                                ("media_type", ""),
                                ("received_row_count", 0),
                                models.Q(("body_absence_reason", ""), _negated=True),
                            ),
                            _connector="OR",
                        ),
                        name="grocery_page_body_fields_valid",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="fetchattempt",
            constraint=models.UniqueConstraint(
                fields=("acquisition_run_id", "attempt_ordinal"),
                name="grocery_fetch_run_attempt_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="fetchattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(("attempt_ordinal__gt", 0)),
                name="grocery_fetch_attempt_ordinal_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="fetchattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("state__in", ("STARTED", "SUCCEEDED", "RETRYABLE_FAILED", "TERMINAL_FAILED"))
                ),
                name="grocery_fetch_state_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="fetchattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("received_page_count__gte", 0),
                    ("received_row_count__gte", 0),
                    ("received_byte_count__gte", 0),
                ),
                name="grocery_fetch_counts_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="fetchattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("failure_class", ""),
                    (
                        "failure_class__in",
                        (
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
                        ),
                    ),
                    _connector="OR",
                ),
                name="grocery_fetch_failure_class_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="fetchattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("completed_at__isnull", True),
                        ("failure_class", ""),
                        ("failure_code", ""),
                        ("state", "STARTED"),
                    ),
                    models.Q(
                        ("completed_at__isnull", False),
                        ("failure_class", ""),
                        ("failure_code", ""),
                        ("state", "SUCCEEDED"),
                    ),
                    models.Q(
                        ("state__in", ("RETRYABLE_FAILED", "TERMINAL_FAILED")),
                        ("completed_at__isnull", False),
                        models.Q(("failure_class", ""), _negated=True),
                    ),
                    _connector="OR",
                ),
                name="grocery_fetch_state_outcome_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="fetchattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("completed_at__isnull", True),
                    ("completed_at__gte", models.F("started_at")),
                    _connector="OR",
                ),
                name="grocery_fetch_time_order_valid",
            ),
        ),
    ]
