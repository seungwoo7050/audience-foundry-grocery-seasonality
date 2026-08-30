from __future__ import annotations

import io
import json
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from email.message import Message
from itertools import pairwise
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from scripts.http_load_profile import (
    HTTP_5XX_RATE_LIMIT,
    LOGICAL_VIRTUAL_USERS,
    MAX_CONCURRENCY,
    MAX_RESPONSE_BYTES,
    NOMINAL_REQUEST_INTERVAL_MS,
    P95_LIMIT_MS,
    P95_SCHEDULE_JITTER_LIMIT_MS,
    PHASE0_DURATION_SECONDS,
    PROFILE_COMPLETION_GRACE_SECONDS,
    RECOVERY_FLOOR_INTERVAL_MS,
    REQUESTS_PER_SECOND,
    CompletedRequest,
    HttpObservation,
    LoadProfileConfig,
    LoadProfileError,
    RunMeasurements,
    _ActiveRequestCounter,
    _execute_scheduled_request,
    _NoRedirectHandler,
    _validate_local_url,
    build_report,
    http_request,
    main,
    request_plan,
    run_profile,
)

_DETAIL_ID = uuid.UUID("018f47d2-f9b2-7cc4-8ddf-fce39c000001")
_REVISION_TOKEN = "c" * 64


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.value += seconds


class OversleepOnceClock(FakeClock):
    def __init__(self) -> None:
        super().__init__()
        self._overslept = False

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        if not self._overslept:
            self.value += 0.2
            self._overslept = True


class SmallOversleepClock(FakeClock):
    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self.value += 0.002


class InlineExecutor(Executor):
    """Deterministic executor for pacing tests; concurrency has a separate test."""

    def submit[T](
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[T]:
        future: Future[T] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future


def inline_executor_factory(max_workers: int) -> Executor:
    assert max_workers == 1
    return InlineExecutor()


class RecordingSessionExecutor(InlineExecutor):
    def __init__(
        self,
        virtual_user_id: int,
        submission_order: list[int],
    ) -> None:
        self.virtual_user_id = virtual_user_id
        self.submission_order = submission_order
        self.submitted_ids: list[int] = []

    def submit[T](
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[T]:
        submitted_id = args[0]
        assert submitted_id == self.virtual_user_id
        self.submitted_ids.append(submitted_id)
        self.submission_order.append(submitted_id)
        return super().submit(fn, *args, **kwargs)


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int = 200,
        cache_control: str = "private, no-store",
        revision_token: str | None = _REVISION_TOKEN,
        body: bytes = b"ok",
    ) -> None:
        self._url = url
        self._status = status
        self._body = body
        self.headers = {
            "Cache-Control": cache_control,
            "X-Publication-Fact-Set": revision_token,
        }
        self.read_limit: int | None = None

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def getcode(self) -> int:
        return self._status

    def geturl(self) -> str:
        return self._url

    def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self._body[:limit]


def observation(
    *,
    latency_ms: float = 100.0,
    status_code: int = 200,
    valid: bool = True,
    revision_token: str | None = _REVISION_TOKEN,
) -> HttpObservation:
    return HttpObservation(
        latency_ms=latency_ms,
        status_code=status_code,
        valid=valid,
        revision_token=revision_token,
    )


def completed_requests(
    observations: list[HttpObservation],
) -> list[CompletedRequest]:
    return [
        CompletedRequest(
            kind="catalog" if index % 10 < 7 else "detail",
            virtual_user_id=index % LOGICAL_VIRTUAL_USERS,
            observation=value,
        )
        for index, value in enumerate(observations)
    ]


def run_measurements(
    *,
    elapsed_seconds: float,
    p95_schedule_jitter_ms: float = 0.0,
    max_schedule_jitter_ms: float = 0.0,
    minimum_inter_submission_ms: float = NOMINAL_REQUEST_INTERVAL_MS,
    burst_interval_violations: int = 0,
    observed_peak_active: int = 1,
) -> RunMeasurements:
    return RunMeasurements(
        elapsed_seconds=elapsed_seconds,
        p95_schedule_jitter_ms=p95_schedule_jitter_ms,
        max_schedule_jitter_ms=max_schedule_jitter_ms,
        minimum_inter_submission_ms=minimum_inter_submission_ms,
        burst_interval_violations=burst_interval_violations,
        observed_peak_active=observed_peak_active,
    )


def test_phase0_defaults_are_exact_and_smoke_is_explicitly_non_acceptance() -> None:
    phase0 = LoadProfileConfig(port=8000, detail_id=_DETAIL_ID)
    smoke = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        duration_seconds=1,
        profile="smoke",
    )

    assert phase0.duration_seconds == PHASE0_DURATION_SECONDS == 900
    assert phase0.scheduled_requests == 9_000
    assert REQUESTS_PER_SECOND == 10
    assert LOGICAL_VIRTUAL_USERS == MAX_CONCURRENCY == 20
    assert NOMINAL_REQUEST_INTERVAL_MS == 100.0
    assert RECOVERY_FLOOR_INTERVAL_MS == 90.0
    assert P95_SCHEDULE_JITTER_LIMIT_MS == 100.0
    assert PROFILE_COMPLETION_GRACE_SECONDS == 3.0
    assert phase0.label == "PHASE0_900S"
    assert smoke.label == "SMOKE_NON_ACCEPTANCE"
    with pytest.raises(LoadProfileError):
        LoadProfileConfig(
            port=8000,
            detail_id=_DETAIL_ID,
            duration_seconds=899,
            profile="phase0",
        )
    with pytest.raises(LoadProfileError):
        LoadProfileConfig(
            port=8000,
            detail_id=_DETAIL_ID,
            duration_seconds=900,
            profile="smoke",
        )


