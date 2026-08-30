"""Run one bounded, secret-safe KAMIS recent-price ingestion generation."""

from __future__ import annotations

import logging
import uuid
from argparse import ArgumentTypeError
from enum import StrEnum
from typing import Final, NoReturn

from django.core.management.base import BaseCommand, CommandError, CommandParser

from grocery.models import FetchAttempt, ParseRun
from grocery.observability import Severity, log_event
from grocery.source.client import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    KamisHttpClient,
    KamisTransportError,
)
from grocery.source.configuration import bootstrap_kamis_source_configuration
from grocery.source.generation import (
    ParseGenerationError,
    ParseGenerationErrorCode,
    ParseGenerationFailureCode,
    complete_kamis_parse_generation,
    fail_kamis_parse_run,
    start_or_get_kamis_parse_run,
)
from grocery.source.kamis import KamisParseError, parse_recent_price_rows
from grocery.source.persistence import complete_kamis_fetch, fail_kamis_fetch, start_kamis_fetch
from grocery.source.registry import INITIAL_RETAIL_IDENTITY_REGISTRY
from grocery.source.secrets import SecretLoadError, load_kamis_api_key

_LOGGER: Final = logging.getLogger("grocery.audit")

_IDENTITY_PARSE_CODES: Final = frozenset(
    {
        "category_name_drift",
        "coverage_identity_drift",
        "grade_code_name_drift",
        "item_code_name_drift",
        "product_class_name_drift",
        "unit_identity_drift",
        "unsupported_category",
        "unsupported_product_class",
        "variety_code_name_drift",
    }
)


class _CommandFailureCode(StrEnum):
    SOURCE_START_FAILED = "INGEST_SOURCE_START_FAILED"
    SECRET_UNAVAILABLE = "INGEST_SECRET_UNAVAILABLE"  # noqa: S105 - safe status code
    FETCH_FAILED = "INGEST_FETCH_FAILED"
    FETCH_FINALIZATION_FAILED = "INGEST_FETCH_FINALIZATION_FAILED"
    FETCH_PERSISTENCE_FAILED = "INGEST_FETCH_PERSISTENCE_FAILED"
    PARSE_START_FAILED = "INGEST_PARSE_START_FAILED"
    PARSE_FAILED = "INGEST_PARSE_FAILED"
    GENERATION_FAILED = "INGEST_GENERATION_FAILED"


