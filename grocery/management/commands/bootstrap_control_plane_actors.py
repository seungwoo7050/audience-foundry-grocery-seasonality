"""Bootstrap fixed actors for an externally authenticated private production job."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError, CommandParser

from grocery.management.control_plane import (
    ControlPlaneCode,
    ControlPlaneError,
    bootstrap_control_plane_actors,
)


class Command(BaseCommand):
    help = (
        "Create or validate fixed production control-plane actors. The flag is not "
        "authentication; run only behind external MFA/IAM and role-specific DB credentials."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--expected-release-sha", required=True)

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            actors = bootstrap_control_plane_actors(options.get("expected_release_sha"))
        except ControlPlaneError as error:
            raise CommandError(f"code={error.code.value}") from None
        except Exception:
            raise CommandError(f"code={ControlPlaneCode.PERSISTENCE_FAILED.value}") from None

        self.stdout.write(
            " ".join(
                (
                    "status=READY",
                    "review_actor=READY",
                    f"review_created={'yes' if actors.reviewer_created else 'no'}",
                    "publication_actor=READY",
                    f"publication_created={'yes' if actors.publisher_created else 'no'}",
                )
            )
        )