def test_workload_is_deterministic_seventy_thirty_and_strictly_loopback() -> None:
    config = LoadProfileConfig(port=8765, detail_id=_DETAIL_ID)
    first = [request_plan(config, index) for index in range(100)]
    second = [request_plan(config, index) for index in range(100)]

    assert first == second
    assert sum(plan.kind == "catalog" for plan in first) == 70
    assert sum(plan.kind == "detail" for plan in first) == 30
    assert [plan.kind for plan in first[:10]] == ["catalog"] * 7 + ["detail"] * 3
    for plan in first:
        parsed = urlsplit(plan.url)
        assert parsed.scheme == "http"
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port == 8765

    assert str(_DETAIL_ID) not in repr(config)
    assert "series/" not in repr(first[-1])


def test_smoke_runner_paces_ten_requests_and_emits_no_request_or_revision_values() -> None:
    config = LoadProfileConfig(
        port=8123,
        detail_id=_DETAIL_ID,
        duration_seconds=1,
        profile="smoke",
    )
    clock = FakeClock()
    seen_urls: list[str] = []
    lock = threading.Lock()

    def requester(url: str, port: int, timeout: float) -> HttpObservation:
        assert port == 8123
        assert timeout > 0
        with lock:
            seen_urls.append(url)
        return observation()

    report = run_profile(
        config,
        requester=requester,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        executor_factory=inline_executor_factory,
    )

    assert len(seen_urls) == 10
    assert report.completed_requests == 10
    assert report.catalog_list_search_requests == 7
    assert report.detail_requests == 3
    assert report.throughput_rps == 10.0
    assert report.elapsed_seconds == 1.0
    assert report.p95_schedule_jitter_ms == 0.0
    assert report.max_schedule_jitter_ms == 0.0
    assert report.minimum_inter_submission_ms == 100.0
    assert report.burst_interval_violations == 0
    assert report.logical_users_configured == 20
    assert report.logical_users_participated == 10
    assert report.logical_users_contract_met is True
    assert report.observed_peak_active == 1
    assert report.duration_contract_met is True
    assert report.throughput_target_met is True
    assert report.schedule_jitter_contract_met is True
    assert report.no_burst_contract_met is True
    assert report.schedule_contract_met is True
    assert report.concurrency_contract_met is True
    assert report.revision_consistent is True
    assert report.passed is True
    assert clock.value == 1.0
    timing = report.data()["timing"]
    assert isinstance(timing, dict)
    assert timing["nominal_request_interval_ms"] == 100.0
    assert timing["recovery_floor_interval_ms"] == 90.0
    logical_users = report.data()["logical_users"]
    assert logical_users == {
        "configured": 20,
        "participated": 10,
        "round_robin_contract_met": True,
    }
    concurrency = report.data()["concurrency"]
    assert concurrency == {
        "in_flight_limit": 20,
        "observed_in_flight_peak": 1,
        "in_flight_within_limit": True,
    }
    receipt = report.render()
    assert str(_DETAIL_ID) not in receipt
    assert _REVISION_TOKEN not in receipt
    assert "series/" not in receipt
    assert "category=" not in receipt
    assert "%" not in receipt
    assert "virtual_user_id" not in receipt


