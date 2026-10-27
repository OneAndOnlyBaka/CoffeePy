import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Creates the initial dashboard admin (a superuser) from DJANGO_SUPERUSER_* "
        "env vars, if set and no user with that username exists yet. Run on every "
        "start of the stack (the prepare command, see entrypoint.sh); idempotent."
    )

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if not username or not password:
            return
        if User.objects.filter(username=username).exists():
            return
        User.objects.create_superuser(
            username=username,
            email=os.environ.get("DJANGO_SUPERUSER_EMAIL", ""),
            password=password,
        )
        self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))
