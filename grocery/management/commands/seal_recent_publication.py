"""Seal one reviewed generation into a bounded local publication revision."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from grocery.management.control_plane import (
    ControlPlaneCode,
    ControlPlaneError,
    preflight_operation,
    resolve_operation_actor,
)
from grocery.management.local_phase0 import (
    LocalPhase0Code,
    LocalPhase0Error,
    require_copy_revision,
    require_uuid,
)
from grocery.models import PublicationRevision, seal_recent_publication


class Command(BaseCommand):
    help = (
        "Seal one approved recent-price generation. Production execution requires an "
        "external-MFA private job; the control-plane flag is not authentication."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--decision-id", required=True)
        parser.add_argument("--public-copy-revision", required=True)
        parser.add_argument("--expected-release-sha")

    def handle(self, *args: object, **options: object) -> None:
        del args
        expected_release_sha = options.get("expected_release_sha")
        production = (
            getattr(settings, "CONTROL_PLANE_OPERATIONS_ENABLED", False) is True
            or expected_release_sha is not None
        )
        try:
            if production:
                preflight_operation(expected_release_sha)
            decision_id = require_uuid(options.get("decision_id"))
            copy_revision = require_copy_revision(options.get("public_copy_revision"))
            if production:
                revision, created, actor_id = self._seal(
                    decision_id=decision_id,
                    copy_revision=copy_revision,
                    expected_release_sha=expected_release_sha,
                )
            else:
                revision, created, actor_id = self._seal(
                    decision_id=decision_id,
                    copy_revision=copy_revision,
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
            f"decision_id={revision.review_decision_id}",
            f"parse_run_id={revision.generation_id}",
        ]
        if not production:
            receipt.append(f"actor_id={actor_id}")
        receipt.append(f"created={'yes' if created else 'no'}")
        self.stdout.write(" ".join(receipt))

    @staticmethod
    @transaction.atomic
    def _seal(
        *,
        decision_id: uuid.UUID,
        copy_revision: str,
        expected_release_sha: object = None,
    ) -> tuple[PublicationRevision, bool, int]:
        authority = resolve_operation_actor(
            role="publisher",
            expected_release_sha=expected_release_sha,
            lock=True,
        )
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
            if authority.production:
                raise ControlPlaneError(ControlPlaneCode.PUBLICATION_FAILED) from None
            raise LocalPhase0Error(LocalPhase0Code.PUBLICATION_FAILED) from None
        return revision, revision.id not in existing_ids, authority.actor_id
