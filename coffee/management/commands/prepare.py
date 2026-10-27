from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "migrate, ensure_superuser and - with --tls - ensure_tls, in one process: the migrate "
        "service's whole job (docker-compose.yml). Django takes seconds to start on a Pi 3, and "
        "every container used to run these on its own at boot, all at once."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tls", action="store_true", help="also create/renew the certificates")

    def handle(self, *args, tls=False, **options):
        out = {"stdout": self.stdout, "stderr": self.stderr}
        call_command("migrate", interactive=False, verbosity=options["verbosity"], **out)
        call_command("ensure_superuser", **out)
        if tls:
            call_command("ensure_tls", **out)
