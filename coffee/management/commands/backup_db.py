import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Command(BaseCommand):
    help = (
        "pg_dump the database into a rotating set of SQL files under BACKUP_PATH, "
        "mirroring the old CoffeePy.ini [Backup] Enabled/Path/Interval/Depth options."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single backup immediately, ignoring BACKUP_ENABLED (used by the "
            "dashboard's 'Backup now' button).",
        )

    def handle(self, *args, **options):
        enabled = options["once"] or _env_bool("BACKUP_ENABLED", False)
        if not enabled:
            self.stdout.write("Backups disabled (BACKUP_ENABLED is not set); skipping.")
            return

        backup_path = os.environ.get("BACKUP_PATH")
        if not backup_path:
            raise CommandError("BACKUP_PATH is not set")

        depth = int(os.environ.get("BACKUP_DEPTH", "48"))
        counter_file = Path(backup_path) / ".backup_counter"
        counter = self._next_counter(counter_file, depth)

        target = Path(backup_path) / f"coffee_backup_{counter}.sql"
        target.parent.mkdir(parents=True, exist_ok=True)

        db = settings.DATABASES["default"]
        env = {**os.environ, "PGPASSWORD": db["PASSWORD"] or ""}
        cmd = [
            "pg_dump",
            "--host", db["HOST"] or "localhost",
            "--port", str(db["PORT"] or "5432"),
            "--username", db["USER"],
            "--no-password",
            "--format", "plain",
            "--file", str(target),
            db["NAME"],
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise CommandError(f"pg_dump failed: {result.stderr}")

        counter_file.write_text(str(counter))
        self.stdout.write(self.style.SUCCESS(f"Wrote backup to {target}"))

    def _next_counter(self, counter_file: Path, depth: int) -> int:
        try:
            last = int(counter_file.read_text().strip())
        except (OSError, ValueError):
            last = 0
        nxt = last + 1
        return 1 if nxt > depth else nxt
