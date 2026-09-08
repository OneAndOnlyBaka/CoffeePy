import time
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import CoffeeSort, Member, NfcReaderStatus, NfcScan, Order


class ThankYouTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.tom = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom", favourite_coffee=self.espresso)
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
        self.assertContains(response, "Thank you, Tom!")
        self.assertContains(response, "thankyou_1.gif")
        self.assertContains(response, "Enjoy your coffee!")

    def test_auto_checkout_also_shows_thank_you(self):
        self._log_in()
        with mock.patch("time.time", return_value=time.time() + 25):
            response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Thank you, Tom!")
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
        alice = Member.objects.create(uid=42, nickname="Alice", favourite_coffee=self.espresso)
        self._log_in()
        self.client.post(reverse("kiosk:order_confirm"))

        self._scan("2A")  # uid_as_int() -> 42, i.e. Alice
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Hi Alice!")
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


class ThankYouWithoutANicknameTestCase(TestCase):
    """Members who are still just their card number get nagged about it on every order."""

    def setUp(self):
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        NfcReaderStatus.objects.create(pk=1, is_online=True)
        self.client.get(reverse("kiosk:index"))

    def order_as(self, nickname):
        Member.objects.create(uid=0xDDCCBBAA, nickname=nickname, favourite_coffee=self.espresso)
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        self.client.get(reverse("kiosk:panel"))  # logs in
        return self.client.post(reverse("kiosk:order_confirm"))

    def test_empty_nickname_gets_the_nickname_gif(self):
        response = self.order_as("")

        self.assertContains(response, "thankyou_no_nickname.gif")
        self.assertContains(response, "Set a nickname under Settings.")

    def test_numeric_nickname_gets_the_nickname_gif(self):
        # What the kiosk itself stores when someone leaves the nickname field empty.
        response = self.order_as(str(0xDDCCBBAA))

        self.assertContains(response, "thankyou_no_nickname.gif")

    def test_whitespace_only_nickname_gets_the_nickname_gif(self):
        response = self.order_as("   ")

        self.assertContains(response, "thankyou_no_nickname.gif")

    def test_a_real_nickname_gets_the_usual_tier(self):
        response = self.order_as("Tom")

        self.assertNotContains(response, "thankyou_no_nickname.gif")
        self.assertContains(response, "thankyou_1.gif")

    def test_a_nickname_with_digits_in_it_is_still_a_real_name(self):
        response = self.order_as("C3PO")

        self.assertNotContains(response, "thankyou_no_nickname.gif")

    def test_the_nag_beats_the_weekly_override(self):
        member = Member.objects.create(uid=42, nickname="")
        for _ in range(101):
            Order.objects.create(member=member, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        NfcScan.objects.create(uid="2A", event=NfcScan.EVENT_INSERTED)  # uid_as_int() -> 42
        self.client.get(reverse("kiosk:panel"))

        response = self.client.post(reverse("kiosk:order_confirm"))

        self.assertContains(response, "thankyou_no_nickname.gif")
        self.assertNotContains(response, "thankyou_week100.gif")
