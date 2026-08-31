"""Seal three independently reviewed historical collections."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from grocery.historical_publication_models import HistoricalRetailPublicationRevision
from grocery.historical_publications import seal_historical_publication
from grocery.management.control_plane import (
    ControlPlaneCode,
    ControlPlaneError,
    preflight_operation,
    resolve_operation_actor,
)
from grocery.management.local_phase0 import (
    LocalPhase0Code,
    LocalPhase0Error,
    require_sha256,
    require_uuid,
)


class Command(BaseCommand):
    help = (
        "Seal one exact historical retail bundle. Production requires an external-MFA "
        "private job; the control-plane flag is not authentication."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--monthly-review-id", required=True)
        parser.add_argument("--regional-review-id", required=True)
        parser.add_argument("--market-review-id", required=True)
        parser.add_argument("--compatibility-report-sha256", required=True)
        parser.add_argument("--expected-release-sha")

    def handle(self, *args: object, **options: object) -> None:
        del args
        expected_release_sha = options.get("expected_release_sha")
        production = (
            getattr(settings, "CONTROL_PLANE_OPERATIONS_ENABLED", False) is True
            or expected_release_sha is not None
        )
        try:
            preflight_operation(expected_release_sha)
            revision, created, actor_id = self._seal(
                monthly_review_id=require_uuid(options.get("monthly_review_id")),
                regional_review_id=require_uuid(options.get("regional_review_id")),
                market_review_id=require_uuid(options.get("market_review_id")),
                compatibility_hash=require_sha256(
                    options.get("compatibility_report_sha256")
                ),
                expected_release_sha=expected_release_sha,
            )
        except ControlPlaneError as error:
            raise CommandError(f"code={error.code.value}") from None
        except LocalPhase0Error as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            code = (
                ControlPlaneCode.PUBLICATION_FAILED.value
                if production
                else LocalPhase0Code.PUBLICATION_FAILED.value
            )
            raise CommandError(f"code={code}") from None

        receipt = [
            "status=SEALED",
            f"publication_id={revision.id}",
            f"public_copy_revision={revision.public_copy_revision}",
        ]
        if not production:
            receipt.append(f"actor_id={actor_id}")
        receipt.append(f"created={'yes' if created else 'no'}")
        self.stdout.write(" ".join(receipt))

    @staticmethod
    @transaction.atomic
    def _seal(
        *,
        monthly_review_id: uuid.UUID,
        regional_review_id: uuid.UUID,
        market_review_id: uuid.UUID,
        compatibility_hash: str,
        expected_release_sha: object = None,
    ) -> tuple[HistoricalRetailPublicationRevision, bool, int]:
        authority = resolve_operation_actor(
            role="publisher",
            expected_release_sha=expected_release_sha,
            lock=True,
        )
        existing_ids = set(
            HistoricalRetailPublicationRevision.objects.select_for_update()
            .filter(
                monthly_review_id=monthly_review_id,
                regional_review_id=regional_review_id,
                market_review_id=market_review_id,
                public_copy_revision=HistoricalRetailPublicationRevision.COPY_REVISION,
            )
            .values_list("id", flat=True)
        )
        revision = seal_historical_publication(
            monthly_review_id=monthly_review_id,
            regional_review_id=regional_review_id,
            market_review_id=market_review_id,
            compatibility_report_sha256=compatibility_hash,
        )
        return revision, revision.id not in existing_ids, authority.actor_id
