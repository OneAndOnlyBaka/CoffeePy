from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry, CoffeeSort, Member, NfcScan, Order
from dashboard.models import KioskBoards


def boards(ranking, pillory):
    KioskBoards.objects.update_or_create(pk=1, defaults={"ranking": ranking, "pillory": pillory})


class KioskBoardsTestCase(TestCase):
    """The ranking and the pillory at the kiosk are the admin's decision (dashboard → System)."""

    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.client.get(reverse("kiosk:index"))

    def nav(self, mode):
        return self.client.get(reverse("kiosk:nav", args=[mode]), HTTP_HX_REQUEST="true")

    def test_a_new_device_shows_neither(self):
        self.assertEqual(KioskBoards.current().kiosk_views(), [])
        index = self.client.get(reverse("kiosk:index"))
        self.assertContains(index, 'data-mode="ranking"')
        self.assertRegex(index.content.decode(), r'data-mode="ranking"[^>]*\shidden>')
        self.assertContains(self.nav("ranking"), 'data-mode="order"')  # the tap changes nothing
        self.assertContains(self.client.get(reverse("kiosk:panel")), 'data-ranking="0"')

    def test_only_the_pillory(self):
        boards(False, True)
        panel = self.nav("ranking")
        self.assertContains(panel, 'data-ranking="1"')
        self.assertContains(panel, "pillory_empty.webm")  # straight to the pillory, the only tab
        self.assertNotContains(panel, "ranking-tabs")
        self.client.get(reverse("kiosk:ranking_view", args=["alltime"]))
        self.assertEqual(self.client.session["ranking_view"], "pillory")

    def test_only_the_ranking(self):
        boards(True, False)
        panel = self.nav("ranking")
        self.assertContains(panel, "ranking-tabs")
        self.assertNotContains(panel, reverse("kiosk:ranking_view", args=["pillory"]))

    def test_switched_off_while_on_screen(self):
        boards(True, True)
        self.nav("ranking")
        boards(False, False)
        panel = self.client.get(reverse("kiosk:panel"))
        self.assertContains(panel, 'data-mode="order"')
        self.assertContains(panel, 'data-ranking="0"')

    def test_no_podium_badge_without_the_ranking(self):
        member = Member.objects.create(uid=0xDDCCBBAA, nickname="Alice X.", favourite_coffee=self.espresso)
        Order.objects.create(member=member, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        for ranking, badge in ((True, True), (False, False)):
            with self.subTest(ranking=ranking):
                boards(ranking, False)
                self.nav("order")
                NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
                panel = self.client.get(reverse("kiosk:panel")).content.decode()
                self.assertEqual("rank-badge" in panel, badge)


class KioskBoardsSettingTestCase(TestCase):
    def test_saved_on_the_system_page_with_an_audit_row(self):
        User.objects.create_superuser(username="root", password="pw")
        client = Client()
        client.login(username="root", password="pw")
        response = client.post(reverse("dashboard:system_dashboard"), {"form": "boards", "pillory": "on"})
        self.assertRedirects(response, reverse("dashboard:system_dashboard"))
        self.assertEqual(KioskBoards.current().kiosk_views(), ["pillory"])
        self.assertTrue(AuditLogEntry.objects.filter(action="kiosk_boards.update",
                                                     details="ranking off, pillory on").exists())


class KioskBoardsMigrationTestCase(TestCase):
    """0005: a device in use keeps what it showed before; a new one starts with both off."""

    migration = import_module("dashboard.migrations.0005_kioskboards")

    def test_a_device_with_members_keeps_both(self):
        Member.objects.create(uid=1, nickname="Alice X.")
        KioskBoards.objects.all().delete()
        self.migration.keep_what_a_running_device_shows(apps, None)
        self.assertEqual(KioskBoards.current().kiosk_views(), ["alltime", "month", "pillory"])

    def test_a_new_device_starts_without(self):
        KioskBoards.objects.all().delete()
        self.migration.keep_what_a_running_device_shows(apps, None)
        self.assertFalse(KioskBoards.objects.exists())
