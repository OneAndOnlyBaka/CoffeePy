from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase


class PrepareTestCase(TestCase):
    """The migrate service's single process: migrate, the admin, and on request the certificates."""

    @mock.patch.dict("os.environ", {"DJANGO_SUPERUSER_USERNAME": "admin", "DJANGO_SUPERUSER_PASSWORD": "pw"})
    def test_admin_without_tls(self):
        with mock.patch("dashboard.tls.ensure") as ensure:
            call_command("prepare", verbosity=0, stdout=mock.Mock())
        self.assertTrue(User.objects.filter(username="admin", is_superuser=True).exists())
        ensure.assert_not_called()

    def test_tls_on_request(self):
        with mock.patch("dashboard.tls.ensure", return_value=[]) as ensure:
            call_command("prepare", "--tls", verbosity=0, stdout=mock.Mock())
        ensure.assert_called_once()
