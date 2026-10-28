from django.core.management.base import BaseCommand

from dashboard import tls


class Command(BaseCommand):
    help = (
        "Creates the device CA and Caddy's certificates if missing, renews those close to "
        "expiry or issued for other hostnames, and rewrites the Caddyfile (see dashboard/tls.py)."
    )

    def handle(self, *args, **options):
        done = tls.ensure()
        self.stdout.write("; ".join(done) if done else "TLS certificates up to date.")
