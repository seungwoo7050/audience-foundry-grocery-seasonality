"""Run the explicit disposable live KAMIS source-to-SSR assurance loop."""

from django.core.management.base import BaseCommand, CommandError

from scripts.live_api_e2e_smoke import LiveSmokeFailure, run_live_api_e2e_smoke


class Command(BaseCommand):
    help = "Run the opt-in live KAMIS E2E smoke against an empty disposable database."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        try:
            receipt = run_live_api_e2e_smoke()
        except LiveSmokeFailure as error:
            raise CommandError(f"status=FAIL stage={error.stage} code={error.code}") from None
        self.stdout.write(receipt.render())
