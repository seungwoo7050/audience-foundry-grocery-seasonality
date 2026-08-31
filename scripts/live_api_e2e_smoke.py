"""Opt-in, raw-free KAMIS live source-to-SSR smoke for a disposable database."""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Final
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from grocery.historical_activation_models import (
    HistoricalRetailPublicationActivation,
    HistoricalRetailPublicationChannel,
)
from grocery.historical_activations import transition_historical_publication
from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_daily_models import DailyMarketRetailPrice, DailyRegionalRetailPrice
from grocery.historical_identity_models import (
    HistoricalRetailSeriesKey,
    RetailMarketKey,
    RetailRegionKey,
    price_series_identity_sha256,
)
from grocery.historical_ingestion_workflow import (
    HistoricalIngestionOutcome,
    ingest_historical_collection,
)
from grocery.historical_monthly_models import MonthlyRegionalRetailPrice
from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.historical_publications import seal_historical_publication
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.historical_reviews import record_historical_review_decision
from grocery.management.local_phase0 import bootstrap_local_operator
from grocery.models import (
    FetchAttempt,
    ParseRun,
    PriceSeriesKey,
    PublicationActivation,
    PublicationChannel,
    PublicationRevision,
    ReviewDecision,
    SourceArtifact,
    SourceConfiguration,
    record_review_decision,
    seal_recent_publication,
    transition_recent_publication,
)
from grocery.source.client import (
    CONNECT_READ_TIMEOUT_SECONDS,
    MAX_ATTEMPTS_PER_PAGE,
    MAX_CALLS,
    MAX_PAGE_BYTES,
    MAX_PAGES,
    KamisFetchResult,
    KamisHttpClient,
)
from grocery.source.historical_contract import (
    HISTORICAL_ENDPOINT_CONTRACTS,
    HistoricalDataset,
    HistoricalPriceQuery,
)
from grocery.source.secrets import KAMIS_API_KEY, load_kamis_api_key
from grocery.vnext_presentation import format_provider_krw

_DATABASE_PREFIX: Final = "grocery_vnext_live_"
_PAGE_SIZE: Final = 1_000
_AUTO_MAPPING_REVISION: Final = "LIVE_SMOKE_AUTO_DERIVED_NOT_REVIEWED_V1"
_MANIFEST: Final = hashlib.sha256(b"local-live-api-e2e-smoke-manifest-v1").hexdigest()
_SAFE_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")
_CACHED_RESULT_MARKER: Final = "test-only-cached-live-result"


