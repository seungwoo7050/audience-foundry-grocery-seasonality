"""Seal one reviewed generation into a bounded local publication revision."""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from grocery.management.local_phase0 import (
    LocalPhase0Code,
    LocalPhase0Error,
    canonical_actor_id,
    get_local_operator,
    require_copy_revision,
    require_uuid,
)
from grocery.models import PublicationRevision, seal_recent_publication


class Command(BaseCommand):
    help = "Seal one approved recent-price generation for local Phase 0."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--decision-id", required=True)
        parser.add_argument("--public-copy-revision", required=True)

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            decision_id = require_uuid(options.get("decision_id"))
            copy_revision = require_copy_revision(options.get("public_copy_revision"))
            revision, created, actor_id = self._seal(
                decision_id=decision_id,
                copy_revision=copy_revision,
            )
        except LocalPhase0Error as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            raise CommandError(f"code={LocalPhase0Code.PUBLICATION_FAILED.value}") from None

        self.stdout.write(
            " ".join(
                (
                    "status=SEALED",
                    f"publication_id={revision.id}",
                    f"decision_id={revision.review_decision_id}",
                    f"parse_run_id={revision.generation_id}",
                    f"actor_id={actor_id}",
                    f"created={'yes' if created else 'no'}",
                )
            )
        )

    @staticmethod
    @transaction.atomic
    def _seal(
        *, decision_id: uuid.UUID, copy_revision: str
    ) -> tuple[PublicationRevision, bool, int]:
        actor = get_local_operator(lock=True)
        existing_ids = set(
            PublicationRevision.objects.select_for_update()
            .filter(
                review_decision_id=decision_id,
                public_copy_revision=copy_revision,
            )
            .values_list("id", flat=True)
        )
        try:
            revision = seal_recent_publication(decision_id, copy_revision)
        except Exception:
            raise LocalPhase0Error(LocalPhase0Code.PUBLICATION_FAILED) from None
        return revision, revision.id not in existing_ids, canonical_actor_id(actor)
