from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from coffee.models import CoffeeSort, Member, MemberLinkToken

VERSION = "2.0.0-v2-rework.7"


@override_settings(COFFEEPY_VERSION=VERSION)
class VersionFooterTestCase(TestCase):
    """Whoever reports a problem can read off which build it was - dashboard, portal, kiosk."""

    def test_dashboard_navigation(self):
        User.objects.create_superuser(username="root", password="pw")
        client = Client()
        client.login(username="root", password="pw")
        # The sidebar and the phone's "More" sheet draw the same navigation.
        self.assertContains(client.get(reverse("dashboard:index")), f"CoffeePy {VERSION}", count=2)

    def test_phone_portal(self):
        member = Member.objects.create(uid=42, nickname="Alice X.")
        token = MemberLinkToken.objects.create(member=member, expires_at=timezone.now() + timedelta(minutes=5))
        client = Client()
        client.get(reverse("portal:link", args=[token.token]))
        self.assertContains(client.get(reverse("portal:index")), f"CoffeePy {VERSION}")

    def test_kiosk_admin_qr_codes(self):
        self.assertContains(Client().get(reverse("kiosk:qr_popup")), VERSION)

    def test_kiosk_setup_screen(self):
        client = Client()
        client.get(reverse("kiosk:index"))
        self.assertFalse(CoffeeSort.objects.exists())  # no sorts yet: the setup screen with its QR codes
        self.assertContains(client.get(reverse("kiosk:panel")), VERSION)
