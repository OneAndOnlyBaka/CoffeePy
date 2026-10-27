import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry, CoffeeSort, Member, NfcReaderStatus, NfcScan, Order


class ThankYouTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.tom = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=self.espresso)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.client.get(reverse("kiosk:index"))

    def _scan(self, uid, event=NfcScan.EVENT_INSERTED):
        NfcScan.objects.create(uid=uid, event=event)

    def _log_in(self):
        self._scan("AABBCCDD")
        self.client.get(reverse("kiosk:panel"))  # logs in

    def test_order_confirm_shows_thank_you_with_first_tier_gif(self):
        self._log_in()
        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(response, "Thank you, Tom X.!")
        self.assertContains(response, "thankyou_1.gif")
        self.assertContains(response, "Enjoy your coffee!")

    def test_auto_checkout_also_shows_thank_you(self):
        self._log_in()
        with mock.patch("time.time", return_value=time.time() + 25):
            response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Thank you, Tom X.!")
        self.assertEqual(Order.objects.count(), 1)

    def test_cancel_does_not_show_thank_you(self):
        self._log_in()
        response = self.client.post(reverse("kiosk:order_cancel"))
        self.assertNotContains(response, "Thank you")
        self.assertContains(response, "Apply your card")

    def test_thank_you_reverts_to_wait_for_card_after_20s(self):
        self._log_in()
        self.client.post(reverse("kiosk:order_confirm"))
        with mock.patch("time.time", return_value=time.time() + 21):
            response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Apply your card")
        self.assertNotContains(response, "Thank you")

    def test_polls_during_the_interlude_return_identical_html(self):
        """panel_stable_swap.js skips the swap while the panel HTML doesn't change, and
        only an untouched <img> plays its gif through instead of restarting at frame 0 on
        every 500ms poll. Anything varying per render here (a csrf token, a countdown)
        would silently bring the restarting back."""
        self._log_in()
        self.client.post(reverse("kiosk:order_confirm"))

        first = self.client.get(reverse("kiosk:panel"))
        second = self.client.get(reverse("kiosk:panel"))

        self.assertEqual(first.content, second.content)

    def test_panel_html_has_no_leftover_template_syntax(self):
        """A multi-line {# #} is not a comment to Django's lexer - it renders as literal
        text on the kiosk screen."""
        self._log_in()
        response = self.client.post(reverse("kiosk:order_confirm"))

        for leftover in (b"{#", b"#}", b"{%", b"%}"):
            self.assertNotIn(leftover, response.content)

    def test_a_different_members_card_interrupts_thank_you_immediately(self):
        alice = Member.objects.create(uid=42, nickname="Alice X.", favourite_coffee=self.espresso)
        self._log_in()
        self.client.post(reverse("kiosk:order_confirm"))

        self._scan("2A")  # uid_as_int() -> 42, i.e. Alice
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Hi Alice X.!")
        self.assertNotContains(response, "Thank you")

    def test_gif_tier_escalates_with_cups_ordered_today(self):
        # 3 cups already today (espresso = 1 machine stroke each) -> 4th cup lands in the
        # 4-5 tier.
        for _ in range(3):
            Order.objects.create(member=self.tom, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)

        self._log_in()
        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(response, "thankyou_4_5.gif")
        self.assertContains(response, "on a roll today!")

    def test_gif_tier_warns_at_six_to_eight_cups(self):
        for _ in range(5):
            Order.objects.create(member=self.tom, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)

        self._log_in()
        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(response, "thankyou_6_8.gif")
        self.assertContains(response, "Whoa, pace yourself!")

    def test_gif_tier_above_eight_cups(self):
        for _ in range(8):
            Order.objects.create(member=self.tom, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)

        self._log_in()
        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(response, "thankyou_9plus.gif")
        self.assertContains(response, "Certified caffeine junkie today!")

    def test_week_override_beats_the_daily_tier_above_100_cups_this_week(self):
        for _ in range(100):
            Order.objects.create(member=self.tom, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)

        self._log_in()
        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertContains(response, "thankyou_week100.gif")
        self.assertContains(response, "100+ cups this week")

    def test_no_week_override_at_or_below_100_cups_this_week(self):
        for _ in range(99):
            Order.objects.create(member=self.tom, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)

        self._log_in()
        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertNotContains(response, "thankyou_week100.gif")


