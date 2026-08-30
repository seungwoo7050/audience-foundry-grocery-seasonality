from __future__ import annotations

import io
import json
import runpy
import signal
import subprocess
import sys
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
    _CHILD_BOOTSTRAP,
    CLI_TERMINATION_GRACE_SECONDS,
    CLI_WATCHDOG_GRACE_SECONDS,
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
    _ChildOutputLimitError,
    _collect_child_output,
    _entrypoint,
    _execute_scheduled_request,
    _NoRedirectHandler,
    _terminate_child,
    _validate_local_url,
    _validated_child_output,
    build_report,
    http_request,
    main,
    request_plan,
    run_profile,
    supervised_main,
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
    assert CLI_WATCHDOG_GRACE_SECONDS == 5.0
    assert CLI_TERMINATION_GRACE_SECONDS == 1.0
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


def test_supervised_cli_reprints_only_a_validated_child_receipt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        profile="smoke",
        duration_seconds=1,
    )
    arguments = [
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
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(elapsed_seconds=1.0),
    )
    child = MagicMock()

    with (
        patch("scripts.http_load_profile.subprocess.Popen", return_value=child) as popen,
        patch(
            "scripts.http_load_profile._collect_child_output",
            return_value=(f"{report.render()}\n", 0),
        ) as collect_child_output,
    ):
        exit_code = supervised_main(arguments)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.count("\n") == 1
    assert json.loads(output) == report.data()
    assert str(_DETAIL_ID) not in output
    assert _REVISION_TOKEN not in output
    command = popen.call_args.args[0]
    assert str(_DETAIL_ID) not in " ".join(command)
    assert popen.call_args.kwargs["stderr"] is subprocess.DEVNULL
    assert popen.call_args.kwargs["start_new_session"] is True
    assert set(popen.call_args.kwargs["env"]) == {
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
        "PYTHONUTF8",
    }
    assert collect_child_output.call_args.kwargs["timeout_seconds"] == pytest.approx(
        1.0 + CLI_WATCHDOG_GRACE_SECONDS
    )


def test_supervised_cli_kills_stubborn_child_and_redacts_timeout_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "--port",
        "8000",
        "--detail-id",
        str(_DETAIL_ID),
        "--profile",
        "smoke",
        "--duration-seconds",
        "1",
    ]
    private_output = f"private={_DETAIL_ID};revision={_REVISION_TOKEN}"
    child = MagicMock()
    child.pid = 4_321
    timeout = subprocess.TimeoutExpired(
        cmd=("private-command", str(_DETAIL_ID)),
        timeout=1.0 + CLI_WATCHDOG_GRACE_SECONDS,
        output=private_output,
    )

    with (
        patch("scripts.http_load_profile.subprocess.Popen", return_value=child),
        patch("scripts.http_load_profile._collect_child_output", side_effect=timeout),
        patch("scripts.http_load_profile._terminate_child") as terminate_child,
    ):
        exit_code = supervised_main(arguments)

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output) == {
        "error": "WATCHDOG_TIMEOUT",
        "passed": False,
        "profile": "SMOKE_NON_ACCEPTANCE",
    }
    assert output.count("\n") == 1
    assert str(_DETAIL_ID) not in output
    assert _REVISION_TOKEN not in output
    assert "private-command" not in output
    terminate_child.assert_called_once_with(child)


def test_supervised_cli_redacts_process_creation_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "--port",
        "8000",
        "--detail-id",
        str(_DETAIL_ID),
        "--profile",
        "smoke",
        "--duration-seconds",
        "1",
    ]
    private_error = OSError(f"failed argument {str(_DETAIL_ID)} {_REVISION_TOKEN}")

    with patch("scripts.http_load_profile.subprocess.Popen", side_effect=private_error):
        exit_code = supervised_main(arguments)

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output) == {
        "error": "SUPERVISOR_FAILED",
        "passed": False,
        "profile": "SMOKE_NON_ACCEPTANCE",
    }
    assert output.count("\n") == 1
    assert str(_DETAIL_ID) not in output
    assert _REVISION_TOKEN not in output


