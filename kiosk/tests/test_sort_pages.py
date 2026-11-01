import re
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from coffee import services
from coffee.models import CoffeeSort, Member, NfcScan, Order
from kiosk.views import SORT_PAGES_ORDER, SORT_PAGES_SETTINGS, SORTS_WITHOUT_SCROLL


class SortPagesTestCase(TestCase):
    """Up to SORTS_WITHOUT_SCROLL sorts fit the order and settings screens as they are. With more,
    they come in pages, never a scrolling list (a swipe, then leaving the screen, crashed
    Chromium on the device): "For you" first - the favourite the order screen books after 20s is
    always in sight - then the rest, alphabetical."""

    def setUp(self):
        self.client = Client()
        self.client.get(reverse("kiosk:index"))

    def make_sorts(self, count):
        return [CoffeeSort.objects.create(name=f"Sort {i:02}", price=Decimal("0.40"), machine_strokes=1)
                for i in range(count)]

    def panel(self, mode="order"):
        self.client.get(reverse("kiosk:nav", args=[mode]))
        NfcScan.objects.create(uid="AABBCCDD", event=NfcScan.EVENT_INSERTED)
        return self.client.get(reverse("kiosk:panel")).content.decode()

    def names_in(self, content):
        return re.findall(r"<span>(Sort \d\d)</span>", content)

    def pages_in(self, content):
        """[(heading, [names], current?), ...] in the order they are rendered."""
        pages = re.findall(r'<div class="sort-page( current)?" data-page="\d+">\s*(?:<h2 class="sort-group">([^<]*)</h2>)?(.*?)</div>\s*</div>',
                           content, re.S)
        return [(heading or None, self.names_in(body), bool(current)) for current, heading, body in pages]

    def test_up_to_the_limit_one_alphabetical_grid(self):
        sorts = self.make_sorts(SORTS_WITHOUT_SCROLL)
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=sorts[-1])
        content = self.panel()
        self.assertNotIn("For you", content)
        self.assertNotIn("data-sort-page", content)
        self.assertEqual(self.names_in(content), [s.name for s in sorts])

    def test_beyond_it_the_likely_ones_come_first(self):
        sorts = self.make_sorts(SORTS_WITHOUT_SCROLL + 3)
        tom = Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=sorts[9])
        anna = Member.objects.create(uid=1, nickname="Anna B.")
        for sort, times in ((sorts[4], 3), (sorts[7], 1)):
            for _ in range(times):
                Order.objects.create(member=tom, coffee_sort=sort, value=Decimal("0.40"), machine_strokes=1)
        for _ in range(5):
            Order.objects.create(member=anna, coffee_sort=sorts[12], value=Decimal("0.40"), machine_strokes=1)
        pages = self.pages_in(self.panel())
        cells, for_you = SORT_PAGES_ORDER
        heading, first, current = pages[0]
        self.assertEqual((heading, current), ("For you", True))
        # favourite, Tom's own most ordered, then everyone's
        self.assertEqual(first[:4], [sorts[9].name, sorts[4].name, sorts[7].name, sorts[12].name])
        self.assertLessEqual(len(first), for_you)  # only what favourite and history give
        rest = [name for _, names, _ in pages[1:] for name in names]
        self.assertEqual(rest, sorted(rest))
        self.assertEqual(sorted(first + rest), [s.name for s in sorts])  # each sort once
        self.assertTrue(all(len(names) <= cells - 2 for _, names, _ in pages[1:]))
        self.assertEqual([current for *_, current in pages].count(True), 1)

    def test_long_lists_take_several_pages(self):
        self.make_sorts(30)
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.")
        content = self.panel()
        pages = self.pages_in(content)
        # no favourite, no history: no empty "For you" - 30 sorts, seven a page
        self.assertEqual([heading for heading, *_ in pages], [f"More sorts ({n} of 5)" for n in range(1, 6)])
        self.assertEqual(sum(len(names) for _, names, _ in pages), 30)
        self.assertTrue(pages[0][2])  # the first sort is preselected, and on the page shown
        self.assertEqual(content.count("‹ Back"), 4)

    def test_the_order_holds_for_the_whole_session(self):
        """Worked out once at the card: an order somebody else places meanwhile must not reorder
        the tiles - the poll would differ and swap the panel (Chromium's crash, CLAUDE.md)."""
        sorts = self.make_sorts(SORTS_WITHOUT_SCROLL + 1)
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.")
        first = self.panel()
        anna = Member.objects.create(uid=1, nickname="Anna B.")
        for _ in range(5):
            Order.objects.create(member=anna, coffee_sort=sorts[-1], value=Decimal("0.40"), machine_strokes=1)
        self.assertEqual(first, self.client.get(reverse("kiosk:panel")).content.decode())
        self.client.get(reverse("kiosk:nav", args=["order"]))  # logs out
        self.assertNotIn("sort_order", self.client.session)

    def test_settings_group_the_same_way(self):
        sorts = self.make_sorts(SORTS_WITHOUT_SCROLL + 1)
        Member.objects.create(uid=0xDDCCBBAA, nickname="Tom X.", favourite_coffee=sorts[5])
        content = self.panel("settings")
        pages = self.pages_in(content)
        self.assertEqual(pages[0][:2], ("For you", pages[0][1]))
        self.assertEqual(pages[0][1][0], sorts[5].name)
        self.assertTrue(all(len(names) <= SORT_PAGES_SETTINGS[0] - 2 for _, names, _ in pages[1:]))
        self.assertRegex(content, rf'value="{sorts[5].pk}"\s+checked')

    def test_preferred_sorts_without_a_member_or_orders(self):
        sorts = self.make_sorts(3)
        self.assertEqual(services.preferred_sorts(None, 6), [])
        Order.objects.create(member=Member.objects.create(uid=1, nickname="Anna B."), coffee_sort=sorts[1],
                             value=Decimal("0.40"), machine_strokes=1)
        self.assertEqual(services.preferred_sorts(None, 6), [sorts[1].pk])