class LiveSmokeInvariantError(RuntimeError):
    """One fixed, value-free smoke invariant failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LiveSmokeFailure(RuntimeError):
    """A stage and safe code suitable for a raw-free operator receipt."""

    def __init__(self, stage: str, code: str) -> None:
        self.stage = stage
        self.code = code
        super().__init__(f"{stage}:{code}")


@dataclass(frozen=True, slots=True)
class LiveSmokeReceipt:
    recent_rows: int
    monthly_rows: int
    regional_rows: int
    market_rows: int

    def render(self) -> str:
        return " ".join(
            (
                "status=PASS",
                f"recent_rows={self.recent_rows}",
                f"monthly_rows={self.monthly_rows}",
                f"regional_rows={self.regional_rows}",
                f"market_rows={self.market_rows}",
                "ssr_routes=5",
                "source_calls_during_ssr=0",
                "raw_response_retained=no",
            )
        )


class CachedLiveClient:
    """Pass one already-fetched live result through the normal persistence workflow."""

    def __init__(
        self,
        dataset: HistoricalDataset,
        query: HistoricalPriceQuery,
        result: KamisFetchResult,
    ) -> None:
        self._dataset = dataset
        self._query = query
        self._result = result
        self._used = False

    def fetch_historical_prices(
        self,
        dataset: HistoricalDataset,
        service_key: str,
        *,
        query: HistoricalPriceQuery,
        page_size: int,
    ) -> KamisFetchResult:
        if (
            self._used
            or dataset != self._dataset
            or query != self._query
            or service_key != _CACHED_RESULT_MARKER
            or page_size != _PAGE_SIZE
        ):
            raise LiveSmokeInvariantError("cached_result_contract_invalid")
        self._used = True
        return self._result


def validate_disposable_environment(
    *,
    opt_in: object,
    debug: object,
    admin_enabled: object,
    qa_previews_enabled: object,
    control_plane_enabled: object,
    database: Mapping[str, object],
    occupied: bool,
) -> None:
    """Reject every environment that could be production or a shared local database."""

    name = database.get("NAME")
    host = database.get("HOST")
    port = database.get("PORT")
    if (
        opt_in != "1"
        or debug is not True
        or admin_enabled is not False
        or qa_previews_enabled is not False
        or control_plane_enabled is not False
        or database.get("ENGINE") != "django.db.backends.postgresql"
        or host not in {"127.0.0.1", "localhost", "::1"}
        or port not in {55434, "55434"}
        or not isinstance(name, str)
        or not name.startswith(_DATABASE_PREFIX)
        or occupied
    ):
        raise LiveSmokeInvariantError("disposable_environment_denied")


def safe_failure_code(error: BaseException) -> str:
    candidate = getattr(error, "code", None)
    if not isinstance(candidate, str) or _SAFE_CODE.fullmatch(candidate) is None:
        candidate = type(error).__name__
    return candidate if _SAFE_CODE.fullmatch(candidate) is not None else "internal_error"


def month_shift(value: date, offset: int) -> str:
    ordinal = value.year * 12 + value.month - 1 + offset
    year, month = divmod(ordinal, 12)
    return f"{year:04d}{month + 1:02d}"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise LiveSmokeInvariantError(code)


def _source_text(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise LiveSmokeInvariantError("live_dimension_invalid")
    return value


def _occupancy_models() -> tuple[type[Any], ...]:
    """Return mutable roots that must be empty before a disposable live run."""

    return (
        SourceConfiguration,
        SourceArtifact,
        PriceSeriesKey,
        PublicationRevision,
        PublicationChannel,
        HistoricalRetailSeriesKey,
        RetailRegionKey,
        RetailMarketKey,
        HistoricalSourceCollection,
        HistoricalRetailPublicationRevision,
        HistoricalRetailPublicationChannel,
        get_user_model(),
        Group,
    )


def _root_rows_exist() -> bool:
    return any(model.objects.exists() for model in _occupancy_models())


def _require_disposable_environment(opt_in_variable: str) -> None:
    validate_disposable_environment(
        opt_in=os.environ.get(opt_in_variable),
        debug=settings.DEBUG,
        admin_enabled=getattr(settings, "ADMIN_ENABLED", None),
        qa_previews_enabled=getattr(settings, "QA_STATE_PREVIEWS_ENABLED", None),
        control_plane_enabled=getattr(settings, "CONTROL_PLANE_OPERATIONS_ENABLED", None),
        database=dict(settings.DATABASES["default"]),
        occupied=_root_rows_exist(),
    )


def _publish_recent(parse_run: ParseRun, actor: Any) -> PublicationRevision:
    attempt = FetchAttempt.objects.select_related("source_configuration").get(
        artifact=parse_run.artifact,
        state=FetchAttempt.State.SUCCEEDED,
    )
    source = attempt.source_configuration
    decision, _created = record_review_decision(
        decision_id=uuid.uuid4(),
        actor=actor,
        decision=ReviewDecision.Decision.APPROVE,
        source_configuration_id=source.id,
        source_artifact_id=parse_run.artifact_id,
        parse_run_id=parse_run.id,
        reconciliation_report_sha256=parse_run.result_hash,
        acceptance_evidence_sha256=_digest("local-live-smoke-recent-acceptance"),
        reason_code="LOCAL_LIVE_SMOKE_AUTO_APPROVED",
        approved_mode=source.publication_mode,
        approved_coverage_identity=source.coverage_identity,
        approved_coverage_evidence_revision=source.coverage_evidence_revision,
    )
    revision = seal_recent_publication(decision.id, "ko-v4")
    transition_recent_publication(
        operation_id=uuid.uuid4(),
        actor=actor,
        operation=PublicationActivation.Operation.ACTIVATE,
        target_revision_id=revision.id,
        expected_current_revision_id=None,
        expected_version=0,
        reason_code="LOCAL_LIVE_SMOKE_RECENT_ACTIVATED",
        acceptance_evidence_sha256=_digest("local-live-smoke-recent-publication"),
    )
    return revision


def _exact_query(
    series: PriceSeriesKey,
    *,
    start: str,
    end: str,
    region_code: str | None,
) -> HistoricalPriceQuery:
    return HistoricalPriceQuery(
        start=start,
        end=end,
        category_code=series.category_code,
        item_code=series.item_code,
        variety_code=series.variety_code,
        grade_code=series.grade_code,
        region_code=region_code,
    )


def _historical_source_configuration(dataset: HistoricalDataset) -> SourceConfiguration:
    contract = HISTORICAL_ENDPOINT_CONTRACTS[dataset]
    endpoint = urlsplit(contract.endpoint)
    mode = {
        HistoricalDataset.MONTHLY: SourceConfiguration.PublicationMode.HISTORICAL_MONTHLY,
        HistoricalDataset.REGIONAL: SourceConfiguration.PublicationMode.HISTORICAL_REGIONAL,
        HistoricalDataset.MARKET: SourceConfiguration.PublicationMode.HISTORICAL_MARKET,
    }[dataset]
    rights_locator = f"https://www.data.go.kr/data/{dataset.value}/openapi.do"
    return SourceConfiguration.objects.create(
        source_owner_name="한국농수산식품유통공사",
        dataset_id=dataset.value,
        configuration_revision="local-live-api-e2e-smoke-v1",
        interface_revision="data-go-live-smoke-v1",
        state=SourceConfiguration.State.ACTIVE,
        state_changed_at=timezone.now(),
        publication_mode=mode,
        coverage_identity="LOCAL_LIVE_SMOKE_EXACT_SERIES_REGION_V1",
        coverage_evidence_revision=_AUTO_MAPPING_REVISION,
        endpoint_host=endpoint.hostname or "",
        endpoint_path=contract.path,
        authentication_mode=SourceConfiguration.AuthenticationMode.DATA_GO_KR_SERVICE_KEY,
        logical_secret_name=KAMIS_API_KEY,
        provider_quota_limit=10_000,
        provider_quota_period=SourceConfiguration.QuotaPeriod.UNSPECIFIED,
        request_timeout_seconds=int(CONNECT_READ_TIMEOUT_SECONDS),
        retry_policy=SourceConfiguration.RetryPolicy.BOUNDED_TRANSIENT_ONLY,
        schedule_execution_mode=SourceConfiguration.ScheduleExecutionMode.PLATFORM_SINGLETON,
        schedule_interval_hours=168 if dataset == HistoricalDataset.MONTHLY else 24,
        max_retries=MAX_ATTEMPTS_PER_PAGE - 1,
        max_requests_per_attempt=MAX_CALLS,
        max_pages_per_attempt=MAX_PAGES,
        max_page_bytes=MAX_PAGE_BYTES,
        rights_evidence_locator=rights_locator,
        rights_evidence_sha256=_digest(f"test-only-rights-locator:{rights_locator}"),
        rights_confirmed_at=timezone.now(),
        raw_retention=SourceConfiguration.RawRetention.HASH_ONLY,
    )


def _register_live_dimensions(
    series: PriceSeriesKey,
    results: tuple[KamisFetchResult, KamisFetchResult, KamisFetchResult],
) -> dict[str, RetailRegionKey]:
    HistoricalRetailSeriesKey.objects.create(
        recent_series=series,
        series_identity_sha256=price_series_identity_sha256(series),
        cross_source_evidence_revision=_AUTO_MAPPING_REVISION,
        code_manifest_sha256=_MANIFEST,
    )
    region_names: dict[str, str] = {}
    for result in results:
        for row in result.rows:
            code = _source_text(row, "sgg_cd")
            name = _source_text(row, "sgg_nm")
            _require(region_names.get(code, name) == name, "live_region_name_drift")
            region_names[code] = name
    regions = {
        code: RetailRegionKey.objects.create(
            region_code=code,
            region_name=name,
            identity_evidence_revision=_AUTO_MAPPING_REVISION,
        )
        for code, name in sorted(region_names.items())
    }
    market_names: dict[tuple[str, str], str] = {}
    for row in results[2].rows:
        key = (_source_text(row, "sgg_cd"), _source_text(row, "mrkt_cd"))
        name = _source_text(row, "mrkt_nm")
        _require(market_names.get(key, name) == name, "live_market_name_drift")
        market_names[key] = name
    _require(bool(market_names), "live_market_dimension_empty")
    for (region_code, market_code), market_name in sorted(market_names.items()):
        RetailMarketKey.objects.create(
            region=regions[region_code],
            market_code=market_code,
            market_name=market_name,
            identity_evidence_revision=_AUTO_MAPPING_REVISION,
        )
    return regions


def _ingest_historical(
    dataset: HistoricalDataset,
    query: HistoricalPriceQuery,
    result: KamisFetchResult,
) -> HistoricalIngestionOutcome:
    return ingest_historical_collection(
        collection_id=uuid.uuid4(),
        source_configuration_id=_historical_source_configuration(dataset).id,
        dataset=dataset,
        queries=(query,),
        code_manifest_sha256=_MANIFEST,
        service_key=_CACHED_RESULT_MARKER,
        client=CachedLiveClient(dataset, query, result),
        page_size=_PAGE_SIZE,
    )


def _publish_historical(
    outcomes: Mapping[HistoricalDataset, HistoricalIngestionOutcome],
    actor: Any,
) -> HistoricalRetailPublicationRevision:
    decisions: dict[HistoricalDataset, HistoricalCollectionReviewDecision] = {}
    for dataset, outcome in outcomes.items():
        collection = outcome.collection
        decision, _created = record_historical_review_decision(
            decision_id=uuid.uuid4(),
            actor=actor,
            collection_id=collection.id,
            decision=HistoricalCollectionReviewDecision.Decision.APPROVE,
            reconciliation_report_sha256=collection.result_sha256,
            acceptance_evidence_sha256=_digest(f"local-live-smoke-acceptance:{dataset.value}"),
            reason_code="LOCAL_LIVE_SMOKE_AUTO_APPROVED",
            approved_result_sha256=collection.result_sha256,
            approved_partition_manifest_sha256=collection.partition_manifest_sha256,
        )
        decisions[dataset] = decision
    revision = seal_historical_publication(
        monthly_review_id=decisions[HistoricalDataset.MONTHLY].id,
        regional_review_id=decisions[HistoricalDataset.REGIONAL].id,
        market_review_id=decisions[HistoricalDataset.MARKET].id,
        compatibility_report_sha256=_digest("local-live-smoke-compatibility"),
    )
    transition_historical_publication(
        operation_id=uuid.uuid4(),
        actor=actor,
        operation=HistoricalRetailPublicationActivation.Operation.ACTIVATE,
        target_revision_id=revision.id,
        expected_current_revision_id=None,
        expected_version=0,
        reason_code="LOCAL_LIVE_SMOKE_HISTORICAL_ACTIVATED",
        acceptance_evidence_sha256=_digest("local-live-smoke-historical-publication"),
    )
    return revision


def _verify_ssr(
    recent: PublicationRevision,
    historical: HistoricalRetailPublicationRevision,
    series: PriceSeriesKey,
    region: RetailRegionKey,
) -> None:
    monthly = MonthlyRegionalRetailPrice.objects.get(
        collection=historical.monthly_review.collection,
        series__recent_series=series,
        region=region,
        year_month=historical.month_max,
    )
    regional = DailyRegionalRetailPrice.objects.get(
        collection=historical.regional_review.collection,
        series=monthly.series,
        region=region,
        survey_date=historical.date_max,
    )
    market = (
        DailyMarketRetailPrice.objects.filter(
            collection=historical.market_review.collection,
            series=monthly.series,
            region=region,
            survey_date=historical.date_max,
        )
        .order_by("id")
        .first()
    )
    if market is None:
        raise LiveSmokeInvariantError("published_market_missing")
    client = Client()
    with (
        patch.object(
            KamisHttpClient,
            "fetch_recent_prices",
            side_effect=LiveSmokeInvariantError("ssr_source_call_forbidden"),
        ),
        patch.object(
            KamisHttpClient,
            "fetch_historical_prices",
            side_effect=LiveSmokeInvariantError("ssr_source_call_forbidden"),
        ),
    ):
        responses = (
            client.get(reverse("grocery:catalog")),
            client.get(reverse("grocery:detail", kwargs={"series_id": series.id})),
            client.get(
                reverse("grocery:history", kwargs={"series_id": series.id}),
                {"region": str(region.id), "range": "36"},
            ),
            client.get(
                reverse("grocery:regions", kwargs={"series_id": series.id}),
                {"date": historical.date_max.isoformat()},
            ),
            client.get(
                reverse(
                    "grocery:markets",
                    kwargs={"series_id": series.id, "region_id": region.id},
                ),
                {"date": historical.date_max.isoformat()},
            ),
        )
    _require(all(response.status_code == 200 for response in responses), "ssr_status_invalid")
    _require(
        all(response.headers.get("Cache-Control") == "no-store" for response in responses),
        "ssr_cache_policy_invalid",
    )
    _require(
        all(
            response.headers.get("X-Publication-Fact-Set") == recent.typed_fact_set_sha256
            for response in responses
        ),
        "ssr_recent_fact_set_invalid",
    )
    _require(
        all(
            response.headers.get("X-Historical-Publication-Fact-Set")
            == historical.typed_fact_set_sha256
            for response in responses[2:]
        ),
        "ssr_historical_fact_set_invalid",
    )
    _require(series.item_name.encode() in responses[0].content, "ssr_catalog_value_missing")
    _require(series.item_name.encode() in responses[1].content, "ssr_detail_value_missing")
    _require(
        format_provider_krw(monthly.provider_mean).encode() in responses[2].content,
        "ssr_monthly_value_missing",
    )
    _require(
        format_provider_krw(regional.provider_mean).encode() in responses[3].content,
        "ssr_regional_value_missing",
    )
    _require(
        format_provider_krw(market.provider_price).encode() in responses[4].content,
        "ssr_market_value_missing",
    )
    _require(
        all(b"<script" not in response.content.lower() for response in responses),
        "ssr_script_present",
    )


def _execute_live_flow() -> LiveSmokeReceipt:
    stage = "LOCAL_OPERATOR"
    try:
        actor, _created = bootstrap_local_operator()

        stage = "RECENT_INGESTION"
        command_output = io.StringIO()
        try:
            call_command("ingest_kamis_recent", page_size=_PAGE_SIZE, stdout=command_output)
        finally:
            command_output.close()
        parse_run = ParseRun.objects.get(status=ParseRun.Status.VALIDATED)
        recent = _publish_recent(parse_run, actor)
        entry = (
            recent.entries.select_related("snapshot__series")
            .order_by(
                "snapshot__series__category_code",
                "snapshot__series__item_name",
                "snapshot__series__item_code",
                "snapshot__series__variety_code",
                "snapshot__series__grade_code",
                "snapshot__series__raw_unit",
                "snapshot__series__raw_unit_size",
                "snapshot__series_id",
            )
            .first()
        )
        if entry is None:
            raise LiveSmokeInvariantError("recent_entry_missing")
        series = entry.snapshot.series
        survey_date = entry.snapshot.source_effective_date

        stage = "HISTORICAL_FETCH"
        secret = load_kamis_api_key()
        try:
            source_client = KamisHttpClient()
            discovery_query = _exact_query(
                series,
                start=month_shift(survey_date, 0),
                end=month_shift(survey_date, 0),
                region_code=None,
            )
            discovery = source_client.fetch_historical_prices(
                HistoricalDataset.MONTHLY,
                secret.reveal(),
                query=discovery_query,
                page_size=_PAGE_SIZE,
            )
            _require(bool(discovery.rows), "monthly_discovery_empty")
            region_code = min(_source_text(row, "sgg_cd") for row in discovery.rows)
            monthly_query = _exact_query(
                series,
                start=month_shift(survey_date, -35),
                end=month_shift(survey_date, 0),
                region_code=region_code,
            )
            day = survey_date.strftime("%Y%m%d")
            regional_query = _exact_query(series, start=day, end=day, region_code=region_code)
            market_query = _exact_query(series, start=day, end=day, region_code=region_code)
            monthly_result = source_client.fetch_historical_prices(
                HistoricalDataset.MONTHLY,
                secret.reveal(),
                query=monthly_query,
                page_size=_PAGE_SIZE,
            )
            regional_result = source_client.fetch_historical_prices(
                HistoricalDataset.REGIONAL,
                secret.reveal(),
                query=regional_query,
                page_size=_PAGE_SIZE,
            )
            market_result = source_client.fetch_historical_prices(
                HistoricalDataset.MARKET,
                secret.reveal(),
                query=market_query,
                page_size=_PAGE_SIZE,
            )
        finally:
            del secret
        _require(
            bool(monthly_result.rows and regional_result.rows and market_result.rows),
            "historical_live_dataset_empty",
        )
        del discovery

        stage = "TEST_ONLY_MAPPING"
        results = (monthly_result, regional_result, market_result)
        regions = _register_live_dimensions(series, results)
        _require(region_code in regions, "selected_region_missing")

        stage = "TYPED_PERSISTENCE"
        outcomes = {
            HistoricalDataset.MONTHLY: _ingest_historical(
                HistoricalDataset.MONTHLY, monthly_query, monthly_result
            ),
            HistoricalDataset.REGIONAL: _ingest_historical(
                HistoricalDataset.REGIONAL, regional_query, regional_result
            ),
            HistoricalDataset.MARKET: _ingest_historical(
                HistoricalDataset.MARKET, market_query, market_result
            ),
        }
        del monthly_result, regional_result, market_result

        stage = "TEST_ONLY_PUBLICATION"
        historical = _publish_historical(outcomes, actor)

        stage = "SSR"
        _verify_ssr(recent, historical, series, regions[region_code])
        return LiveSmokeReceipt(
            recent_rows=parse_run.accepted_row_count,
            monthly_rows=outcomes[HistoricalDataset.MONTHLY].accepted_row_count,
            regional_rows=outcomes[HistoricalDataset.REGIONAL].accepted_row_count,
            market_rows=outcomes[HistoricalDataset.MARKET].accepted_row_count,
        )
    except Exception as error:
        if isinstance(error, LiveSmokeFailure):
            raise
        raise LiveSmokeFailure(stage, safe_failure_code(error)) from None


def run_live_api_e2e_smoke() -> LiveSmokeReceipt:
    try:
        _require_disposable_environment("LIVE_SOURCE_E2E_SMOKE")
    except Exception as error:
        raise LiveSmokeFailure("ENVIRONMENT", safe_failure_code(error)) from None
    try:
        with transaction.atomic():
            receipt = _execute_live_flow()
            transaction.set_rollback(True)
    except LiveSmokeFailure:
        raise
    except Exception as error:
        raise LiveSmokeFailure("ROLLBACK", safe_failure_code(error)) from None
    try:
        _require(not _root_rows_exist(), "rollback_verification_failed")
    except Exception as error:
        raise LiveSmokeFailure("ROLLBACK", safe_failure_code(error)) from None
    return receipt


def run_live_browser_fixture() -> LiveSmokeReceipt:
    """Commit normalized, test-published live data to an exact disposable database."""

    try:
        _require_disposable_environment("LIVE_SOURCE_BROWSER_FIXTURE")
    except Exception as error:
        raise LiveSmokeFailure("ENVIRONMENT", safe_failure_code(error)) from None
    try:
        with transaction.atomic():
            receipt = _execute_live_flow()
            _require(receipt.recent_rows >= 2, "fixture_selection_candidate_missing")
            _require(_root_rows_exist(), "fixture_persistence_missing")
    except LiveSmokeFailure:
        raise
    except Exception as error:
        raise LiveSmokeFailure("FIXTURE", safe_failure_code(error)) from None
    return receipt
