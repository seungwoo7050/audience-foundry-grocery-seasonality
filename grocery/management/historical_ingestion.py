"""Shared fail-closed command shell for three historical source families."""

from __future__ import annotations

import re
import uuid
from abc import abstractmethod

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from grocery.historical_ingestion_workflow import (
    HistoricalIngestionError,
    ingest_historical_collection,
)
from grocery.historical_registry import load_historical_dimension_registry
from grocery.source.client import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, KamisHttpClient
from grocery.source.historical_client import prepare_historical_request
from grocery.source.historical_contract import (
    HistoricalContractError,
    HistoricalDataset,
    HistoricalPriceQuery,
)
from grocery.source.secrets import SecretLoadError, load_kamis_api_key

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _uuid(value: object) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):  # fmt: skip
        raise CommandError("code=HISTORICAL_INGEST_UUID_INVALID") from None


def _sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CommandError("code=HISTORICAL_INGEST_MANIFEST_INVALID")
    return value


def _page_size(value: object) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):  # fmt: skip
        raise CommandError("code=HISTORICAL_INGEST_PAGE_SIZE_INVALID") from None
    if parsed < 1 or parsed > MAX_PAGE_SIZE:
        raise CommandError("code=HISTORICAL_INGEST_PAGE_SIZE_INVALID")
    return parsed


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class HistoricalIngestionCommand(BaseCommand):
    dataset: HistoricalDataset

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--collection-id", required=True)
        parser.add_argument("--source-configuration-id", required=True)
        parser.add_argument("--code-manifest-sha256", required=True)
        parser.add_argument("--start", required=True)
        parser.add_argument("--end", required=True)
        parser.add_argument("--category-code", required=True)
        parser.add_argument("--item-code")
        parser.add_argument("--variety-code")
        parser.add_argument("--grade-code")
        parser.add_argument("--region-code", action="append")
        parser.add_argument("--page-size", default=DEFAULT_PAGE_SIZE)

    @abstractmethod
    def build_queries(self, options: dict[str, object]) -> tuple[HistoricalPriceQuery, ...]:
        raise NotImplementedError

    @staticmethod
    def query(options: dict[str, object], *, region_code: str | None) -> HistoricalPriceQuery:
        return HistoricalPriceQuery(
            start=str(options["start"]),
            end=str(options["end"]),
            category_code=str(options["category_code"]),
            item_code=_optional_text(options.get("item_code")),
            variety_code=_optional_text(options.get("variety_code")),
            grade_code=_optional_text(options.get("grade_code")),
            region_code=region_code,
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        collection_id = _uuid(options.get("collection_id"))
        source_id = _uuid(options.get("source_configuration_id"))
        manifest = _sha256(options.get("code_manifest_sha256"))
        page_size = _page_size(options.get("page_size"))
        try:
            queries = self.build_queries(options)
            if not queries or len(queries) > 100:
                raise HistoricalContractError("invalid_historical_partition_count")
            for query in queries:
                prepare_historical_request(self.dataset, query)
            load_historical_dimension_registry(manifest)
        except (HistoricalContractError, ValidationError):  # fmt: skip
            raise CommandError("code=HISTORICAL_INGEST_CONTRACT_INVALID") from None

        try:
            secret = load_kamis_api_key()
        except SecretLoadError:
            raise CommandError("code=HISTORICAL_INGEST_SECRET_UNAVAILABLE") from None
        except Exception:
            raise CommandError("code=HISTORICAL_INGEST_SECRET_UNAVAILABLE") from None
        try:
            outcome = ingest_historical_collection(
                collection_id=collection_id,
                source_configuration_id=source_id,
                dataset=self.dataset,
                queries=queries,
                code_manifest_sha256=manifest,
                service_key=secret.reveal(),
                client=KamisHttpClient(),
                page_size=page_size,
            )
        except HistoricalIngestionError as error:
            raise CommandError(f"code=HISTORICAL_INGEST_{error.code}") from None
        except Exception:
            raise CommandError("code=HISTORICAL_INGEST_FAILED") from None
        finally:
            del secret
        self.stdout.write(
            " ".join(
                (
                    "status=VALIDATED",
                    f"collection_id={outcome.collection.id}",
                    f"partitions={outcome.partition_count}",
                    f"rows={outcome.accepted_row_count}",
                )
            )
        )


def region_scopes(options: dict[str, object], *, required: bool) -> tuple[str | None, ...]:
    raw = options.get("region_code")
    regions = (
        tuple(value for value in raw if isinstance(value, str))
        if isinstance(raw, list)
        else ()
    )
    if required and not regions:
        raise HistoricalContractError("missing_historical_region")
    return regions or (None,)
