"""Bounded Phase 0 HTTP read-profile runner for the local Django candidate.

The runner deliberately reaches only a trusted loopback process.  A standard-library
socket timeout cannot forcibly stop a peer that keeps slowly producing bytes, so the
external CLI supervises the worker in a separate process and terminates its process
group at a fixed deadline.  Queue time remains part of end-to-end latency and the
child report retains the stricter elapsed-time acceptance gate.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Never, cast
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

PHASE0_DURATION_SECONDS: Final = 900
REQUESTS_PER_SECOND: Final = 10
LOGICAL_VIRTUAL_USERS: Final = 20
MAX_CONCURRENCY: Final = LOGICAL_VIRTUAL_USERS
REQUEST_TIMEOUT_SECONDS: Final = 2.0
MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
P95_LIMIT_MS: Final = 500.0
HTTP_5XX_RATE_LIMIT: Final = 0.005
P95_SCHEDULE_JITTER_LIMIT_MS: Final = 100.0
PROFILE_COMPLETION_GRACE_SECONDS: Final = 3.0
# The child report still has the stricter three-second completion gate above.  The
# process watchdog adds only enough fixed headroom for interpreter startup and IPC.
CLI_WATCHDOG_GRACE_SECONDS: Final = 5.0
CLI_TERMINATION_GRACE_SECONDS: Final = 1.0
NOMINAL_REQUEST_INTERVAL_MS: Final = 1000.0 / REQUESTS_PER_SECOND
# Recover scheduler stalls by at most 10 ms per request; never submit less than
# 90 ms after the prior actual submission.
RECOVERY_FLOOR_INTERVAL_MS: Final = 90.0
_CLOCK_COMPARISON_EPSILON_MS: Final = 0.001

_LOCAL_HOST: Final = "127.0.0.1"
_WORKLOAD_SLOTS: Final[tuple[str, ...]] = (
    "catalog",
    "list_vegetable",
    "search_vegetable",
    "catalog",
    "list_fruit",
    "search_fruit",
    "catalog",
    "detail",
    "detail",
    "detail",
)
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")
_REVISION_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_THREAD_STATE = threading.local()
_CHILD_MODE: Final = "--internal-watchdog-child"
_MAX_CHILD_INPUT_CHARACTERS: Final = 4_096
_MAX_CHILD_OUTPUT_CHARACTERS: Final = 32_768

type RequestKind = Literal["catalog", "detail"]
type Requester = Callable[[str, int, float], "HttpObservation"]


class LoadProfileError(RuntimeError):
    """A configuration failure represented by one fixed non-sensitive code."""

    _CODES: Final = frozenset(
        {
            "argument_invalid",
            "duration_invalid",
            "local_url_invalid",
            "profile_invalid",
        }
    )

    def __init__(self, code: str) -> None:
        selected = code if code in self._CODES else "argument_invalid"
        self.code = selected
        super().__init__(selected)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise LoadProfileError("argument_invalid")


@dataclass(frozen=True, slots=True)
class LoadProfileConfig:
    port: int
    detail_id: uuid.UUID = field(repr=False)
    duration_seconds: int = PHASE0_DURATION_SECONDS
    profile: str = "phase0"

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise LoadProfileError("argument_invalid")
        if type(self.detail_id) is not uuid.UUID:
            raise LoadProfileError("argument_invalid")
        if type(self.duration_seconds) is not int or self.duration_seconds < 1:
            raise LoadProfileError("duration_invalid")
        if self.profile == "phase0":
            if self.duration_seconds != PHASE0_DURATION_SECONDS:
                raise LoadProfileError("duration_invalid")
        elif self.profile == "smoke":
            if self.duration_seconds >= PHASE0_DURATION_SECONDS:
                raise LoadProfileError("duration_invalid")
        else:
            raise LoadProfileError("profile_invalid")

    @property
    def scheduled_requests(self) -> int:
        return self.duration_seconds * REQUESTS_PER_SECOND

    @property
    def label(self) -> str:
        return "PHASE0_900S" if self.profile == "phase0" else "SMOKE_NON_ACCEPTANCE"


@dataclass(frozen=True, slots=True)
class RequestPlan:
    kind: RequestKind
    url: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class HttpObservation:
    latency_ms: float
    status_code: int | None
    valid: bool
    revision_token: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class CompletedRequest:
    kind: RequestKind
    virtual_user_id: int
    observation: HttpObservation


@dataclass(frozen=True, slots=True)
class RunMeasurements:
    elapsed_seconds: float
    p95_schedule_jitter_ms: float
    max_schedule_jitter_ms: float
    minimum_inter_submission_ms: float
    burst_interval_violations: int
    observed_peak_active: int


@dataclass(frozen=True, slots=True)
class LoadReport:
    profile: str
    duration_seconds: int
    scheduled_requests: int
    completed_requests: int
    catalog_list_search_requests: int
    detail_requests: int
    successful_requests: int
    error_count: int
    http_5xx_count: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    http_5xx_rate: float
    elapsed_seconds: float
    throughput_rps: float
    minimum_accepted_throughput_rps: float
    p95_schedule_jitter_ms: float
    max_schedule_jitter_ms: float
    minimum_inter_submission_ms: float
    burst_interval_violations: int
    logical_users_configured: int
    logical_users_participated: int
    observed_peak_active: int
    duration_contract_met: bool
    throughput_target_met: bool
    schedule_jitter_contract_met: bool
    no_burst_contract_met: bool
    schedule_contract_met: bool
    logical_users_contract_met: bool
    concurrency_contract_met: bool
    workload_consistent: bool
    revision_consistent: bool
    passed: bool

    def data(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "duration_seconds": self.duration_seconds,
            "target_requests_per_second": REQUESTS_PER_SECOND,
            "logical_users": {
                "configured": self.logical_users_configured,
                "participated": self.logical_users_participated,
                "round_robin_contract_met": self.logical_users_contract_met,
            },
            "concurrency": {
                "in_flight_limit": MAX_CONCURRENCY,
                "observed_in_flight_peak": self.observed_peak_active,
                "in_flight_within_limit": self.concurrency_contract_met,
            },
            "counts": {
                "scheduled": self.scheduled_requests,
                "completed": self.completed_requests,
                "catalog_list_search": self.catalog_list_search_requests,
                "detail": self.detail_requests,
                "successful": self.successful_requests,
                "errors": self.error_count,
                "http_5xx": self.http_5xx_count,
            },
            "latency_ms": {
                "p50": self.p50_ms,
                "p95": self.p95_ms,
                "max": self.max_ms,
            },
            "http_5xx_rate": self.http_5xx_rate,
            "timing": {
                "elapsed_seconds": self.elapsed_seconds,
                "completion_grace_seconds": PROFILE_COMPLETION_GRACE_SECONDS,
                "duration_contract_met": self.duration_contract_met,
                "throughput_rps": self.throughput_rps,
                "minimum_accepted_throughput_rps": self.minimum_accepted_throughput_rps,
                "throughput_target_met": self.throughput_target_met,
                "p95_schedule_jitter_ms": self.p95_schedule_jitter_ms,
                "max_schedule_jitter_ms": self.max_schedule_jitter_ms,
                "p95_schedule_jitter_limit_ms": P95_SCHEDULE_JITTER_LIMIT_MS,
                "schedule_jitter_contract_met": self.schedule_jitter_contract_met,
                "minimum_inter_submission_ms": self.minimum_inter_submission_ms,
                "nominal_request_interval_ms": NOMINAL_REQUEST_INTERVAL_MS,
                "recovery_floor_interval_ms": RECOVERY_FLOOR_INTERVAL_MS,
                "burst_interval_violations": self.burst_interval_violations,
                "no_burst_contract_met": self.no_burst_contract_met,
                "schedule_contract_met": self.schedule_contract_met,
            },
            "workload_consistent": self.workload_consistent,
            "revision_consistent": self.revision_consistent,
            "passed": self.passed,
        }

    def render(self) -> str:
        return json.dumps(
            self.data(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _get_opener() -> OpenerDirector:
    existing = getattr(_THREAD_STATE, "opener", None)
    if isinstance(existing, OpenerDirector):
        return existing
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
    _THREAD_STATE.opener = opener
    return opener


def _validate_local_url(url: str, *, expected_port: int) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise LoadProfileError("local_url_invalid") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname != _LOCAL_HOST
        or port != expected_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise LoadProfileError("local_url_invalid")


def request_plan(config: LoadProfileConfig, index: int) -> RequestPlan:
    if type(index) is not int or index < 0:
        raise LoadProfileError("argument_invalid")
    base_url = f"http://{_LOCAL_HOST}:{config.port}"
    slot = _WORKLOAD_SLOTS[index % len(_WORKLOAD_SLOTS)]
    if slot == "catalog":
        plan = RequestPlan(kind="catalog", url=f"{base_url}/")
    elif slot == "list_vegetable":
        plan = RequestPlan(
            kind="catalog",
            url=f"{base_url}/?{urlencode({'category': 'vegetable'})}",
        )
    elif slot == "search_vegetable":
        plan = RequestPlan(
            kind="catalog",
            url=f"{base_url}/?{urlencode({'q': '배추'})}",
        )
    elif slot == "list_fruit":
        plan = RequestPlan(
            kind="catalog",
            url=f"{base_url}/?{urlencode({'category': 'fruit'})}",
        )
    elif slot == "search_fruit":
        plan = RequestPlan(
            kind="catalog",
            url=f"{base_url}/?{urlencode({'q': '사과'})}",
        )
    else:
        plan = RequestPlan(
            kind="detail",
            url=f"{base_url}/series/{config.detail_id}/",
        )
    _validate_local_url(plan.url, expected_port=config.port)
    return plan


def _cache_control_is_no_store(value: object) -> bool:
    if type(value) is not str:
        return False
    directives = {part.strip().lower().split("=", maxsplit=1)[0] for part in value.split(",")}
    return "no-store" in directives


def _revision_token(value: object) -> str | None:
    if type(value) is not str:
        return None
    if _REVISION_SHA256.fullmatch(value) is None:
        return None
    return value


def http_request(url: str, expected_port: int, timeout_seconds: float) -> HttpObservation:
    started_at = time.perf_counter()
    status_code: int | None = None
    revision: str | None = None
    valid = False
    try:
        _validate_local_url(url, expected_port=expected_port)
        request = Request(  # noqa: S310 - the loopback-only URL is validated above.
            url,
            headers={
                "Accept": "text/html",
                "User-Agent": "audience-foundry-phase0-load/1",
            },
            method="GET",
        )
        with _get_opener().open(request, timeout=timeout_seconds) as response:
            raw_status = response.getcode()
            status_code = raw_status if type(raw_status) is int else None
            final_url = response.geturl()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            cache_control = response.headers.get("Cache-Control")
            revision = _revision_token(response.headers.get("X-Publication-Fact-Set"))
            valid = bool(
                status_code == 200
                and final_url == url
                and len(body) <= MAX_RESPONSE_BYTES
                and _cache_control_is_no_store(cache_control)
                and revision is not None
            )
    except HTTPError as error:
        status_code = error.code if type(error.code) is int else None
        error.close()
    except Exception:
        valid = False
    latency_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    return HttpObservation(
        latency_ms=latency_ms,
        status_code=status_code,
        valid=valid,
        revision_token=revision,
    )


def _failed_observation(*, latency_ms: float = 0.0) -> HttpObservation:
    return HttpObservation(
        latency_ms=max(0.0, latency_ms),
        status_code=None,
        valid=False,
        revision_token=None,
    )


@dataclass(frozen=True, slots=True)
class _TimedObservation:
    observation: HttpObservation
    schedule_jitter_ms: float


class _ActiveRequestCounter:
    """Thread-safe observed concurrency without retaining request data."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self._peak = 0

    def enter(self) -> None:
        with self._lock:
            self._active += 1
            self._peak = max(self._peak, self._active)

    def leave(self) -> None:
        with self._lock:
            self._active -= 1

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak


