from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format

from coffee.models import CoffeeSort, Member, NfcScan, Order
from dashboard.models import KioskBoards


class RankingBadgeTestCase(TestCase):
    def setUp(self):
        KioskBoards.objects.update_or_create(pk=1, defaults={"ranking": True, "pillory": True})
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=1, nickname="Alice X.")
        self.bob = Member.objects.create(uid=2, nickname="Bob X.")
        self.carol = Member.objects.create(uid=3, nickname="Carol X.")
        self.dave = Member.objects.create(uid=4, nickname="Dave X.")
        for member, strokes in ((self.alice, 10), (self.bob, 8), (self.carol, 6), (self.dave, 4)):
            Order.objects.create(
                member=member, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=strokes
            )
        self.client.get(reverse("kiosk:index"))
        self.client.get(reverse("kiosk:nav", args=["ranking"]))

    def podium(self, response):
        return {place: row["name"] if row else None for place, row in response.context["podium"]}

    def test_alltime_ranking_puts_the_top_three_on_the_podium(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertEqual(self.podium(response), {1: "Alice X.", 2: "Bob X.", 3: "Carol X."})
        self.assertNotContains(response, "Dave")
        self.assertContains(response, "podium.gif")
        self.assertContains(response, '<div class="podium-banner">All time</div>', html=False)

    def test_month_tab_names_the_month_in_the_banner(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        response = self.client.get(reverse("kiosk:ranking_view", args=["month"]), HTTP_HX_REQUEST="true")
        with translation.override("de"):
            month = date_format(timezone.localtime(), "F")
        self.assertContains(response, f'<div class="podium-banner">{month}</div>', html=False)
        self.assertContains(response, "vault.gif")  # still running - no podium yet
        self.assertNotContains(response, "podium.gif")
        self.assertEqual([place for place, _ in response.context["podium"]], [1, 2, 3])
        self.assertEqual(self.podium(response)[1], "Alice X.")

    def test_a_place_nobody_holds_stays_empty(self):
        Order.objects.exclude(member=self.alice).delete()
        self.assertEqual(self.podium(self.client.get(reverse("kiosk:panel"))), {1: "Alice X.", 2: None, 3: None})

    def test_ranking_screen_is_identical_from_poll_to_poll(self):
        # Otherwise panel_stable_swap.js swaps it in again, and the podium GIF restarts.
        for view in ("alltime", "pillory"):
            self.client.get(reverse("kiosk:ranking_view", args=[view]))
            first = self.client.get(reverse("kiosk:panel")).content
            self.assertEqual(first, self.client.get(reverse("kiosk:panel")).content, view)

    def test_ranking_shows_the_corner_buttons(self):
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "admin-btn")
        self.assertContains(response, "lang-btn")

    def test_ranking_follows_new_orders_while_the_screen_stays_open(self):
        # The rows are cached between polls (_ranking_rows) - a new order has to show up on
        # the very next one regardless.
        self.assertEqual(self.podium(self.client.get(reverse("kiosk:panel")))[1], "Alice X.")
        Order.objects.create(member=self.dave, coffee_sort=self.espresso, value=Decimal("3.00"), machine_strokes=20)
        self.assertEqual(self.podium(self.client.get(reverse("kiosk:panel")))[1], "Dave X.")

    def test_ranking_follows_a_renamed_member(self):
        self.client.get(reverse("kiosk:panel"))
        Member.objects.filter(pk=self.alice.pk).update(nickname="Alicia X.")
        self.assertContains(self.client.get(reverse("kiosk:panel")), "Alicia")

    def test_pillory_tab_lists_debtors_without_podium(self):
        Order.objects.create(
            member=self.dave,
            coffee_sort=self.espresso,
            value=Decimal("50.00"),
            machine_strokes=1,
            created_at=timezone.now() - timedelta(days=40),
        )

        self.client.get(reverse("kiosk:ranking_view", args=["pillory"]))
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "ranking/pillory.gif")
        self.assertNotContains(response, "podium.gif")
        self.assertContains(response, '<div class="podium-banner">Shame</div>', html=False)
        # in the ticker - twice, so the band runs on without a gap
        self.assertEqual(response.content.decode().count("<b>Dave X.</b>"), 2)

    def test_pillory_without_debtors_says_so(self):
        self.client.get(reverse("kiosk:ranking_view", args=["pillory"]))
        response = self.client.get(reverse("kiosk:panel"))
        self.assertContains(response, "Everyone has paid up!")
        self.assertNotContains(response, "ticker-track")
        # the executioner with nothing to do instead of the walk of shame
        self.assertContains(response, "ranking/pillory_empty.gif")
        self.assertContains(response, '<div class="podium-banner">No shame</div>', html=False)
        self.assertNotContains(response, "ranking/pillory.gif")

    def test_ranking_nav_opens_on_the_podium_again(self):
        self.client.get(reverse("kiosk:ranking_view", args=["pillory"]))
        self.client.get(reverse("kiosk:nav", args=["order"]))
        self.client.get(reverse("kiosk:nav", args=["ranking"]))
        self.assertContains(self.client.get(reverse("kiosk:panel")), '<div class="podium-banner">All time</div>', html=False)

    def test_unknown_tab_is_ignored(self):
        self.client.get(reverse("kiosk:ranking_view", args=["nonsense"]))
        self.assertContains(self.client.get(reverse("kiosk:panel")), "podium.gif")


class OrderScreenBadgeTestCase(TestCase):
    def setUp(self):
        KioskBoards.objects.update_or_create(pk=1, defaults={"ranking": True, "pillory": True})
        self.client = Client()
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.alice = Member.objects.create(uid=1, nickname="Alice X.", favourite_coffee=self.espresso)
        self.bob = Member.objects.create(uid=2, nickname="Bob X.", favourite_coffee=self.espresso)
        self.dave = Member.objects.create(uid=4, nickname="Dave X.", favourite_coffee=self.espresso)
        for member, strokes in ((self.alice, 10), (self.bob, 8)):
            Order.objects.create(
                member=member, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=strokes
            )
        self.client.get(reverse("kiosk:index"))

    def _log_in(self, uid_hex):
        NfcScan.objects.create(uid=uid_hex, event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel"))

    def test_top_scorer_sees_both_alltime_and_month_badges(self):
        response = self._log_in("01")  # Alice, uid=1
        self.assertContains(response, "medal-1")
        self.assertContains(response, 'title="Top 3 all-time"')
        self.assertContains(response, 'title="Top 3 this month"')

    def test_member_outside_top_three_sees_no_badge(self):
        response = self._log_in("04")  # Dave, uid=4, no orders at all
        self.assertNotContains(response, "rank-badge")