def test_supervised_cli_redacts_unexpected_validator_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "--port",
        "8000",
        "--detail-id",
        str(_DETAIL_ID),
        "--profile",
        "smoke",
        "--duration-seconds",
        "1",
    ]
    private_error = RuntimeError(f"validator failed {_DETAIL_ID} {_REVISION_TOKEN}")

    with (
        patch("scripts.http_load_profile.subprocess.Popen", return_value=MagicMock()),
        patch(
            "scripts.http_load_profile._collect_child_output",
            return_value=("{}\n", 0),
        ),
        patch(
            "scripts.http_load_profile._validated_child_output",
            side_effect=private_error,
        ),
    ):
        exit_code = supervised_main(arguments)

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output) == {
        "error": "CHILD_RESULT_INVALID",
        "passed": False,
        "profile": "SMOKE_NON_ACCEPTANCE",
    }
    assert str(_DETAIL_ID) not in output
    assert _REVISION_TOKEN not in output


@pytest.mark.parametrize(
    "child_output",
    (
        '{"passed":true,"private":"http://127.0.0.1:8000/?token=value"}\n',
        '{}\n{"detail_id":"018f47d2-f9b2-7cc4-8ddf-fce39c000001"}\n',
        "민감한 오류\n",
    ),
)
def test_supervised_cli_rejects_unvalidated_child_output_without_reflection(
    child_output: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "--port",
        "8000",
        "--detail-id",
        str(_DETAIL_ID),
        "--profile",
        "smoke",
        "--duration-seconds",
        "1",
    ]
    child = MagicMock()

    with (
        patch("scripts.http_load_profile.subprocess.Popen", return_value=child),
        patch(
            "scripts.http_load_profile._collect_child_output",
            return_value=(child_output, 0),
        ),
    ):
        exit_code = supervised_main(arguments)

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output) == {
        "error": "CHILD_RESULT_INVALID",
        "passed": False,
        "profile": "SMOKE_NON_ACCEPTANCE",
    }
    assert output.count("\n") == 1
    assert "token=value" not in output
    assert str(_DETAIL_ID) not in output
    assert "민감한 오류" not in output


def test_parent_recomputes_derived_gates_before_accepting_child_receipt() -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        profile="smoke",
        duration_seconds=1,
    )
    report = build_report(
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(elapsed_seconds=1.0),
    )
    variants: list[dict[str, object]] = []
    for mutation in ("throughput", "counts", "latency"):
        payload = json.loads(report.render())
        if mutation == "throughput":
            payload["timing"]["throughput_rps"] = 0.0
        elif mutation == "counts":
            payload["counts"].update(
                {
                    "completed": 0,
                    "catalog_list_search": 0,
                    "detail": 0,
                    "successful": 0,
                }
            )
            payload["timing"]["throughput_rps"] = 0.0
        else:
            payload["latency_ms"]["p95"] = P95_LIMIT_MS + 1.0
            payload["latency_ms"]["max"] = P95_LIMIT_MS + 1.0
        variants.append(payload)

    for payload in variants:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert (
            _validated_child_output(
                f"{encoded}\n",
                return_code=0,
                config=config,
            )
            is None
        )


def test_parent_accepts_genuine_receipts_across_three_decimal_rounding_boundaries() -> None:
    config = LoadProfileConfig(
        port=8000,
        detail_id=_DETAIL_ID,
        profile="smoke",
        duration_seconds=1,
    )
    passing_after_small_overrun = build_report(
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(elapsed_seconds=1.0002),
    )
    passing_with_distinct_rounded_throughput = build_report(
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(elapsed_seconds=1.0038),
    )
    failing_after_completion_boundary = build_report(
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(elapsed_seconds=4.0004),
    )
    failing_after_jitter_boundary = build_report(
        config,
        completed_requests([observation() for _index in range(10)]),
        measurements=run_measurements(
            elapsed_seconds=1.0,
            p95_schedule_jitter_ms=P95_SCHEDULE_JITTER_LIMIT_MS + 0.0004,
            max_schedule_jitter_ms=P95_SCHEDULE_JITTER_LIMIT_MS + 0.0004,
        ),
    )
    failing_after_latency_boundary = build_report(
        config,
        completed_requests(
            [observation() for _index in range(9)] + [observation(latency_ms=P95_LIMIT_MS + 0.0004)]
        ),
        measurements=run_measurements(elapsed_seconds=1.0),
    )

    assert passing_after_small_overrun.elapsed_seconds == 1.0
    assert passing_after_small_overrun.throughput_rps == 9.998
    assert passing_after_small_overrun.passed is True
    assert passing_with_distinct_rounded_throughput.elapsed_seconds == 1.004
    assert passing_with_distinct_rounded_throughput.throughput_rps == 9.962
    assert passing_with_distinct_rounded_throughput.passed is True
    cases = (
        (passing_after_small_overrun, 0),
        (passing_with_distinct_rounded_throughput, 0),
        (failing_after_completion_boundary, 1),
        (failing_after_jitter_boundary, 1),
        (failing_after_latency_boundary, 1),
    )
    for report, return_code in cases:
        assert (
            _validated_child_output(
                f"{report.render()}\n",
                return_code=return_code,
                config=config,
            )
            == report.render()
        )