def _execute_scheduled_request(
    virtual_user_id: int,
    requester: Requester,
    url: str,
    port: int,
    timeout_seconds: float,
    paced_deadline: float,
    monotonic: Callable[[], float],
    active_counter: _ActiveRequestCounter,
) -> _TimedObservation:
    if type(virtual_user_id) is not int or not 0 <= virtual_user_id < LOGICAL_VIRTUAL_USERS:
        raise LoadProfileError("argument_invalid")
    request_started_at = monotonic()
    schedule_jitter_seconds = max(0.0, request_started_at - paced_deadline)
    active_counter.enter()
    try:
        try:
            observation = requester(url, port, timeout_seconds)
        except Exception:
            observation = _failed_observation()
        completed_at = monotonic()
    finally:
        active_counter.leave()

    # The requester's service latency and this outer clock are independent in tests.
    # Taking the maximum keeps production wall time authoritative while preserving a
    # deterministic injected service duration.  Both include time through completion.
    wall_latency_ms = max(0.0, (completed_at - paced_deadline) * 1000.0)
    end_to_end_latency_ms = max(
        wall_latency_ms,
        (schedule_jitter_seconds * 1000.0) + observation.latency_ms,
    )
    timed_observation = HttpObservation(
        latency_ms=end_to_end_latency_ms,
        status_code=observation.status_code,
        valid=observation.valid,
        revision_token=observation.revision_token,
    )
    return _TimedObservation(
        observation=timed_observation,
        schedule_jitter_ms=schedule_jitter_seconds * 1000.0,
    )


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return ordered[index]


