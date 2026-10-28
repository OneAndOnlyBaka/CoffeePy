from django.core.management import call_command
from django.core.management.base import BaseCommand

from coffee import clock
from dashboard import night


class Command(BaseCommand):
    help = (
        "The backup service's minutely round (docker-compose.yml): the clock check (coffee/clock.py), "
        "the kiosk's night schedule (dashboard/night.py), then a backup if one is due - in one process, because each Django start costs seconds on a Pi 3."
    )

    def handle(self, *args, **options):
        # The clock first: a backup is named and dated after it.
        try:
            correction = clock.check()
        except Exception as exc:  # noqa: BLE001 - a broken check must not stop the backups
            self.stderr.write(f"clock check failed: {exc}")
        else:
            if correction:
                self.stdout.write(f"clock put forward: {correction['reason']}")
        # Rewritten every round, so a restore or a lost file is back in step within a minute.
        try:
            night.write_file()
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(f"night schedule not written: {exc}")
        call_command("backup_db", if_due=True, stdout=self.stdout, stderr=self.stderr)
