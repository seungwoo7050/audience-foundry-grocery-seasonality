from unittest.mock import patch

import pytest

from grocery.models import SourceConfiguration
from grocery.source.client import MAX_CALLS, MAX_PAGE_BYTES, MAX_PAGES
from grocery.source.configuration import (
    KAMIS_CONFIGURATION_REVISION,
    KAMIS_DATASET_ID,
    KAMIS_ENDPOINT_HOST,
    KAMIS_ENDPOINT_PATH,
    KAMIS_GATE_CONFIRMED_AT,
    KAMIS_LOGICAL_SECRET_NAME,
    KAMIS_RIGHTS_EVIDENCE_LOCATOR,
    KAMIS_SOURCE_CONFIGURATION_ID,
    SourceConfigurationDriftError,
    bootstrap_kamis_source_configuration,
)
from grocery.source.kamis import KAMIS_RETAIL_COVERAGE_IDENTITY
from grocery.source.registry import IDENTITY_EVIDENCE_REVISION, OFFICIAL_DOCS_ZIP_SHA256

pytestmark = pytest.mark.django_db


def test_bootstrap_seals_the_approved_a_path_contract_without_loading_a_secret() -> None:
    with patch("grocery.source.secrets.load_kamis_api_key") as secret_loader:
        source = bootstrap_kamis_source_configuration()

    secret_loader.assert_not_called()
    assert source.pk == KAMIS_SOURCE_CONFIGURATION_ID
    assert source.source_owner_name == "한국농수산식품유통공사"
    assert source.dataset_id == KAMIS_DATASET_ID
    assert source.configuration_revision == KAMIS_CONFIGURATION_REVISION
    assert source.interface_revision == "recent-price-v1"
    assert source.state == SourceConfiguration.State.ACTIVE
    assert source.state_changed_at == KAMIS_GATE_CONFIRMED_AT
    assert source.publication_mode == SourceConfiguration.PublicationMode.RECENT_COMPARISON
    assert source.coverage_identity == KAMIS_RETAIL_COVERAGE_IDENTITY
    assert source.coverage_evidence_revision == IDENTITY_EVIDENCE_REVISION
    assert source.endpoint_scheme == SourceConfiguration.EndpointScheme.HTTPS
    assert source.endpoint_host == KAMIS_ENDPOINT_HOST
    assert source.endpoint_path == KAMIS_ENDPOINT_PATH
    assert source.endpoint_method == SourceConfiguration.EndpointMethod.GET
    assert (
        source.authentication_mode == SourceConfiguration.AuthenticationMode.DATA_GO_KR_SERVICE_KEY
    )
    assert source.logical_secret_name == KAMIS_LOGICAL_SECRET_NAME
    assert source.provider_quota_limit == 10_000
    assert source.provider_quota_period == SourceConfiguration.QuotaPeriod.UNSPECIFIED
    assert source.request_timeout_seconds == 10
    assert source.retry_policy == SourceConfiguration.RetryPolicy.BOUNDED_TRANSIENT_ONLY
    assert source.max_retries == 2
    assert source.max_requests_per_attempt == MAX_CALLS == 12
    assert source.max_pages_per_attempt == MAX_PAGES == 12
    assert source.max_page_bytes == MAX_PAGE_BYTES == 4 * 1024 * 1024
    assert source.rights_evidence_locator == KAMIS_RIGHTS_EVIDENCE_LOCATOR
    assert source.rights_evidence_sha256 == OFFICIAL_DOCS_ZIP_SHA256
    assert source.rights_confirmed_at == KAMIS_GATE_CONFIRMED_AT
    assert source.raw_retention == SourceConfiguration.RawRetention.HASH_ONLY


def test_bootstrap_is_idempotent_for_the_same_sealed_revision() -> None:
    first = bootstrap_kamis_source_configuration()
    second = bootstrap_kamis_source_configuration()

    assert second.pk == first.pk
    assert SourceConfiguration.objects.count() == 1


def test_bootstrap_fails_closed_on_same_revision_field_drift_without_values() -> None:
    source = bootstrap_kamis_source_configuration()
    SourceConfiguration.objects.filter(pk=source.pk).update(endpoint_path="/changed/public/path")

    with pytest.raises(SourceConfigurationDriftError) as caught:
        bootstrap_kamis_source_configuration()

    assert caught.value.field_names == ("endpoint_path",)
    assert str(caught.value) == "source_configuration_drift fields=endpoint_path"
    assert "/changed/public/path" not in str(caught.value)


def test_bootstrap_does_not_modify_an_existing_drifted_revision() -> None:
    source = bootstrap_kamis_source_configuration()
    SourceConfiguration.objects.filter(pk=source.pk).update(state=SourceConfiguration.State.PAUSED)

    with pytest.raises(SourceConfigurationDriftError):
        bootstrap_kamis_source_configuration()

    source.refresh_from_db()
    assert source.state == SourceConfiguration.State.PAUSED
