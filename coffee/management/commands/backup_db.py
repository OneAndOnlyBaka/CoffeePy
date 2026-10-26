import time

from django.core.management.base import BaseCommand, CommandError

from coffee import backups
from coffee.models import BackupSettings
from dashboard import audit


class Command(BaseCommand):
    help = (
        "Backs the whole device up to its USB backup stick (set up in the dashboard) and thins "
        "out the older backups (coffee.backups.rotate). Without the stick it writes nothing - and "
        "says so in data/storage/backup-status.json, which the dashboard and the kiosk show."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Fail loudly instead of skipping when there is no stick (the dashboard's 'Back up now').",
        )
        parser.add_argument(
            "--if-due",
            action="store_true",
            help="Only when the interval set in the dashboard has passed since the last attempt "
            "(the backup service calls this every minute).",
        )

    def handle(self, *args, **options):
        automatic = not options["once"]
        if options["if_due"]:
            last = backups.read_backup_status().get("attempted", 0)
            if time.time() - last < BackupSettings.current().interval_minutes * 60:
                return
        directory = backups.own_backup_dir()
        if directory is None:
            message = "No backup stick plugged in - nothing backed up."
            if backups.configured_stick() is not None:
                backups.write_backup_status(error="", skipped=message)
                if automatic:
                    audit.log_actor("backup service", "backup.automatic", details=message, outcome="failure")
            if not automatic:
                raise CommandError(message)
            self.stdout.write(message)
            return
        try:
            target = backups.new_backup(directory)
        except (backups.BackupError, OSError) as exc:
            backups.write_backup_status(error=str(exc), skipped="")
            audit.log_actor("backup service", "backup.automatic", details=str(exc), outcome="failure")
            raise CommandError(str(exc)) from exc
        backups.write_backup_status(last_ok=target.stat().st_mtime, last_file=target.name, error="", skipped="")
        if automatic:  # "Back up now" is logged by the dashboard, with who did it
            audit.log_actor("backup service", "backup.automatic", target=target.name,
                            details="encrypted" if backups.passphrase() else "not encrypted")
        self.stdout.write(self.style.SUCCESS(f"Wrote backup to {target}"))