def _page_size(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise ArgumentTypeError("page_size_invalid") from None
    if parsed < 1 or parsed > MAX_PAGE_SIZE:
        raise ArgumentTypeError("page_size_invalid")
    return parsed


def _safe_option_page_size(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_PAGE_SIZE:
        raise CommandError("code=INGEST_PAGE_SIZE_INVALID")
    return value


def _failure_receipt(
    code: _CommandFailureCode,
    *,
    attempt: FetchAttempt | None = None,
    parse_run: ParseRun | None = None,
) -> str:
    fields = [f"code={code.value}"]
    if attempt is not None:
        fields.extend(
            (
                f"attempt_id={attempt.id}",
                f"status={attempt.state}",
                f"pages={attempt.received_page_count}",
                f"rows={attempt.received_row_count}",
            )
        )
    if parse_run is not None:
        fields.extend(
            (
                f"parse_run_id={parse_run.id}",
                f"parse_status={parse_run.status}",
            )
        )
    return " ".join(fields)


def _log_fetch(
    command_run_id: uuid.UUID,
    attempt: FetchAttempt,
    *,
    message_code: str,
    lifecycle_event: str,
    severity: Severity = "INFO",
) -> None:
    log_event(
        _LOGGER,
        severity,
        message_code,
        command_run_id=command_run_id,
        lifecycle_id=attempt.id,
        lifecycle_status=str(attempt.state),
        lifecycle_event=lifecycle_event,
    )


def _log_parse(
    command_run_id: uuid.UUID,
    parse_run: ParseRun,
    *,
    message_code: str,
    lifecycle_event: str,
    severity: Severity = "INFO",
) -> None:
    log_event(
        _LOGGER,
        severity,
        message_code,
        command_run_id=command_run_id,
        lifecycle_id=parse_run.id,
        lifecycle_status=str(parse_run.status),
        lifecycle_event=lifecycle_event,
    )


def _finalize_fetch_failure(
    command_run_id: uuid.UUID,
    attempt: FetchAttempt,
    error: KamisTransportError,
    *,
    command_code: _CommandFailureCode,
) -> NoReturn:
    try:
        failed = fail_kamis_fetch(attempt.id, error)
    except Exception:
        log_event(
            _LOGGER,
            "ERROR",
            "ingest.fetch.finalization_failed",
            command_run_id=command_run_id,
            lifecycle_id=attempt.id,
            lifecycle_status=FetchAttempt.State.STARTED,
            lifecycle_event="FETCH_FINALIZATION_FAILED",
        )
        raise CommandError(
            _failure_receipt(_CommandFailureCode.FETCH_FINALIZATION_FAILED, attempt=attempt)
        ) from None
    _log_fetch(
        command_run_id,
        failed,
        message_code="ingest.fetch.failed",
        lifecycle_event="FETCH_FAILED",
        severity="ERROR",
    )
    raise CommandError(_failure_receipt(command_code, attempt=failed)) from None


def _parse_failure_code(error: KamisParseError) -> ParseGenerationFailureCode:
    if error.code in _IDENTITY_PARSE_CODES:
        return ParseGenerationFailureCode.IDENTITY_DRIFT
    if error.code == "duplicate_semantic_identity":
        return ParseGenerationFailureCode.RECONCILIATION_FAILED
    return ParseGenerationFailureCode.SCHEMA_INVALID


def _generation_failure_code(error: ParseGenerationError) -> ParseGenerationFailureCode:
    if error.code in {
        ParseGenerationErrorCode.NONDETERMINISTIC_REPLAY,
        ParseGenerationErrorCode.RESULT_RECONCILIATION_FAILED,
    }:
        return ParseGenerationFailureCode.RECONCILIATION_FAILED
    return ParseGenerationFailureCode.PERSISTENCE_FAILED


def _finalize_parse_failure(
    command_run_id: uuid.UUID,
    parse_run: ParseRun,
    failure_code: ParseGenerationFailureCode,
    *,
    command_code: _CommandFailureCode,
) -> NoReturn:
    failed: ParseRun | None = None
    try:
        failed = fail_kamis_parse_run(parse_run.id, failure_code)
    except Exception:
        # A replay may already be terminal. Never replace the original safe failure
        # with exception text from the ORM or parser boundary.
        try:
            candidate = ParseRun.objects.only("id", "status").get(pk=parse_run.id)
        except Exception:
            candidate = parse_run
        if candidate.status in {
            ParseRun.Status.VALIDATED,
            ParseRun.Status.QUARANTINED,
            ParseRun.Status.FAILED,
        }:
            failed = candidate
    if failed is None:
        _log_parse(
            command_run_id,
            parse_run,
            message_code="ingest.parse.finalization_failed",
            lifecycle_event="PARSE_FINALIZATION_FAILED",
            severity="ERROR",
        )
        raise CommandError(
            _failure_receipt(_CommandFailureCode.GENERATION_FAILED, parse_run=parse_run)
        ) from None
    _log_parse(
        command_run_id,
        failed,
        message_code="ingest.parse.failed",
        lifecycle_event="PARSE_FAILED",
        severity="ERROR",
    )
    raise CommandError(_failure_receipt(command_code, parse_run=failed)) from None


class Command(BaseCommand):
    help = "Fetch and persist one bounded KAMIS recent-price generation."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--page-size",
            default=DEFAULT_PAGE_SIZE,
            type=_page_size,
            help=f"Rows per source page (1..{MAX_PAGE_SIZE}).",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        page_size = _safe_option_page_size(options.get("page_size", DEFAULT_PAGE_SIZE))
        command_run_id = uuid.uuid4()

        try:
            source = bootstrap_kamis_source_configuration()
            attempt = start_kamis_fetch(source.id, acquisition_run_id=command_run_id)
        except Exception:
            log_event(
                _LOGGER,
                "ERROR",
                "ingest.source.start_failed",
                command_run_id=command_run_id,
                lifecycle_event="SOURCE_START_FAILED",
            )
            raise CommandError(_failure_receipt(_CommandFailureCode.SOURCE_START_FAILED)) from None

        _log_fetch(
            command_run_id,
            attempt,
            message_code="ingest.fetch.started",
            lifecycle_event="FETCH_STARTED",
        )

        try:
            secret = load_kamis_api_key()
        except SecretLoadError:
            _finalize_fetch_failure(
                command_run_id,
                attempt,
                KamisTransportError("service_key_missing"),
                command_code=_CommandFailureCode.SECRET_UNAVAILABLE,
            )
        except Exception:
            _finalize_fetch_failure(
                command_run_id,
                attempt,
                KamisTransportError("service_key_missing"),
                command_code=_CommandFailureCode.SECRET_UNAVAILABLE,
            )

        try:
            result = KamisHttpClient().fetch_recent_prices(
                secret.reveal(),
                page_size=page_size,
            )
        except KamisTransportError as error:
            _finalize_fetch_failure(
                command_run_id,
                attempt,
                error,
                command_code=_CommandFailureCode.FETCH_FAILED,
            )
        except Exception:
            _finalize_fetch_failure(
                command_run_id,
                attempt,
                KamisTransportError("transport_internal_error"),
                command_code=_CommandFailureCode.FETCH_FAILED,
            )
        finally:
            del secret

        try:
            completed_fetch = complete_kamis_fetch(attempt.id, result)
        except Exception:
            _finalize_fetch_failure(
                command_run_id,
                attempt,
                KamisTransportError("transport_internal_error"),
                command_code=_CommandFailureCode.FETCH_PERSISTENCE_FAILED,
            )

        _log_fetch(
            command_run_id,
            completed_fetch.attempt,
            message_code="ingest.fetch.succeeded",
            lifecycle_event="FETCH_COMPLETED",
        )

        try:
            started_parse = start_or_get_kamis_parse_run(completed_fetch.artifact.id)
        except Exception:
            log_event(
                _LOGGER,
                "ERROR",
                "ingest.parse.start_failed",
                command_run_id=command_run_id,
                lifecycle_event="PARSE_START_FAILED",
            )
            raise CommandError(
                _failure_receipt(
                    _CommandFailureCode.PARSE_START_FAILED,
                    attempt=completed_fetch.attempt,
                )
            ) from None

        if started_parse.created:
            parse_start_message = "ingest.parse.started"
            parse_start_event = "PARSE_STARTED"
        elif started_parse.parse_run.status == ParseRun.Status.STARTED:
            parse_start_message = "ingest.parse.resumed"
            parse_start_event = "PARSE_RESUMED"
        else:
            parse_start_message = "ingest.parse.replay_started"
            parse_start_event = "PARSE_REPLAY_STARTED"
        _log_parse(
            command_run_id,
            started_parse.parse_run,
            message_code=parse_start_message,
            lifecycle_event=parse_start_event,
        )

        try:
            parsed = parse_recent_price_rows(
                result.rows,
                identity_registry=INITIAL_RETAIL_IDENTITY_REGISTRY,
            )
        except KamisParseError as error:
            _finalize_parse_failure(
                command_run_id,
                started_parse.parse_run,
                _parse_failure_code(error),
                command_code=_CommandFailureCode.PARSE_FAILED,
            )
        except Exception:
            _finalize_parse_failure(
                command_run_id,
                started_parse.parse_run,
                ParseGenerationFailureCode.SCHEMA_INVALID,
                command_code=_CommandFailureCode.PARSE_FAILED,
            )

        try:
            completed_parse = complete_kamis_parse_generation(started_parse.parse_run.id, parsed)
        except ParseGenerationError as error:
            _finalize_parse_failure(
                command_run_id,
                started_parse.parse_run,
                _generation_failure_code(error),
                command_code=_CommandFailureCode.GENERATION_FAILED,
            )
        except Exception:
            _finalize_parse_failure(
                command_run_id,
                started_parse.parse_run,
                ParseGenerationFailureCode.PERSISTENCE_FAILED,
                command_code=_CommandFailureCode.GENERATION_FAILED,
            )

        _log_parse(
            command_run_id,
            completed_parse.parse_run,
            message_code="ingest.parse.validated",
            lifecycle_event="PARSE_VALIDATED",
        )
        log_event(
            _LOGGER,
            "INFO",
            "ingest.command.succeeded",
            command_run_id=command_run_id,
            lifecycle_event="COMMAND_SUCCEEDED",
        )
        receipt = " ".join(
            (
                f"status={completed_parse.parse_run.status}",
                f"attempt_id={completed_fetch.attempt.id}",
                f"artifact_id={completed_fetch.artifact.id}",
                f"parse_run_id={completed_parse.parse_run.id}",
                f"pages={completed_fetch.attempt.received_page_count}",
                f"rows={parsed.input_row_count}",
                f"accepted={parsed.accepted_row_count}",
                f"out_of_scope={parsed.out_of_scope_row_count}",
                f"replayed={'yes' if completed_parse.replayed else 'no'}",
            )
        )
        self.stdout.write(receipt)
