"""Bootstrap the fixed, local-only Phase 0 review and publication actor."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from grocery.management.local_phase0 import (
    LocalPhase0Code,
    LocalPhase0Error,
    bootstrap_local_operator,
    canonical_actor_id,
)


class Command(BaseCommand):
    help = "Create or validate the fixed local-only Phase 0 operator."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        try:
            actor, created = bootstrap_local_operator()
            actor_id = canonical_actor_id(actor)
        except LocalPhase0Error as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            raise CommandError(f"code={LocalPhase0Code.PERSISTENCE_FAILED.value}") from None

        self.stdout.write(
            " ".join(
                (
                    "status=READY",
                    f"actor_id={actor_id}",
                    f"created={'yes' if created else 'no'}",
                )
            )
        )
