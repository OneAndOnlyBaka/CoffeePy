import os

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase


class EnsureSuperuserTestCase(TestCase):
    def tearDown(self):
        for key in ("DJANGO_SUPERUSER_USERNAME", "DJANGO_SUPERUSER_PASSWORD", "DJANGO_SUPERUSER_EMAIL"):
            os.environ.pop(key, None)

    def test_does_nothing_without_env_vars(self):
        call_command("ensure_superuser")
        self.assertEqual(User.objects.count(), 0)

    def test_creates_superuser_from_env_vars(self):
        os.environ["DJANGO_SUPERUSER_USERNAME"] = "admin"
        os.environ["DJANGO_SUPERUSER_PASSWORD"] = "s3cret-enough"
        os.environ["DJANGO_SUPERUSER_EMAIL"] = "admin@example.com"

        call_command("ensure_superuser")

        user = User.objects.get(username="admin")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password("s3cret-enough"))

    def test_is_idempotent_when_user_already_exists(self):
        User.objects.create_superuser(username="admin", email="", password="original-pw")
        os.environ["DJANGO_SUPERUSER_USERNAME"] = "admin"
        os.environ["DJANGO_SUPERUSER_PASSWORD"] = "different-pw"

        call_command("ensure_superuser")

        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(User.objects.get(username="admin").check_password("original-pw"))