def test_phase0_runner_executes_exact_900_second_ten_rps_seventy_thirty_plan() -> None:
    config = LoadProfileConfig(port=8123, detail_id=_DETAIL_ID)
    clock = FakeClock()

    def requester(_url: str, _port: int, _timeout: float) -> HttpObservation:
        return observation()

    report = run_profile(
        config,
        requester=requester,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        executor_factory=inline_executor_factory,
    )

    assert report.elapsed_seconds == 900.0
    assert report.completed_requests == 9_000
    assert report.catalog_list_search_requests == 6_300
    assert report.detail_requests == 2_700
    assert report.throughput_rps == 10.0
    assert report.p95_schedule_jitter_ms == 0.0
    assert report.minimum_inter_submission_ms == 100.0
    assert report.burst_interval_violations == 0
    assert report.logical_users_configured == 20
    assert report.logical_users_participated == 20
    assert report.logical_users_contract_met is True
    assert report.workload_consistent is True
    assert report.passed is True


def test_twenty_fixed_logical_user_sessions_receive_requests_round_robin() -> None:
    config = LoadProfileConfig(
        port=8123,
        detail_id=_DETAIL_ID,
        duration_seconds=4,
        profile="smoke",
    )
    clock = FakeClock()
    sessions: list[RecordingSessionExecutor] = []
    submission_order: list[int] = []

    def session_executor_factory(max_workers: int) -> Executor:
        assert max_workers == 1
        session = RecordingSessionExecutor(len(sessions), submission_order)
        sessions.append(session)
        return session

    report = run_profile(
        config,
        requester=lambda _url, _port, _timeout: observation(),
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        executor_factory=session_executor_factory,
    )

    assert len(sessions) == LOGICAL_VIRTUAL_USERS
    assert submission_order == list(range(LOGICAL_VIRTUAL_USERS)) * 2
    assert [session.submitted_ids for session in sessions] == [
        [virtual_user_id, virtual_user_id] for virtual_user_id in range(LOGICAL_VIRTUAL_USERS)
    ]
    assert report.logical_users_configured == 20
    assert report.logical_users_participated == 20
    assert report.logical_users_contract_met is True
    assert report.observed_peak_active == 1
    assert report.passed is True


def test_phase0_elapsed_and_throughput_boundary_is_exactly_nine_hundred_to_nine_oh_three() -> None:
    config = LoadProfileConfig(port=8123, detail_id=_DETAIL_ID)
    requests = completed_requests([observation() for _index in range(9_000)])

    at_boundary = build_report(
        config,
        requests,
        measurements=run_measurements(elapsed_seconds=903.0),
    )
    beyond_boundary = build_report(
        config,
        requests,
        measurements=run_measurements(elapsed_seconds=903.001),
    )

    assert at_boundary.elapsed_seconds == 903.0
    assert at_boundary.throughput_rps == round(9_000 / 903, 3)
    assert at_boundary.minimum_accepted_throughput_rps == round(9_000 / 903, 3)
    assert at_boundary.duration_contract_met is True
    assert at_boundary.throughput_target_met is True
    assert at_boundary.passed is True
    assert beyond_boundary.duration_contract_met is False
    assert beyond_boundary.throughput_target_met is False
    assert beyond_boundary.passed is False


def test_end_to_end_latency_includes_schedule_queue_delay_through_completion() -> None:
    clock = FakeClock()
    clock.value = 0.3
    active_counter = _ActiveRequestCounter()

    def requester(_url: str, _port: int, _timeout: float) -> HttpObservation:
        clock.value += 0.05
        return observation(latency_ms=50.0)

    timed = _execute_scheduled_request(
        0,
        requester,
        "http://127.0.0.1:8000/",
        8000,
        2.0,
        0.1,
        clock.monotonic,
        active_counter,
    )

    assert timed.schedule_jitter_ms == pytest.approx(200.0)
    assert timed.observation.latency_ms == pytest.approx(250.0)
    assert active_counter.peak == 1


