"""Build an opt-in live KAMIS fixture in an empty disposable local database."""

from django.core.management.base import BaseCommand, CommandError

from scripts.live_api_e2e_smoke import LiveSmokeFailure, run_live_browser_fixture


class Command(BaseCommand):
    help = (
        "Fetch and normalize live KAMIS data, then test-publish it in an empty disposable "
        "local database for browser acceptance. It is not human-reviewed or production-activatable."
    )

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        try:
            receipt = run_live_browser_fixture()
        except LiveSmokeFailure as error:
            raise CommandError(f"status=FAIL stage={error.stage} code={error.code}") from None
        self.stdout.write(receipt.render())
