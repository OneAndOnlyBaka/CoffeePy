from django.test import TestCase
from django.utils import timezone

from coffee import names
from coffee.models import Member


class NameRulesTestCase(TestCase):
    """coffee/names.py: first name and the start of the last name, nothing made up, no twins."""

    def test_normalize_puts_right_what_a_touchscreen_gets_slightly_wrong(self):
        for raw, expected in (("anna b", "Anna B."), ("  anna   b.  ", "Anna B."), ("ANNA B", "Anna B."),
                              ("anna-lena b", "Anna-Lena B."), ("anna lena be", "Anna Lena Be."),
                              ("McKenzie b", "McKenzie B."), ("jürgen ö", "Jürgen Ö."), ("Tom", "Tom"), ("", "")):
            with self.subTest(raw=raw):
                self.assertEqual(names.normalize(raw), expected)

    def test_what_passes(self):
        for name in ("Anna B.", "Anna Be.", "Anna-Lena B.", "Anna Lena B.", "Jürgen Ö.", "Otto M.", "Lili K.",
                     "Ali A.", "McKenzie B."):
            with self.subTest(name=name):
                self.assertIsNone(names.problem(name, extra_blocked=[]))

    def test_what_does_not(self):
        for name, problem in (("", "empty"), ("   ", "empty"), ("3721941674", "format"), ("Tom", "format"),
                              ("T B.", "format"), ("Tom B", "format"), ("Tom Bauer", "format"), ("tom b.", "format"),
                              ("Anna Lena Maria Theresa B.", "too_long"), ("Test B.", "made_up"),
                              ("Kaffee K.", "made_up"), ("Aaaron B.", "made_up"), ("Asdf G.", "made_up"),
                              ("Qwertz Q.", "made_up"), ("Hans-Nobody B.", "made_up")):
            with self.subTest(name=name):
                self.assertEqual(names.problem(name, extra_blocked=[]), problem)

    def test_extra_blocked_names(self):
        self.assertEqual(names.problem("Chef B.", extra_blocked=["chef"]), "made_up")

    def test_no_two_active_members_with_one_name(self):
        anna = Member.objects.create(uid=1, nickname="Anna B.")
        self.assertEqual(names.problem("Anna B.", extra_blocked=[]), "taken")
        self.assertIsNone(names.problem("Anna B.", member=anna, extra_blocked=[]))  # her own name
        self.assertIsNone(names.problem("Anna Be.", extra_blocked=[]))
        Member.objects.filter(pk=1).update(departed_at=timezone.now())
        self.assertIsNone(names.problem("Anna B.", extra_blocked=[]))  # a departed member's name is free again

    def test_member_name_problem(self):
        self.assertEqual(Member.objects.create(uid=5, nickname="5").name_problem, "format")
        self.assertIsNone(Member.objects.create(uid=6, nickname="Ben C.").name_problem)


class WhatTheLegacyNamesTaughtTestCase(TestCase):
    """The legacy database: bare first names, "Anna B" without the full stop, short codes."""

    def test_short_codes(self):
        for name in ("AB", "TMK", "ABCD", "ÄÖ"):
            with self.subTest(name=name):
                self.assertEqual(names.normalize(name), name)
                self.assertIsNone(names.problem(name, extra_blocked=[]))
        for name, problem in (("A", "format"), ("ABCDE", "format"), ("A9", "format"), ("ab", "format"),
                              ("XXX", "made_up"), ("ASDF", "made_up"), ("ICH", "made_up"), ("ABC", "made_up")):
            with self.subTest(name=name):
                self.assertEqual(names.problem(names.normalize(name), extra_blocked=[]), problem)
        Member.objects.create(uid=1, nickname="AB")
        self.assertEqual(names.problem("AB", extra_blocked=[]), "taken")

    def test_a_name_that_only_lacks_its_form_is_fixed_not_asked_for(self):
        self.assertEqual(names.fixable(Member.objects.create(uid=1, nickname="Anna B")), "Anna B.")
        self.assertEqual(names.fixable(Member.objects.create(uid=2, nickname="ben k.")), "Ben K.")
        self.assertIsNone(names.fixable(Member.objects.create(uid=3, nickname="Clara")))  # needs her initial
        self.assertIsNone(names.fixable(Member.objects.create(uid=4, nickname="Anna B.")))  # nothing to fix

    def test_suggestions(self):
        for old, expected in (("Anna", "Anna "), ("anna", "Anna "), ("Max Mustermann", "Max M."),
                              ("anna-lena", "Anna-Lena "), ("3721941674", ""), ("", ""), ("X999", ""),
                              ("Tom 2", "Tom ")):
            with self.subTest(old=old):
                self.assertEqual(names.suggestion(old), expected)
