"""Record one human approval for a validated historical source collection."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from grocery.historical_collection_models import HistoricalSourceCollection
from grocery.historical_review_models import HistoricalCollectionReviewDecision
from grocery.historical_reviews import record_historical_review_decision
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

_NONE = "NONE"
_LOCAL_REASON = "LOCAL_HISTORICAL_COLLECTION_APPROVED"
_CONTROL_REASON = "CONTROL_PLANE_HISTORICAL_COLLECTION_APPROVED"


def _optional_uuid(value: object) -> uuid.UUID | None:
    return None if value == _NONE else require_uuid(value)


class Command(BaseCommand):
    help = (
        "Approve one validated historical collection. Production requires an "
        "external-MFA private job; the control-plane flag is not authentication."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--collection-id", required=True)
        parser.add_argument("--decision-id", required=True)
        parser.add_argument("--reconciliation-report-sha256", required=True)
        parser.add_argument("--acceptance-evidence-sha256", required=True)
        parser.add_argument("--supersedes-decision", default=_NONE)
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
            collection_id = require_uuid(options.get("collection_id"))
            decision_id = require_uuid(options.get("decision_id"))
            reconciliation_hash = require_sha256(options.get("reconciliation_report_sha256"))
            acceptance_hash = require_sha256(options.get("acceptance_evidence_sha256"))
            supersedes_id = _optional_uuid(options.get("supersedes_decision"))
            decision, created, actor_id = self._approve(
                collection_id=collection_id,
                decision_id=decision_id,
                reconciliation_hash=reconciliation_hash,
                acceptance_hash=acceptance_hash,
                supersedes_id=supersedes_id,
                expected_release_sha=expected_release_sha,
            )
        except ControlPlaneError as error:
            raise CommandError(f"code={error.code.value}") from None
        except LocalPhase0Error as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            code = (
                ControlPlaneCode.REVIEW_FAILED.value
                if production
                else LocalPhase0Code.REVIEW_FAILED.value
            )
            raise CommandError(f"code={code}") from None

        receipt = [
            "status=APPROVED",
            f"decision_id={decision.id}",
            f"collection_id={decision.collection_id}",
            f"collection_kind={decision.collection.kind}",
        ]
        if not production:
            receipt.append(f"actor_id={actor_id}")
        receipt.append(f"created={'yes' if created else 'no'}")
        self.stdout.write(" ".join(receipt))

    @staticmethod
    @transaction.atomic
    def _approve(
        *,
        collection_id: uuid.UUID,
        decision_id: uuid.UUID,
        reconciliation_hash: str,
        acceptance_hash: str,
        supersedes_id: uuid.UUID | None,
        expected_release_sha: object = None,
    ) -> tuple[HistoricalCollectionReviewDecision, bool, int]:
        authority = resolve_operation_actor(
            role="reviewer",
            expected_release_sha=expected_release_sha,
            lock=True,
        )
        collection = (
            HistoricalSourceCollection.objects.select_for_update()
            .select_related("source_configuration")
            .get(pk=collection_id)
        )
        decision, created = record_historical_review_decision(
            decision_id=decision_id,
            actor=authority.actor,
            collection_id=collection.id,
            decision=HistoricalCollectionReviewDecision.Decision.APPROVE,
            reconciliation_report_sha256=reconciliation_hash,
            acceptance_evidence_sha256=acceptance_hash,
            reason_code=_CONTROL_REASON if authority.production else _LOCAL_REASON,
            approved_result_sha256=collection.result_sha256,
            approved_partition_manifest_sha256=collection.partition_manifest_sha256,
            supersedes_id=supersedes_id,
        )
        return decision, created, authority.actor_id