def test_one_scheduler_stall_recovers_gradually_without_a_catch_up_burst() -> None:
    config = LoadProfileConfig(port=8123, detail_id=_DETAIL_ID)
    clock = OversleepOnceClock()
    request_started_at: list[float] = []

    def requester(_url: str, _port: int, _timeout: float) -> HttpObservation:
        request_started_at.append(clock.monotonic())
        return observation(latency_ms=0.0)

    report = run_profile(
        config,
        requester=requester,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        executor_factory=inline_executor_factory,
    )

    assert len(request_started_at) == 9_000
    assert request_started_at[1] == pytest.approx(0.3)
    assert all(
        later - earlier == pytest.approx(0.09)
        for earlier, later in pairwise(request_started_at[1:22])
    )
    assert all(
        later - earlier == pytest.approx(0.1)
        for earlier, later in pairwise(request_started_at[21:])
    )
    assert report.elapsed_seconds == pytest.approx(900.0)
    assert report.max_schedule_jitter_ms == pytest.approx(200.0)
    assert report.p95_schedule_jitter_ms == 0.0
    assert report.minimum_inter_submission_ms == pytest.approx(90.0)
    assert report.burst_interval_violations == 0
    assert report.schedule_jitter_contract_met is True
    assert report.no_burst_contract_met is True
    assert report.schedule_contract_met is True
    assert report.passed is True


def test_small_repeated_clock_jitter_preserves_inter_submission_rate() -> None:
    config = LoadProfileConfig(
        port=8123,
        detail_id=_DETAIL_ID,
        duration_seconds=1,
        profile="smoke",
    )
    clock = SmallOversleepClock()

    report = run_profile(
        config,
        requester=lambda _url, _port, _timeout: observation(latency_ms=0.0),
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        executor_factory=inline_executor_factory,
    )

    assert report.p95_schedule_jitter_ms == pytest.approx(2.0)
    assert report.minimum_inter_submission_ms == pytest.approx(100.0)
    assert report.burst_interval_violations == 0
    assert report.no_burst_contract_met is True
    assert report.passed is True


def test_observed_in_flight_peak_is_measured_separately_and_bounded_at_twenty() -> None:
    active_counter = _ActiveRequestCounter()
    all_active = threading.Barrier(MAX_CONCURRENCY)

    def requester(_url: str, _port: int, _timeout: float) -> HttpObservation:
        all_active.wait(timeout=5.0)
        return observation(latency_ms=1.0)

    scheduled_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = [
            executor.submit(
                _execute_scheduled_request,
                virtual_user_id,
                requester,
                "http://127.0.0.1:8000/",
                8000,
                2.0,
                scheduled_at,
                time.monotonic,
                active_counter,
            )
            for virtual_user_id in range(MAX_CONCURRENCY)
        ]
        assert all(future.result().observation.valid for future in futures)

    assert active_counter.peak == MAX_CONCURRENCY == 20


def test_report_uses_nearest_rank_metrics_and_exact_release_thresholds() -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        duration_seconds=10,
        profile="smoke",
    )
    values = [observation(latency_ms=float(index)) for index in range(1, 101)]
    report = build_report(
        config,
        completed_requests(values),
        measurements=run_measurements(elapsed_seconds=10.0),
    )

    assert report.p50_ms == 50.0
    assert report.p95_ms == 95.0
    assert report.max_ms == 100.0
    assert report.http_5xx_rate == 0.0
    assert report.throughput_rps == 10.0
    assert report.passed is True
    assert P95_LIMIT_MS == 500.0
    assert HTTP_5XX_RATE_LIMIT == 0.005


def test_slow_drain_cannot_pass_elapsed_or_throughput_contract() -> None:
    config = LoadProfileConfig(port=8000, detail_id=_DETAIL_ID)
    values = [observation(latency_ms=100.0) for _index in range(8_551)] + [
        observation(latency_ms=100_000.0) for _index in range(449)
    ]
    report = build_report(
        config,
        completed_requests(values),
        measurements=run_measurements(
            elapsed_seconds=3_600.0,
            observed_peak_active=MAX_CONCURRENCY,
        ),
    )

    # A socket-level timeout cannot forcibly stop a trusted loopback slow-drip peer.
    # Once it returns, however, the bounded elapsed gate makes false acceptance impossible.
    assert report.p95_ms == 100.0
    assert report.throughput_rps == 2.5
    assert report.duration_contract_met is False
    assert report.throughput_target_met is False
    assert report.passed is False


