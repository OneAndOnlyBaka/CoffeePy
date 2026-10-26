import os

from django.core.management.base import BaseCommand, CommandError

from coffee import backups
from dashboard import audit


class Command(BaseCommand):
    help = (
        "Backs the database up to this device's USB backup stick (set up in the dashboard), "
        "keeping the newest BACKUP_DEPTH backups. Without the stick it does nothing - and says "
        "so in data/storage/backup-status.json, which the dashboard shows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Fail loudly instead of skipping when there is no stick (the dashboard's 'Back up now').",
        )

    def handle(self, *args, **options):
        directory = backups.own_backup_dir()
        if directory is None:
            message = "No backup stick plugged in - nothing backed up."
            if backups.configured_stick() is not None:
                backups.write_backup_status(error="", skipped=message)
                if not options["once"]:
                    audit.log_actor("backup service", "backup.automatic", details=message, outcome="failure")
            if options["once"]:
                raise CommandError(message)
            self.stdout.write(message)
            return
        try:
            target = backups.new_backup(directory, keep=int(os.environ.get("BACKUP_DEPTH", "48")))
        except (backups.BackupError, OSError) as exc:
            backups.write_backup_status(error=str(exc), skipped="")
            audit.log_actor("backup service", "backup.automatic", details=str(exc), outcome="failure")
            raise CommandError(str(exc)) from exc
        backups.write_backup_status(last_ok=os.path.getmtime(target), last_file=target.name, error="", skipped="")
        if not options["once"]:  # "Back up now" is logged by the dashboard, with who did it
            audit.log_actor("backup service", "backup.automatic", target=target.name)
        self.stdout.write(self.style.SUCCESS(f"Wrote backup to {target}"))