def build_report(
    config: LoadProfileConfig,
    completed: list[CompletedRequest],
    *,
    measurements: RunMeasurements,
) -> LoadReport:
    latencies = [request.observation.latency_ms for request in completed]
    successful = sum(
        request.observation.valid and request.observation.status_code == 200
        for request in completed
    )
    http_5xx = sum(
        request.observation.status_code is not None
        and 500 <= request.observation.status_code <= 599
        for request in completed
    )
    errors = len(completed) - successful - http_5xx
    revision_tokens = [
        request.observation.revision_token
        for request in completed
        if request.observation.valid
        and request.observation.status_code == 200
        and request.observation.revision_token is not None
    ]
    revision_consistent = bool(
        successful > 0 and len(revision_tokens) == successful and len(set(revision_tokens)) == 1
    )
    p50_ms = _percentile(latencies, 50)
    p95_ms = _percentile(latencies, 95)
    max_ms = max(latencies, default=0.0)
    completed_count = len(completed)
    catalog_count = sum(request.kind == "catalog" for request in completed)
    detail_count = sum(request.kind == "detail" for request in completed)
    http_5xx_rate = http_5xx / completed_count if completed_count else 1.0
    measurement_seconds = max(measurements.elapsed_seconds, 0.001)
    throughput_rps = completed_count / measurement_seconds
    maximum_elapsed_seconds = float(config.duration_seconds) + PROFILE_COMPLETION_GRACE_SECONDS
    minimum_accepted_throughput_rps = config.scheduled_requests / maximum_elapsed_seconds
    duration_contract_met = bool(
        float(config.duration_seconds) <= measurements.elapsed_seconds <= maximum_elapsed_seconds
    )
    throughput_target_met = bool(throughput_rps >= minimum_accepted_throughput_rps)
    schedule_jitter_contract_met = bool(
        measurements.p95_schedule_jitter_ms <= P95_SCHEDULE_JITTER_LIMIT_MS
    )
    no_burst_contract_met = bool(
        measurements.burst_interval_violations == 0
        and measurements.minimum_inter_submission_ms + _CLOCK_COMPARISON_EPSILON_MS
        >= RECOVERY_FLOOR_INTERVAL_MS
    )
    schedule_contract_met = schedule_jitter_contract_met and no_burst_contract_met
    expected_virtual_user_ids = [
        index % LOGICAL_VIRTUAL_USERS for index in range(config.scheduled_requests)
    ]
    completed_virtual_user_ids = [request.virtual_user_id for request in completed]
    logical_users_participated = len(
        {
            virtual_user_id
            for virtual_user_id in completed_virtual_user_ids
            if type(virtual_user_id) is int and 0 <= virtual_user_id < LOGICAL_VIRTUAL_USERS
        }
    )
    logical_users_contract_met = bool(completed_virtual_user_ids == expected_virtual_user_ids)
    concurrency_contract_met = bool(1 <= measurements.observed_peak_active <= MAX_CONCURRENCY)
    workload_consistent = bool(
        catalog_count == (config.scheduled_requests * 7) // 10
        and detail_count == (config.scheduled_requests * 3) // 10
    )
    report_passed = bool(
        completed_count == config.scheduled_requests
        and workload_consistent
        and errors == 0
        and revision_consistent
        and p95_ms <= P95_LIMIT_MS
        and http_5xx_rate < HTTP_5XX_RATE_LIMIT
        and duration_contract_met
        and throughput_target_met
        and schedule_contract_met
        and logical_users_contract_met
        and concurrency_contract_met
    )
    return LoadReport(
        profile=config.label,
        duration_seconds=config.duration_seconds,
        scheduled_requests=config.scheduled_requests,
        completed_requests=completed_count,
        catalog_list_search_requests=catalog_count,
        detail_requests=detail_count,
        successful_requests=successful,
        error_count=errors,
        http_5xx_count=http_5xx,
        p50_ms=round(p50_ms, 3),
        p95_ms=round(p95_ms, 3),
        max_ms=round(max_ms, 3),
        http_5xx_rate=round(http_5xx_rate, 6),
        elapsed_seconds=round(measurements.elapsed_seconds, 3),
        throughput_rps=round(throughput_rps, 3),
        minimum_accepted_throughput_rps=round(
            minimum_accepted_throughput_rps,
            3,
        ),
        p95_schedule_jitter_ms=round(measurements.p95_schedule_jitter_ms, 3),
        max_schedule_jitter_ms=round(measurements.max_schedule_jitter_ms, 3),
        minimum_inter_submission_ms=round(
            measurements.minimum_inter_submission_ms,
            3,
        ),
        burst_interval_violations=measurements.burst_interval_violations,
        logical_users_configured=LOGICAL_VIRTUAL_USERS,
        logical_users_participated=logical_users_participated,
        observed_peak_active=measurements.observed_peak_active,
        duration_contract_met=duration_contract_met,
        throughput_target_met=throughput_target_met,
        schedule_jitter_contract_met=schedule_jitter_contract_met,
        no_burst_contract_met=no_burst_contract_met,
        schedule_contract_met=schedule_contract_met,
        logical_users_contract_met=logical_users_contract_met,
        concurrency_contract_met=concurrency_contract_met,
        workload_consistent=workload_consistent,
        revision_consistent=revision_consistent,
        passed=report_passed,
    )


