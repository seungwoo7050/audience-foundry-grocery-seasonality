"""Deterministic database bootstrap for the approved KAMIS A-path source.

This module contains only public configuration metadata and the logical name of the
credential.  It deliberately does not import or invoke the credential loader.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from django.db import transaction

from grocery.models import SourceConfiguration
from grocery.source.client import (
    CONNECT_READ_TIMEOUT_SECONDS,
    KAMIS_ENDPOINT,
    MAX_ATTEMPTS_PER_PAGE,
    MAX_CALLS,
    MAX_PAGE_BYTES,
    MAX_PAGES,
)
from grocery.source.kamis import KAMIS_RETAIL_COVERAGE_IDENTITY
from grocery.source.registry import IDENTITY_EVIDENCE_REVISION, OFFICIAL_DOCS_ZIP_SHA256

KAMIS_DATASET_ID: Final = "15156063"
KAMIS_CONFIGURATION_REVISION: Final = "kamis-15156063-recent-comparison-v1"
KAMIS_INTERFACE_REVISION: Final = "recent-price-v1"
KAMIS_ENDPOINT_HOST: Final = "apis.data.go.kr"
KAMIS_ENDPOINT_PATH: Final = "/B552845/recent/price"
KAMIS_LOGICAL_SECRET_NAME: Final = "KAMIS_API_KEY"  # noqa: S105 - logical reference only
KAMIS_RIGHTS_EVIDENCE_LOCATOR: Final = "https://www.data.go.kr/data/15156063/openapi.do"
KAMIS_GATE_CONFIRMED_AT: Final = datetime(
    2026,
    8,
    30,
    tzinfo=ZoneInfo("Asia/Seoul"),
)
KAMIS_SOURCE_CONFIGURATION_ID: Final = uuid.uuid5(
    uuid.NAMESPACE_URL,
    f"{KAMIS_RIGHTS_EVIDENCE_LOCATOR}#{KAMIS_CONFIGURATION_REVISION}",
)

_EXPECTED_CONTRACT_FIELDS: Final = MappingProxyType(
    {
        "source_owner_name": "한국농수산식품유통공사",
        "dataset_id": KAMIS_DATASET_ID,
        "configuration_revision": KAMIS_CONFIGURATION_REVISION,
        "interface_revision": KAMIS_INTERFACE_REVISION,
        "state": SourceConfiguration.State.ACTIVE,
        "state_changed_at": KAMIS_GATE_CONFIRMED_AT,
        "publication_mode": SourceConfiguration.PublicationMode.RECENT_COMPARISON,
        "coverage_identity": KAMIS_RETAIL_COVERAGE_IDENTITY,
        "coverage_evidence_revision": IDENTITY_EVIDENCE_REVISION,
        "endpoint_scheme": SourceConfiguration.EndpointScheme.HTTPS,
        "endpoint_host": KAMIS_ENDPOINT_HOST,
        "endpoint_path": KAMIS_ENDPOINT_PATH,
        "endpoint_method": SourceConfiguration.EndpointMethod.GET,
        "authentication_mode": (SourceConfiguration.AuthenticationMode.DATA_GO_KR_SERVICE_KEY),
        "logical_secret_name": KAMIS_LOGICAL_SECRET_NAME,
        "provider_quota_limit": 10_000,
        "provider_quota_period": SourceConfiguration.QuotaPeriod.UNSPECIFIED,
        "request_timeout_seconds": int(CONNECT_READ_TIMEOUT_SECONDS),
        "retry_policy": SourceConfiguration.RetryPolicy.BOUNDED_TRANSIENT_ONLY,
        "max_retries": MAX_ATTEMPTS_PER_PAGE - 1,
        "max_requests_per_attempt": MAX_CALLS,
        "max_pages_per_attempt": MAX_PAGES,
        "max_page_bytes": MAX_PAGE_BYTES,
        "rights_evidence_locator": KAMIS_RIGHTS_EVIDENCE_LOCATOR,
        "rights_evidence_sha256": OFFICIAL_DOCS_ZIP_SHA256,
        "rights_confirmed_at": KAMIS_GATE_CONFIRMED_AT,
        "raw_retention": SourceConfiguration.RawRetention.HASH_ONLY,
    }
)


class SourceConfigurationDriftError(RuntimeError):
    """A same-revision row differs from the sealed, public-only contract."""

    def __init__(self, field_names: tuple[str, ...]) -> None:
        self.field_names = field_names
        super().__init__(f"source_configuration_drift fields={','.join(field_names)}")


def bootstrap_kamis_source_configuration() -> SourceConfiguration:
    """Create the approved A-path configuration once, or validate its exact revision.

    The comparison reports field names only.  No credential is loaded, compared, or
    included in an exception.
    """

    expected_endpoint = (
        f"{SourceConfiguration.EndpointScheme.HTTPS}://{KAMIS_ENDPOINT_HOST}{KAMIS_ENDPOINT_PATH}"
    )
    if KAMIS_ENDPOINT != expected_endpoint:
        raise SourceConfigurationDriftError(("transport_endpoint",))

    defaults = dict(_EXPECTED_CONTRACT_FIELDS)
    defaults.pop("dataset_id")
    defaults.pop("configuration_revision")
    defaults["id"] = KAMIS_SOURCE_CONFIGURATION_ID

    with transaction.atomic():
        source, _created = SourceConfiguration.objects.select_for_update().get_or_create(
            dataset_id=KAMIS_DATASET_ID,
            configuration_revision=KAMIS_CONFIGURATION_REVISION,
            defaults=defaults,
        )
        drifted_fields = tuple(
            sorted(
                field_name
                for field_name, expected_value in _EXPECTED_CONTRACT_FIELDS.items()
                if getattr(source, field_name) != expected_value
            )
        )
        if drifted_fields:
            raise SourceConfigurationDriftError(drifted_fields)

    return source