def test_report_rejects_observed_concurrency_above_configured_bound() -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        duration_seconds=1,
        profile="smoke",
    )
    report = build_report(
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(
            elapsed_seconds=1.0,
            observed_peak_active=MAX_CONCURRENCY + 1,
        ),
    )

    assert report.concurrency_contract_met is False
    assert report.passed is False


def test_report_rejects_non_round_robin_logical_user_sequence() -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        duration_seconds=2,
        profile="smoke",
    )
    requests = completed_requests([observation() for _index in range(20)])
    first = requests[0]
    second = requests[1]
    requests[0] = CompletedRequest(
        kind=first.kind,
        virtual_user_id=second.virtual_user_id,
        observation=first.observation,
    )
    requests[1] = CompletedRequest(
        kind=second.kind,
        virtual_user_id=first.virtual_user_id,
        observation=second.observation,
    )

    report = build_report(
        config,
        requests,
        measurements=run_measurements(elapsed_seconds=2.0),
    )

    assert report.logical_users_configured == 20
    assert report.logical_users_participated == 20
    assert report.logical_users_contract_met is False
    assert report.passed is False


@pytest.mark.parametrize("failure", ("jitter", "burst"))
def test_report_rejects_persistent_schedule_jitter_or_catch_up_burst(failure: str) -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        duration_seconds=1,
        profile="smoke",
    )
    if failure == "jitter":
        measurements = run_measurements(
            elapsed_seconds=1.0,
            p95_schedule_jitter_ms=P95_SCHEDULE_JITTER_LIMIT_MS + 0.001,
        )
    else:
        measurements = run_measurements(
            elapsed_seconds=1.0,
            minimum_inter_submission_ms=RECOVERY_FLOOR_INTERVAL_MS - 0.001,
            burst_interval_violations=1,
        )

    report = build_report(
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=measurements,
    )

    assert report.schedule_contract_met is False
    assert report.passed is False
    if failure == "jitter":
        assert report.schedule_jitter_contract_met is False
    else:
        assert report.no_burst_contract_met is False


@pytest.mark.parametrize("failure", ("latency", "http_5xx", "revision_mix", "error"))
def test_report_fails_for_each_release_blocker(failure: str) -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        duration_seconds=1,
        profile="smoke",
    )
    values = [observation() for _index in range(10)]
    if failure == "latency":
        values[-1] = observation(latency_ms=501.0)
    elif failure == "http_5xx":
        values[-1] = observation(
            status_code=500,
            valid=False,
            revision_token=None,
        )
    elif failure == "revision_mix":
        values[-1] = observation(revision_token="d" * 64)
    else:
        values[-1] = observation(valid=False)

    report = build_report(
        config,
        completed_requests(values),
        measurements=run_measurements(elapsed_seconds=1.0),
    )

    assert report.passed is False
    if failure == "http_5xx":
        assert report.http_5xx_rate == 0.1
        assert report.error_count == 0
    if failure == "revision_mix":
        assert report.error_count == 0
        assert report.revision_consistent is False


def test_report_applies_strict_five_xx_rate_without_treating_it_as_runner_error() -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        duration_seconds=100,
        profile="smoke",
    )
    within_limit = [observation() for _index in range(996)] + [
        observation(status_code=500, valid=False, revision_token=None) for _index in range(4)
    ]
    at_limit = [observation() for _index in range(995)] + [
        observation(status_code=500, valid=False, revision_token=None) for _index in range(5)
    ]

    passing = build_report(
        config,
        completed_requests(within_limit),
        measurements=run_measurements(elapsed_seconds=100.0),
    )
    failing = build_report(
        config,
        completed_requests(at_limit),
        measurements=run_measurements(elapsed_seconds=100.0),
    )

    assert passing.http_5xx_rate == 0.004
    assert passing.error_count == 0
    assert passing.revision_consistent is True
    assert passing.passed is True
    assert failing.http_5xx_rate == HTTP_5XX_RATE_LIMIT
    assert failing.passed is False