def run_profile(
    config: LoadProfileConfig,
    *,
    requester: Requester = http_request,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    executor_factory: Callable[[int], Executor] = ThreadPoolExecutor,
) -> LoadReport:
    started_at = monotonic()
    recovery_floor_seconds = RECOVERY_FLOOR_INTERVAL_MS / 1000.0
    previous_submission_at: float | None = None
    inter_submission_ms: list[float] = []
    active_counter = _ActiveRequestCounter()
    submitted: list[tuple[RequestKind, int, float, Future[_TimedObservation]]] = []
    with ExitStack() as executor_stack:
        virtual_user_executors = tuple(
            executor_stack.enter_context(executor_factory(1))
            for _virtual_user_id in range(LOGICAL_VIRTUAL_USERS)
        )
        for index in range(config.scheduled_requests):
            nominal_deadline = started_at + (index / REQUESTS_PER_SECOND)
            paced_deadline = nominal_deadline
            if previous_submission_at is not None:
                paced_deadline = max(
                    paced_deadline,
                    previous_submission_at + recovery_floor_seconds,
                )
            _sleep_until(paced_deadline, monotonic=monotonic, sleeper=sleeper)
            submitted_at = monotonic()
            if previous_submission_at is not None:
                inter_submission_ms.append(
                    max(0.0, (submitted_at - previous_submission_at) * 1000.0)
                )
            previous_submission_at = submitted_at
            plan = request_plan(config, index)
            virtual_user_id = index % LOGICAL_VIRTUAL_USERS
            submitted.append(
                (
                    plan.kind,
                    virtual_user_id,
                    paced_deadline,
                    virtual_user_executors[virtual_user_id].submit(
                        _execute_scheduled_request,
                        virtual_user_id,
                        requester,
                        plan.url,
                        config.port,
                        REQUEST_TIMEOUT_SECONDS,
                        paced_deadline,
                        monotonic,
                        active_counter,
                    ),
                )
            )

        _sleep_until(
            started_at + config.duration_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
        )

        completed: list[CompletedRequest] = []
        schedule_jitter_ms: list[float] = []
        for kind, virtual_user_id, paced_deadline, future in submitted:
            try:
                timed = future.result()
            except Exception:
                fallback_latency_ms = max(
                    0.0,
                    (monotonic() - paced_deadline) * 1000.0,
                )
                timed = _TimedObservation(
                    observation=_failed_observation(latency_ms=fallback_latency_ms),
                    schedule_jitter_ms=fallback_latency_ms,
                )
            schedule_jitter_ms.append(timed.schedule_jitter_ms)
            completed.append(
                CompletedRequest(
                    kind=kind,
                    virtual_user_id=virtual_user_id,
                    observation=timed.observation,
                )
            )

    elapsed_seconds = max(0.0, monotonic() - started_at)
    minimum_inter_submission_ms = min(inter_submission_ms, default=0.0)
    measurements = RunMeasurements(
        elapsed_seconds=elapsed_seconds,
        p95_schedule_jitter_ms=_percentile(schedule_jitter_ms, 95),
        max_schedule_jitter_ms=max(schedule_jitter_ms, default=0.0),
        minimum_inter_submission_ms=minimum_inter_submission_ms,
        burst_interval_violations=sum(
            interval_ms + _CLOCK_COMPARISON_EPSILON_MS < RECOVERY_FLOOR_INTERVAL_MS
            for interval_ms in inter_submission_ms
        ),
        observed_peak_active=active_counter.peak,
    )
    return build_report(config, completed, measurements=measurements)