def test_streaming_child_output_enforces_hard_byte_cap_before_buffering_all() -> None:
    process = subprocess.Popen(  # noqa: S603 - fixed local test interpreter command.
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 40000)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        start_new_session=True,
    )
    try:
        with pytest.raises(_ChildOutputLimitError):
            _collect_child_output(
                process,
                encoded_arguments=b"[]",
                timeout_seconds=2.0,
            )
    finally:
        _terminate_child(process)


def test_streaming_child_output_enforces_deadline_without_waiting_for_eof() -> None:
    process = MagicMock()
    process.stdin = MagicMock()
    process.stdout = MagicMock()
    clock = FakeClock()
    selector = MagicMock()
    selector.__enter__.return_value = selector

    def expire_selection(timeout: float) -> list[object]:
        clock.sleep(timeout)
        return []

    selector.select.side_effect = expire_selection
    with patch("scripts.http_load_profile.selectors.DefaultSelector", return_value=selector):
        with pytest.raises(subprocess.TimeoutExpired):
            _collect_child_output(
                process,
                encoded_arguments=b"[]",
                timeout_seconds=0.05,
                monotonic=clock.monotonic,
            )

    assert clock.value == pytest.approx(0.05)
    process.stdin.write.assert_called_once_with(b"[]")
    process.stdin.close.assert_called_once_with()
    process.stdout.close.assert_called_once_with()


def test_termination_kills_surviving_process_group_even_when_leader_exited() -> None:
    process = MagicMock()
    process.pid = 4_321
    process.stdin = None
    process.stdout = None
    process.poll.return_value = 0
    process.wait.return_value = 0
    clock = FakeClock()

    with (
        patch("scripts.http_load_profile._process_group_exists", return_value=True),
        patch("scripts.http_load_profile._signal_process_group") as signal_group,
    ):
        _terminate_child(
            process,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

    assert signal_group.call_count == 2
    signal_group.assert_any_call(process, signal.SIGTERM)
    signal_group.assert_any_call(process, signal.SIGKILL)
    assert clock.value == pytest.approx(CLI_TERMINATION_GRACE_SECONDS)


def test_entrypoint_routes_external_cli_to_supervisor() -> None:
    external_arguments = ["--port", "invalid-private-value"]
    with patch("scripts.http_load_profile.supervised_main", return_value=7) as supervisor:
        assert _entrypoint(external_arguments) == 7
        supervisor.assert_called_once_with(external_arguments)


def test_real_child_bootstrap_reads_stdin_without_traceback_or_argument_reflection() -> None:
    script_path = str(__file__).replace("tests/test_http_load_profile.py", "http_load_profile.py")
    result = subprocess.run(  # noqa: S603 - fixed local test interpreter/script command.
        [sys.executable, "-c", _CHILD_BOOTSTRAP, script_path],
        input=b"[]",
        capture_output=True,
        timeout=2.0,
        check=False,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "error": "CONFIG_INVALID",
        "passed": False,
        "profile": "UNAVAILABLE",
    }
    assert result.stdout.count(b"\n") == 1
    assert result.stderr == b""


def test_real_main_module_dispatches_external_cli_through_supervisor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_path = str(__file__).replace("tests/test_http_load_profile.py", "http_load_profile.py")
    arguments = [
        script_path,
        "--port",
        "8000",
        "--detail-id",
        str(_DETAIL_ID),
        "--profile",
        "smoke",
        "--duration-seconds",
        "1",
    ]
    private_error = OSError(f"process failed {_DETAIL_ID} {_REVISION_TOKEN}")

    with (
        patch.object(sys, "argv", arguments),
        patch("subprocess.Popen", side_effect=private_error),
        pytest.raises(SystemExit) as caught,
    ):
        runpy.run_path(script_path, run_name="__main__")

    output = capsys.readouterr().out
    assert caught.value.code == 2
    assert json.loads(output) == {
        "error": "SUPERVISOR_FAILED",
        "passed": False,
        "profile": "SMOKE_NON_ACCEPTANCE",
    }
    assert output.count("\n") == 1
    assert str(_DETAIL_ID) not in output
    assert _REVISION_TOKEN not in output
