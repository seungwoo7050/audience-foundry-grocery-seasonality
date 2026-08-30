"""Bounded Phase 0 HTTP read-profile runner for the local Django candidate."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Never
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
MAX_CONCURRENCY: Final = 20
REQUEST_TIMEOUT_SECONDS: Final = 2.0
MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
P95_LIMIT_MS: Final = 500.0
HTTP_5XX_RATE_LIMIT: Final = 0.005

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
_THREAD_STATE = threading.local()

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
    observation: HttpObservation


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
    throughput_rps: float
    revision_consistent: bool
    passed: bool

    def data(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "duration_seconds": self.duration_seconds,
            "target_requests_per_second": REQUESTS_PER_SECOND,
            "concurrency": MAX_CONCURRENCY,
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
            "throughput_rps": self.throughput_rps,
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
    token = value.strip()
    if not 1 <= len(token) <= 256:
        return None
    if any(ord(character) < 33 or ord(character) > 126 for character in token):
        return None
    return token


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


def _failed_observation() -> HttpObservation:
    return HttpObservation(
        latency_ms=REQUEST_TIMEOUT_SECONDS * 1000.0,
        status_code=None,
        valid=False,
        revision_token=None,
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
    elapsed_seconds: float,
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
    http_5xx_rate = http_5xx / completed_count if completed_count else 1.0
    measurement_seconds = max(float(config.duration_seconds), elapsed_seconds, 0.001)
    throughput_rps = completed_count / measurement_seconds
    report_passed = bool(
        completed_count == config.scheduled_requests
        and errors == 0
        and revision_consistent
        and p95_ms <= P95_LIMIT_MS
        and http_5xx_rate < HTTP_5XX_RATE_LIMIT
    )
    return LoadReport(
        profile=config.label,
        duration_seconds=config.duration_seconds,
        scheduled_requests=config.scheduled_requests,
        completed_requests=completed_count,
        catalog_list_search_requests=sum(request.kind == "catalog" for request in completed),
        detail_requests=sum(request.kind == "detail" for request in completed),
        successful_requests=successful,
        error_count=errors,
        http_5xx_count=http_5xx,
        p50_ms=round(p50_ms, 3),
        p95_ms=round(p95_ms, 3),
        max_ms=round(max_ms, 3),
        http_5xx_rate=round(http_5xx_rate, 6),
        throughput_rps=round(throughput_rps, 3),
        revision_consistent=revision_consistent,
        passed=report_passed,
    )


def run_profile(
    config: LoadProfileConfig,
    *,
    requester: Requester = http_request,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> LoadReport:
    started_at = monotonic()
    submitted: list[tuple[RequestKind, Future[HttpObservation]]] = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        for index in range(config.scheduled_requests):
            scheduled_at = started_at + (index / REQUESTS_PER_SECOND)
            remaining = scheduled_at - monotonic()
            if remaining > 0:
                sleeper(remaining)
            plan = request_plan(config, index)
            submitted.append(
                (
                    plan.kind,
                    executor.submit(
                        requester,
                        plan.url,
                        config.port,
                        REQUEST_TIMEOUT_SECONDS,
                    ),
                )
            )

        remaining_profile_time = started_at + config.duration_seconds - monotonic()
        if remaining_profile_time > 0:
            sleeper(remaining_profile_time)

        completed: list[CompletedRequest] = []
        for kind, future in submitted:
            try:
                observation = future.result()
            except Exception:
                observation = _failed_observation()
            completed.append(CompletedRequest(kind=kind, observation=observation))

    elapsed_seconds = max(0.0, monotonic() - started_at)
    return build_report(config, completed, elapsed_seconds=elapsed_seconds)


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


def main(arguments: list[str] | None = None) -> int:
    selected_arguments = sys.argv[1:] if arguments is None else arguments
    try:
        options = _parser().parse_args(selected_arguments)
        config = LoadProfileConfig(
            port=options.port,
            detail_id=options.detail_id,
            duration_seconds=options.duration_seconds,
            profile=options.profile,
        )
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


if __name__ == "__main__":
    raise SystemExit(main())