def _sleep_until(
    deadline: float,
    *,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> None:
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleeper(remaining)


def _positive_integer(value: str) -> int:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("integer_invalid")
    parsed = int(value)
    if parsed > PHASE0_DURATION_SECONDS:
        raise argparse.ArgumentTypeError("integer_invalid")
    return parsed


def _port(value: str) -> int:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("port_invalid")
    parsed = int(value)
    if parsed > 65_535:
        raise argparse.ArgumentTypeError("port_invalid")
    return parsed


def _detail_id(value: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError("detail_id_invalid") from None
    if str(parsed) != value:
        raise argparse.ArgumentTypeError("detail_id_invalid")
    return parsed


def _profile(value: str) -> str:
    if value not in {"phase0", "smoke"}:
        raise argparse.ArgumentTypeError("profile_invalid")
    return value


def _parser() -> _SafeArgumentParser:
    parser = _SafeArgumentParser(description="Run the bounded local Phase 0 HTTP read profile.")
    parser.add_argument("--port", required=True, type=_port)
    parser.add_argument("--detail-id", required=True, type=_detail_id)
    parser.add_argument("--profile", default="phase0", type=_profile)
    parser.add_argument(
        "--duration-seconds",
        default=PHASE0_DURATION_SECONDS,
        type=_positive_integer,
    )
    return parser


def _safe_failure(*, profile: str, code: str) -> str:
    return json.dumps(
        {"error": code, "passed": False, "profile": profile},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _config_from_arguments(arguments: list[str]) -> LoadProfileConfig:
    options = _parser().parse_args(arguments)
    return LoadProfileConfig(
        port=options.port,
        detail_id=options.detail_id,
        duration_seconds=options.duration_seconds,
        profile=options.profile,
    )


def main(arguments: list[str] | None = None) -> int:
    selected_arguments = sys.argv[1:] if arguments is None else arguments
    try:
        config = _config_from_arguments(selected_arguments)
    except LoadProfileError:
        print(_safe_failure(profile="UNAVAILABLE", code="CONFIG_INVALID"))
        return 2
    try:
        report = run_profile(config)
    except Exception:
        print(_safe_failure(profile=config.label, code="RUNNER_FAILED"))
        return 2
    print(report.render())
    return 0 if report.passed else 1


def _exact_typed_object(
    value: object,
    expected_types: dict[str, type[Any]],
) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    selected = cast(dict[str, object], value)
    if set(selected) != set(expected_types):
        return None
    if any(type(selected[name]) is not expected for name, expected in expected_types.items()):
        return None
    return selected


def _nonnegative_finite_float(value: object) -> bool:
    return bool(type(value) is float and math.isfinite(value) and 0.0 <= value <= 1_000_000_000.0)


def _bounded_nonnegative_integer(value: object, *, maximum: int) -> bool:
    return bool(type(value) is int and 0 <= value <= maximum)


def _validated_report_payload(
    value: object,
    *,
    config: LoadProfileConfig,
) -> dict[str, object] | None:
    report = _exact_typed_object(
        value,
        {
            "profile": str,
            "duration_seconds": int,
            "target_requests_per_second": int,
            "logical_users": dict,
            "concurrency": dict,
            "counts": dict,
            "latency_ms": dict,
            "http_5xx_rate": float,
            "timing": dict,
            "workload_consistent": bool,
            "revision_consistent": bool,
            "passed": bool,
        },
    )
    if report is None:
        return None
    logical_users = _exact_typed_object(
        report["logical_users"],
        {
            "configured": int,
            "participated": int,
            "round_robin_contract_met": bool,
        },
    )
    concurrency = _exact_typed_object(
        report["concurrency"],
        {
            "in_flight_limit": int,
            "observed_in_flight_peak": int,
            "in_flight_within_limit": bool,
        },
    )
    counts = _exact_typed_object(
        report["counts"],
        {
            "scheduled": int,
            "completed": int,
            "catalog_list_search": int,
            "detail": int,
            "successful": int,
            "errors": int,
            "http_5xx": int,
        },
    )
    latency_ms = _exact_typed_object(
        report["latency_ms"],
        {"p50": float, "p95": float, "max": float},
    )
    timing = _exact_typed_object(
        report["timing"],
        {
            "elapsed_seconds": float,
            "completion_grace_seconds": float,
            "duration_contract_met": bool,
            "throughput_rps": float,
            "minimum_accepted_throughput_rps": float,
            "throughput_target_met": bool,
            "p95_schedule_jitter_ms": float,
            "max_schedule_jitter_ms": float,
            "p95_schedule_jitter_limit_ms": float,
            "schedule_jitter_contract_met": bool,
            "minimum_inter_submission_ms": float,
            "nominal_request_interval_ms": float,
            "recovery_floor_interval_ms": float,
            "burst_interval_violations": int,
            "no_burst_contract_met": bool,
            "schedule_contract_met": bool,
        },
    )
    if logical_users is None:
        return None
    if concurrency is None:
        return None
    if counts is None:
        return None
    if latency_ms is None:
        return None
    if timing is None:
        return None

    if (
        report["profile"] != config.label
        or report["duration_seconds"] != config.duration_seconds
        or report["target_requests_per_second"] != REQUESTS_PER_SECOND
        or logical_users["configured"] != LOGICAL_VIRTUAL_USERS
        or concurrency["in_flight_limit"] != MAX_CONCURRENCY
        or counts["scheduled"] != config.scheduled_requests
        or timing["completion_grace_seconds"] != PROFILE_COMPLETION_GRACE_SECONDS
        or timing["p95_schedule_jitter_limit_ms"] != P95_SCHEDULE_JITTER_LIMIT_MS
        or timing["nominal_request_interval_ms"] != NOMINAL_REQUEST_INTERVAL_MS
        or timing["recovery_floor_interval_ms"] != RECOVERY_FLOOR_INTERVAL_MS
    ):
        return None

    integer_values = [
        logical_users["participated"],
        concurrency["observed_in_flight_peak"],
        counts["completed"],
        counts["catalog_list_search"],
        counts["detail"],
        counts["successful"],
        counts["errors"],
        counts["http_5xx"],
        timing["burst_interval_violations"],
    ]
    if not all(
        _bounded_nonnegative_integer(value, maximum=config.scheduled_requests)
        for value in integer_values
    ):
        return None
    if not _bounded_nonnegative_integer(
        logical_users["participated"],
        maximum=LOGICAL_VIRTUAL_USERS,
    ) or not _bounded_nonnegative_integer(
        concurrency["observed_in_flight_peak"],
        maximum=MAX_CONCURRENCY,
    ):
        return None

    float_values = [
        report["http_5xx_rate"],
        latency_ms["p50"],
        latency_ms["p95"],
        latency_ms["max"],
        timing["elapsed_seconds"],
        timing["completion_grace_seconds"],
        timing["throughput_rps"],
        timing["minimum_accepted_throughput_rps"],
        timing["p95_schedule_jitter_ms"],
        timing["max_schedule_jitter_ms"],
        timing["p95_schedule_jitter_limit_ms"],
        timing["minimum_inter_submission_ms"],
        timing["nominal_request_interval_ms"],
        timing["recovery_floor_interval_ms"],
    ]
    if not all(_nonnegative_finite_float(value) for value in float_values):
        return None

    completed_count = cast(int, counts["completed"])
    participated = cast(int, logical_users["participated"])
    observed_peak = cast(int, concurrency["observed_in_flight_peak"])
    if (
        cast(int, counts["catalog_list_search"]) + cast(int, counts["detail"]) != completed_count
        or cast(int, counts["successful"])
        + cast(int, counts["errors"])
        + cast(int, counts["http_5xx"])
        != completed_count
        or cast(float, report["http_5xx_rate"]) > 1.0
        or cast(bool, concurrency["in_flight_within_limit"])
        != (1 <= observed_peak <= MAX_CONCURRENCY)
        or (
            cast(bool, logical_users["round_robin_contract_met"])
            and participated != min(LOGICAL_VIRTUAL_USERS, config.scheduled_requests)
        )
    ):
        return None
    return report


def _validated_child_output(
    output: str,
    *,
    return_code: int | None,
    config: LoadProfileConfig,
) -> str | None:
    if (
        len(output) > _MAX_CHILD_OUTPUT_CHARACTERS
        or not output.endswith("\n")
        or output.count("\n") != 1
    ):
        return None
    try:
        output.encode("ascii")
        decoded: object = json.loads(output[:-1])
    except UnicodeEncodeError, json.JSONDecodeError:
        return None

    report = _validated_report_payload(decoded, config=config)
    if report is not None:
        if return_code not in {0, 1} or cast(bool, report["passed"]) != (return_code == 0):
            return None
        return json.dumps(
            report,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    failure = _exact_typed_object(
        decoded,
        {"error": str, "passed": bool, "profile": str},
    )
    if (
        failure is None
        or failure["error"] != "RUNNER_FAILED"
        or failure["passed"] is not False
        or failure["profile"] != config.label
        or return_code != 2
    ):
        return None
    return _safe_failure(profile=config.label, code="RUNNER_FAILED")


def _terminate_child(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError, ProcessLookupError:
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.communicate(timeout=CLI_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError, ProcessLookupError:
        try:
            process.kill()
        except OSError:
            return
    try:
        process.communicate(timeout=CLI_TERMINATION_GRACE_SECONDS)
    except Exception:
        return


def supervised_main(arguments: list[str] | None = None) -> int:
    selected_arguments = list(sys.argv[1:] if arguments is None else arguments)
    try:
        config = _config_from_arguments(selected_arguments)
    except LoadProfileError:
        print(_safe_failure(profile="UNAVAILABLE", code="CONFIG_INVALID"))
        return 2

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter/script command.
            [sys.executable, os.path.abspath(__file__), _CHILD_MODE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            close_fds=True,
            start_new_session=True,
            env={
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
                "PYTHONUTF8": "1",
            },
        )
        output, _discarded_stderr = process.communicate(
            input=json.dumps(
                selected_arguments,
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            timeout=float(config.duration_seconds) + CLI_WATCHDOG_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired:
        if process is not None:
            _terminate_child(process)
        print(_safe_failure(profile=config.label, code="WATCHDOG_TIMEOUT"))
        return 2
    except KeyboardInterrupt:
        if process is not None:
            _terminate_child(process)
        print(_safe_failure(profile=config.label, code="WATCHDOG_INTERRUPTED"))
        return 130
    except Exception:
        if process is not None:
            _terminate_child(process)
        print(_safe_failure(profile=config.label, code="SUPERVISOR_FAILED"))
        return 2

    safe_output = _validated_child_output(
        output,
        return_code=process.returncode,
        config=config,
    )
    if safe_output is None:
        print(_safe_failure(profile=config.label, code="CHILD_RESULT_INVALID"))
        return 2
    print(safe_output)
    return cast(int, process.returncode)


def _child_main() -> int:
    try:
        encoded_arguments = sys.stdin.read(_MAX_CHILD_INPUT_CHARACTERS + 1)
        if len(encoded_arguments) > _MAX_CHILD_INPUT_CHARACTERS:
            raise ValueError
        decoded_arguments: object = json.loads(encoded_arguments)
        if (
            type(decoded_arguments) is not list
            or len(decoded_arguments) > 16
            or any(type(value) is not str or len(value) > 512 for value in decoded_arguments)
        ):
            raise ValueError
        arguments = cast(list[str], decoded_arguments)
    except Exception:
        print(_safe_failure(profile="UNAVAILABLE", code="CONFIG_INVALID"))
        return 2
    return main(arguments)


if __name__ == "__main__":
    if sys.argv[1:] == [_CHILD_MODE]:
        raise SystemExit(_child_main())
    raise SystemExit(supervised_main())
