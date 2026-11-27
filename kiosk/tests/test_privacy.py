"""The privacy notice at the kiosk (coffee/privacy.py): nothing is booked for a member who hasn't
confirmed it - no order, no payment, no name for a new card - and "no" stores nothing."""
import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee import privacy
from coffee.models import AuditLogEntry, CoffeeSort, Member, NfcReaderStatus, NfcScan, Order, Payment
from dashboard.models import KioskBoards, PrivacyContact


class KioskPrivacyTestCase(TestCase):
    def setUp(self):
        self.espresso = CoffeeSort.objects.create(name="Espresso", price=Decimal("0.40"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.member = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=self.espresso)
        self.client = Client()
        self.client.get(reverse("kiosk:index"))

    def panel(self):
        return self.client.get(reverse("kiosk:panel"))

    def tap(self, uid="AABBCCDD"):
        NfcScan.objects.create(uid=uid, event=NfcScan.EVENT_INSERTED)
        return self.panel()

    def test_no_order_before_the_notice_is_confirmed(self):
        notice = self.tap()
        self.assertContains(notice, "Your data on this coffee machine")
        self.assertContains(notice, privacy.DOCS_URL)
        self.assertContains(notice, "<svg")  # the QR code to it
        self.assertNotContains(notice, "Order now")
        self.assertEqual(notice.content, self.panel().content)  # the same bytes until it is answered
        self.client.post(reverse("kiosk:order_confirm"))
        with mock.patch("time.time", return_value=time.time() + 30):  # time to read: not the usual 20s
            self.assertContains(self.panel(), "Your data on this coffee machine")
        with mock.patch("time.time", return_value=time.time() + 61):  # no auto checkout either
            self.assertContains(self.panel(), "Apply your card")
        self.assertFalse(Order.objects.exists())

    def test_no_payment_before_the_notice(self):
        self.client.get(reverse("kiosk:nav", args=["info"]))
        self.tap()
        self.client.post(reverse("kiosk:pay"), {"amount": "5"})
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(self.client.get(reverse("kiosk:link_phone_popup")).status_code, 403)

    def test_confirmed_once_then_on_to_the_order(self):
        self.tap()
        order = self.client.post(reverse("kiosk:privacy_confirm"))
        self.assertContains(order, "Order now")
        self.member.refresh_from_db()
        self.assertEqual(self.member.privacy_version, privacy.VERSION)
        self.assertIsNotNone(self.member.privacy_confirmed_at)
        self.assertTrue(AuditLogEntry.objects.filter(action="member.privacy_confirm").exists())
        self.client.post(reverse("kiosk:order_confirm"))
        self.assertEqual(Order.objects.get().member, self.member)
        with mock.patch("time.time", return_value=time.time() + 30):
            self.assertNotContains(self.tap(), "Your data on this coffee machine")

    def test_a_new_version_is_asked_for_again(self):
        privacy.confirm(self.member)
        with mock.patch.object(privacy, "VERSION", privacy.VERSION + 1):
            self.assertContains(self.tap(), "Your data on this coffee machine")

    def test_no_logs_out_and_books_nothing(self):
        self.tap()
        self.assertContains(self.client.post(reverse("kiosk:privacy_decline")), "Apply your card")
        self.member.refresh_from_db()
        self.assertEqual(self.member.privacy_version, 0)
        self.assertTrue(AuditLogEntry.objects.filter(action="member.privacy_decline").exists())
        self.client.post(reverse("kiosk:order_confirm"))
        self.assertFalse(Order.objects.exists())

    def test_a_new_card_stores_nothing_until_confirmed(self):
        self.tap("11223344")
        self.client.post(reverse("kiosk:settings_save"), {"nickname": "Bea Y."})  # past the notice
        self.assertFalse(Member.objects.filter(nickname="Bea Y.").exists())
        self.client.post(reverse("kiosk:privacy_decline"))
        self.assertFalse(Member.objects.filter(uid=0x44332211).exists())
        with mock.patch("time.time", return_value=time.time() + 30):
            self.tap("11223344")
            self.assertContains(self.client.post(reverse("kiosk:privacy_confirm")), "Welcome, new card!")
            self.client.post(reverse("kiosk:settings_save"), {"nickname": "Bea Y."})
        bea = Member.objects.get(uid=0x44332211)
        self.assertTrue(privacy.confirmed(bea))

    def test_it_names_who_is_responsible_and_what_everyone_sees(self):
        self.assertContains(self.tap(), "ask the admin of this device")
        self.client.post(reverse("kiosk:privacy_decline"))
        PrivacyContact.objects.update_or_create(pk=1, defaults={"controller": "Anna B., room 2.14"})
        KioskBoards.objects.update_or_create(pk=1, defaults={"pillory": True})
        with mock.patch("time.time", return_value=time.time() + 30):
            notice = self.tap()
        self.assertContains(notice, "ask Anna B., room 2.14")
        self.assertContains(notice, "the pillory (who owes money)")
        self.assertNotContains(notice, "the ranking (who drinks the most)")
