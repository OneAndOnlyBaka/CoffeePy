from django.core.management.base import BaseCommand

from dashboard import audit


class Command(BaseCommand):
    help = (
        "Collects the host's events into the audit log (data/storage/events.jsonl) and deletes "
        "entries older than AUDIT_RETENTION_DAYS (default 90). Run by the backup service."
    )

    def handle(self, *args, **options):
        collected = audit.ingest_host_events()
        pruned = audit.prune()
        if pruned:
            audit.log_actor("system", "audit.prune", details=f"{pruned} entries older than the retention period")
        self.stdout.write(f"collected {collected} host event(s), deleted {pruned} old entr(y/ies)")
