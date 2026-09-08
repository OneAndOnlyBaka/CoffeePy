from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee import services
from coffee.models import CoffeeSort, Member, MemberCard, Order, Payment


class MemberMergeTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.espresso = CoffeeSort.objects.create(name="espresso", price=Decimal("0.30"), machine_strokes=1)
        self.old = Member.objects.create(uid=42, nickname="Alice-old")
        self.new = Member.objects.create(uid=999, nickname="Alice-new")
        Order.objects.create(member=self.old, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        Order.objects.create(member=self.new, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1)
        Payment.objects.create(member=self.new, value=Decimal("5.00"))

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard:member_merge"))
        self.assertEqual(response.status_code, 302)

    def test_picking_the_same_member_twice_shows_error(self):
        response = self.client.post(reverse("dashboard:member_merge"), {"keep": "42", "remove": "42"})
        self.assertContains(response, "two different accounts")

    def test_valid_pick_redirects_to_confirmation(self):
        response = self.client.post(reverse("dashboard:member_merge"), {"keep": "42", "remove": "999"})
        self.assertRedirects(response, reverse("dashboard:member_merge_confirm", args=[42, 999]))

    def test_confirm_page_shows_counts_and_alt_uid_plan(self):
        response = self.client.get(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertContains(response, "1 order")
        self.assertContains(response, "1 payment")
        self.assertContains(response, "link card 999")

    def test_confirming_moves_history_deletes_duplicate_and_links_the_old_card(self):
        response = self.client.post(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertContains(response, "Merge complete")

        self.assertFalse(Member.objects.filter(uid=999).exists())
        self.old.refresh_from_db()
        self.assertTrue(MemberCard.objects.filter(member=self.old, uid=999).exists())
        self.assertEqual(Order.objects.filter(member=self.old).count(), 2)
        self.assertEqual(Payment.objects.filter(member=self.old).count(), 1)

    def test_confirming_keeps_an_existing_alt_card_and_adds_the_new_one(self):
        # unlike the old single-valued alt_uid field, an existing alt card doesn't block
        # linking another one - a member can end up with several over time.
        MemberCard.objects.create(member=self.old, uid=1234)

        response = self.client.get(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertContains(response, "link card 999")

        self.client.post(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertFalse(Member.objects.filter(uid=999).exists())
        self.assertEqual(
            set(MemberCard.objects.filter(member=self.old).values_list("uid", flat=True)), {1234, 999}
        )

    def test_confirming_reparents_the_removed_members_own_alt_cards_too(self):
        # self.new (uid 999, about to be removed) already has its own alt card from an
        # earlier "Set alt card" - it must not get deleted along with self.new.
        MemberCard.objects.create(member=self.new, uid=7777)

        self.client.post(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertEqual(
            set(MemberCard.objects.filter(member=self.old).values_list("uid", flat=True)), {999, 7777}
        )

    def test_merged_member_no_longer_appears_twice_in_the_ranking(self):
        # self.old has 1 cup, self.new has 1 cup - two separate entries before the merge.
        ranking_before = [m.uid for m in services.ranking_all_time()]
        self.assertEqual(ranking_before.count(42), 1)
        self.assertEqual(ranking_before.count(999), 1)

        self.client.post(reverse("dashboard:member_merge_confirm", args=[42, 999]))

        ranking_after = [m.uid for m in services.ranking_all_time()]
        self.assertEqual(ranking_after.count(42), 1)
        self.assertNotIn(999, ranking_after)
        merged = Member.objects.get(uid=42)
        self.assertEqual(services.total_cups(merged), 2)  # combined, not counted separately

    def test_nickname_switches_to_whichever_card_was_more_recently_active(self):
        # self.new's Payment (set up in setUp) is the most recent activity of either
        # account, so "keep" (self.old, uid 42) should end up renamed to self.new's
        # nickname even though it was the account chosen to "keep".
        response = self.client.get(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertContains(response, "Alice-new")
        self.assertContains(response, "renamed")

        response = self.client.post(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertContains(response, "Alice-new")
        merged = Member.objects.get(uid=42)
        self.assertEqual(merged.nickname, "Alice-new")

    def test_nickname_stays_when_keep_was_more_recently_active(self):
        Order.objects.create(
            member=self.old, coffee_sort=self.espresso, value=Decimal("0.30"), machine_strokes=1,
            created_at=timezone.now() + timedelta(minutes=5),
        )

        response = self.client.get(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        self.assertNotContains(response, "renamed")

        self.client.post(reverse("dashboard:member_merge_confirm", args=[42, 999]))
        merged = Member.objects.get(uid=42)
        self.assertEqual(merged.nickname, "Alice-old")

    def test_nickname_stays_when_the_other_card_was_never_active(self):
        Member.objects.create(uid=555, nickname="Untouched")
        self.client.post(reverse("dashboard:member_merge_confirm", args=[42, 555]))
        merged = Member.objects.get(uid=42)
        self.assertEqual(merged.nickname, "Alice-old")
