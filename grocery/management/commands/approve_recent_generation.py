"""Record one exact local Phase 0 approval over a validated recent generation."""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from grocery.management.local_phase0 import (
    LOCAL_APPROVAL_REASON_CODE,
    LocalPhase0Code,
    LocalPhase0Error,
    canonical_actor_id,
    get_local_operator,
    require_sha256,
    require_uuid,
)
from grocery.models import (
    FetchAttempt,
    ParseRun,
    ReviewDecision,
    SourceArtifact,
    SourceConfiguration,
    record_review_decision,
)


class Command(BaseCommand):
    help = "Approve one validated KAMIS recent-price generation for local Phase 0."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--parse-run-id", required=True)
        parser.add_argument("--decision-id", required=True)
        parser.add_argument("--acceptance-evidence-sha256", required=True)

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            parse_run_id = require_uuid(options.get("parse_run_id"))
            decision_id = require_uuid(options.get("decision_id"))
            acceptance_hash = require_sha256(options.get("acceptance_evidence_sha256"))
            decision, created, actor_id = self._approve(
                parse_run_id=parse_run_id,
                decision_id=decision_id,
                acceptance_hash=acceptance_hash,
            )
        except LocalPhase0Error as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            raise CommandError(f"code={LocalPhase0Code.REVIEW_FAILED.value}") from None

        self.stdout.write(
            " ".join(
                (
                    "status=APPROVED",
                    f"decision_id={decision.id}",
                    f"parse_run_id={decision.parse_run_id}",
                    f"artifact_id={decision.source_artifact_id}",
                    f"source_configuration_id={decision.source_configuration_id}",
                    f"actor_id={actor_id}",
                    f"created={'yes' if created else 'no'}",
                )
            )
        )

    @staticmethod
    @transaction.atomic
    def _approve(
        *,
        parse_run_id: uuid.UUID,
        decision_id: uuid.UUID,
        acceptance_hash: str,
    ) -> tuple[ReviewDecision, bool, int]:
        actor = get_local_operator(lock=True)
        try:
            parse_run = ParseRun.objects.select_for_update().get(pk=parse_run_id)
            artifact = SourceArtifact.objects.select_for_update().get(pk=parse_run.artifact_id)
        except ParseRun.DoesNotExist, SourceArtifact.DoesNotExist:
            raise LocalPhase0Error(LocalPhase0Code.GENERATION_INVALID) from None

        if (
            parse_run.status != ParseRun.Status.VALIDATED
            or parse_run.completed_at is None
            or not parse_run.result_hash
            or parse_run.quarantined_row_count != 0
            or parse_run.artifact_id != artifact.id
        ):
            raise LocalPhase0Error(LocalPhase0Code.GENERATION_INVALID)

        attempts = tuple(
            FetchAttempt.objects.select_for_update()
            .filter(artifact=artifact, state=FetchAttempt.State.SUCCEEDED)
            .order_by("source_configuration_id", "started_at", "id")
        )
        source_ids = {attempt.source_configuration_id for attempt in attempts}
        if len(source_ids) != 1:
            raise LocalPhase0Error(LocalPhase0Code.GENERATION_INVALID)
        source_id = next(iter(source_ids))
        try:
            source = SourceConfiguration.objects.select_for_update().get(pk=source_id)
        except SourceConfiguration.DoesNotExist:
            raise LocalPhase0Error(LocalPhase0Code.GENERATION_INVALID) from None
        if (
            source.state != SourceConfiguration.State.ACTIVE
            or source.publication_mode != SourceConfiguration.PublicationMode.RECENT_COMPARISON
            or artifact.source_identity != source.artifact_source_identity
            or not source.coverage_identity
            or not source.coverage_evidence_revision
        ):
            raise LocalPhase0Error(LocalPhase0Code.GENERATION_INVALID)

        try:
            decision, created = record_review_decision(
                decision_id=decision_id,
                actor=actor,
                decision=ReviewDecision.Decision.APPROVE,
                source_configuration_id=source.id,
                source_artifact_id=artifact.id,
                parse_run_id=parse_run.id,
                reconciliation_report_sha256=parse_run.result_hash,
                acceptance_evidence_sha256=acceptance_hash,
                reason_code=LOCAL_APPROVAL_REASON_CODE,
                approved_mode=SourceConfiguration.PublicationMode.RECENT_COMPARISON,
                approved_coverage_identity=source.coverage_identity,
                approved_coverage_evidence_revision=source.coverage_evidence_revision,
            )
        except Exception:
            raise LocalPhase0Error(LocalPhase0Code.REVIEW_FAILED) from None
        return decision, created, canonical_actor_id(actor)