def test_http_request_bounds_body_and_requires_status_cache_and_revision_header() -> None:
    url = "http://127.0.0.1:8000/"
    valid_response = FakeResponse(url=url)
    opener = MagicMock()
    opener.open.return_value = valid_response
    with (
        patch("scripts.http_load_profile._get_opener", return_value=opener),
        patch("scripts.http_load_profile.time.perf_counter", side_effect=(1.0, 1.1)),
    ):
        valid = http_request(url, 8000, 2.0)

    assert valid.valid is True
    assert valid.status_code == 200
    assert valid.revision_token == _REVISION_TOKEN
    assert valid_response.read_limit == MAX_RESPONSE_BYTES + 1
    request = opener.open.call_args.args[0]
    assert request.full_url == url
    assert opener.open.call_args.kwargs == {"timeout": 2.0}

    invalid_responses = (
        FakeResponse(url=url, status=204),
        FakeResponse(url=url, cache_control="public, max-age=60"),
        FakeResponse(url=url, revision_token=None),
        FakeResponse(url=url, revision_token="c" * 63),
        FakeResponse(url=url, revision_token="C" * 64),
        FakeResponse(url=url, revision_token="g" * 64),
        FakeResponse(url=url, revision_token=f" {_REVISION_TOKEN}"),
        FakeResponse(url=url, revision_token=f"{_REVISION_TOKEN} "),
        FakeResponse(url="http://127.0.0.1:8000/changed"),
        FakeResponse(url=url, body=b"x" * (MAX_RESPONSE_BYTES + 1)),
    )
    for response in invalid_responses:
        opener = MagicMock()
        opener.open.return_value = response
        with (
            patch("scripts.http_load_profile._get_opener", return_value=opener),
            patch("scripts.http_load_profile.time.perf_counter", side_effect=(2.0, 2.1)),
        ):
            result = http_request(url, 8000, 2.0)
        assert result.valid is False


def test_redirects_and_off_host_urls_fail_without_reflecting_location() -> None:
    external = "http://outside.example/private-path?serviceKey=secret-value"
    handler = _NoRedirectHandler()
    handler.redirect_request(MagicMock(), None, 302, "Found", {}, external)

    with pytest.raises(LoadProfileError) as caught:
        _validate_local_url(external, expected_port=8000)
    assert str(caught.value) == "local_url_invalid"
    assert "outside.example" not in repr(caught.value)
    assert "private-path" not in repr(caught.value)

    url = "http://127.0.0.1:8000/"
    headers = Message()
    headers["Location"] = external
    redirect = HTTPError(url, 302, "Found", headers, io.BytesIO())
    opener = MagicMock()
    opener.open.side_effect = redirect
    with (
        patch("scripts.http_load_profile._get_opener", return_value=opener),
        patch("scripts.http_load_profile.time.perf_counter", side_effect=(3.0, 3.1)),
    ):
        result = http_request(url, 8000, 2.0)

    assert result.status_code == 302
    assert result.valid is False
    assert result.revision_token is None
    assert not hasattr(result, "url")
    assert external not in repr(result)


def test_internal_revision_value_is_excluded_from_observation_repr() -> None:
    result = observation()

    assert _REVISION_TOKEN not in repr(result)


def test_main_prints_only_safe_json_and_uses_report_exit_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_args = [
        "--port",
        "8000",
        "--detail-id",
        str(_DETAIL_ID),
        "--profile",
        "smoke",
        "--duration-seconds",
        "1",
    ]
    report = build_report(
        LoadProfileConfig(
            port=8000,
            detail_id=_DETAIL_ID,
            profile="smoke",
            duration_seconds=1,
        ),
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(elapsed_seconds=1.0),
    )
    with patch("scripts.http_load_profile.run_profile", return_value=report):
        exit_code = main(config_args)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.count("\n") == 1
    assert json.loads(output) == report.data()
    assert str(_DETAIL_ID) not in output
    assert _REVISION_TOKEN not in output

    private_argument = "http://outside.example/private?serviceKey=secret-value"
    invalid_exit = main(["--port", private_argument, "--detail-id", str(_DETAIL_ID)])
    invalid_output = capsys.readouterr().out
    assert invalid_exit == 2
    assert json.loads(invalid_output) == {
        "error": "CONFIG_INVALID",
        "passed": False,
        "profile": "UNAVAILABLE",
    }
    assert private_argument not in invalid_output