class NameRequiredTestCase(TestCase):
    """Whoever has no usable name (coffee/names.py) - still their card number, a placeholder,
    or just not "First L." - is asked for one before the order, and then gets the order."""

    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.client.get(reverse("kiosk:index"))

    def tap_as(self, nickname):
        Member.objects.create(uid=0xDDCCBBAA, nickname=nickname, favourite_coffee=self.espresso)
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel"))

    def test_no_order_without_a_usable_name(self):
        for nickname in ("", "   ", str(0xDDCCBBAA), "Tom", "test X.", "Asdf G."):
            with self.subTest(nickname=nickname):
                Member.objects.all().delete()
                self.client.get(reverse("kiosk:nav", args=["order"]))
                response = self.tap_as(nickname)
                self.assertContains(response, "Who are you?")
                self.assertContains(response, 'like "Anna B."')
                self.client.post(reverse("kiosk:order_confirm"))
                self.assertFalse(Order.objects.exists())

    def test_a_proper_name_goes_straight_on_to_the_order(self):
        self.tap_as("Tom")
        response = self.client.post(reverse("kiosk:settings_save"), {"nickname": "tom k", "favourite_coffee_id": self.espresso.pk})
        self.assertEqual(Member.objects.get(uid=0xDDCCBBAA).nickname, "Tom K.")  # put right, not refused
        self.assertContains(response, "Hi Tom K.!")
        self.client.post(reverse("kiosk:order_confirm"))
        self.assertEqual(Order.objects.count(), 1)

    def test_a_refused_name_says_why_and_keeps_what_was_typed(self):
        Member.objects.create(uid=1, nickname="Tom K.")
        self.tap_as("")
        for typed, reason in (("", "Please enter your name."), ("Tom", 'like "Anna B."'),
                              ("tom k.", "Somebody already goes by that name"),
                              ("Aaaron X.", "doesn&#x27;t look like a first name")):
            with self.subTest(typed=typed):
                response = self.client.post(reverse("kiosk:settings_save"), {"nickname": typed})
                self.assertContains(response, reason)
                self.assertContains(response, "Who are you?")  # still logged in, still on the name
        self.assertEqual(Member.objects.get(uid=0xDDCCBBAA).nickname, "")

    def test_a_name_that_only_lacks_its_form_is_put_right_without_asking(self):
        response = self.tap_as("anna b")
        self.assertContains(response, "Hi Anna B.!")
        self.assertEqual(Member.objects.get(uid=0xDDCCBBAA).nickname, "Anna B.")
        self.assertTrue(AuditLogEntry.objects.filter(details="name put into the usual form at the kiosk").exists())

    def test_the_old_name_is_the_start_of_the_new_one(self):
        self.assertContains(self.tap_as("Anna"), 'value="Anna "')

    def test_a_real_name_orders_as_before(self):
        self.assertContains(self.tap_as("Tom X."), "Hi Tom X.!")
        response = self.client.post(reverse("kiosk:order_confirm"))
        self.assertNotContains(response, "thankyou_no_nickname.gif")
        self.assertContains(response, "thankyou_1.gif")

    def test_a_name_taken_away_mid_order_still_gets_the_nag(self):
        # An admin renames someone while they stand at the kiosk - rare, but the nag beats
        # the weekly override then.
        member = Member.objects.create(uid=42, nickname="Tom X.")
        for _ in range(101):
            Order.objects.create(member=member, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        NfcScan.objects.create(uid="2A", event=NfcScan.EVENT_INSERTED)  # uid_as_int() -> 42
        self.client.get(reverse("kiosk:panel"))
        Member.objects.filter(pk=42).update(nickname="")

        response = self.client.post(reverse("kiosk:order_confirm"))

        self.assertContains(response, "thankyou_no_nickname.gif")
        self.assertNotContains(response, "thankyou_week100.gif")
