import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

DOCS = Path(settings.BASE_DIR) / "docs"
README = Path(settings.BASE_DIR) / "README.md"

# Bilingual on its own screen, so it is captured once (capture_tui.sh) and only the German manual shows it.
GERMAN_ONLY_PICTURES = {"provision-language.png"}

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
PICTURE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def headings(text, level):
    return [line for line in text.splitlines() if line.startswith("#" * level + " ")]


def anchor(heading):
    """GitHub's slug: lower case, punctuation dropped (letters with umlauts stay), spaces to dashes."""
    text = heading.lstrip("#").strip().lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(path):
    seen, result = {}, set()
    in_code = False
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code or not re.match(r"#{1,6} ", line):
            continue
        slug = anchor(line)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        result.add(slug if n == 0 else f"{slug}-{n}")
    return result


class ManualParityTestCase(SimpleTestCase):
    """The manuals and the member sheets exist in German and English, section for section. A
    passage added to one only is how the two drifted before - this is the check a reviewer can't
    do by eye across a thousand lines each."""

    PAIRS = (("MANUAL.de.md", "MANUAL.en.md"), ("MEMBERS.de.md", "MEMBERS.en.md"))

    def test_same_chapters_and_sections(self):
        for de, en in self.PAIRS:
            de_text, en_text = (DOCS / de).read_text(), (DOCS / en).read_text()
            for level in (2, 3):
                self.assertEqual(len(headings(de_text, level)), len(headings(en_text, level)),
                                 f"{de}/{en}: different number of {'#' * level} sections")

    def test_same_screenshots_in_the_same_order(self):
        def pictures(name, language):
            return [Path(p).name for p in PICTURE.findall((DOCS / name).read_text())
                    if f"manual/{language}/" in p]
        de = [p for p in pictures("MANUAL.de.md", "de") if p not in GERMAN_ONLY_PICTURES]
        self.assertEqual(de, pictures("MANUAL.en.md", "en"))

    def test_the_member_sheet_carries_no_numbers(self):
        """Timings, durations and limits live in the manual - a copy here would drift from it."""
        for name in ("MEMBERS.de.md", "MEMBERS.en.md"):
            body = re.sub(r"\]\([^)]*\)", "]", (DOCS / name).read_text())  # link targets have "#4-..."
            body = re.sub(r"^\d+\. ", "", body, flags=re.M)  # the steps' list numbers
            self.assertEqual(re.findall(r"\d+", body), [], name)


class DocLinksTestCase(SimpleTestCase):
    """Every relative link, picture and #anchor in docs/ and the README points at something."""

    def test_links_and_anchors_resolve(self):
        broken = []
        for path in [README, *sorted(DOCS.glob("*.md"))]:
            text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
            for target in LINK.findall(text) + PICTURE.findall(text):
                if re.match(r"[a-z]+:", target):
                    continue
                file, _, fragment = target.partition("#")
                linked = (path.parent / file).resolve() if file else path
                if not linked.exists():
                    broken.append(f"{path.name}: {target}")
                elif fragment and linked.suffix == ".md" and fragment not in anchors(linked):
                    broken.append(f"{path.name}: {target} (no such section)")
        self.assertEqual(broken, [])


# Numbers the manuals spell out as words.
WORDS = {5: ("five", "fünf"), 6: ("six", "sechs"), 10: ("ten", "zehn")}


class DocFactsTestCase(SimpleTestCase):
    """Numbers and lists the manuals take from the code. Each one is pinned here: whoever changes
    the constant gets a failing test that names the sentence to change with it - before that,
    nothing told them the manual quoted it."""

    def manual(self, language):
        return re.sub(r"\s+", " ", (DOCS / f"MANUAL.{language}.md").read_text())

    def assertInManuals(self, english, german):
        self.assertIn(english, self.manual("en"))
        self.assertIn(german, self.manual("de"))

    def test_kiosk_timings(self):
        from kiosk import views
        # The manual gives one number for both: booking on Order, logging out everywhere else.
        self.assertEqual(views.ORDER_AUTO_CHECKOUT_SECONDS, views.AUTO_LOGOUT_SECONDS)
        n = views.ORDER_AUTO_CHECKOUT_SECONDS
        self.assertInManuals(f"**{n} seconds without input**", f"**{n} Sekunden ohne Eingabe**")
        n = views.IDLE_RETURN_SECONDS
        self.assertInManuals(f"after {n} seconds without a tap", f"nach {n} Sekunden ohne Tipp")

    def test_phone_link_and_portal_session(self):
        from kiosk.views import LINK_TOKEN_TTL_SECONDS
        from portal.decorators import ROLLING_SESSION_SECONDS
        from portal.views import LINK_LOCK_AFTER_FAILURES, LINK_LOCK_MINUTES
        en, de = WORDS[LINK_TOKEN_TTL_SECONDS // 60]
        self.assertInManuals(f"valid for {en} minutes", f"gilt {de} Minuten")
        en, de = WORDS[round(ROLLING_SESSION_SECONDS / (86400 * 30.5))]
        self.assertInManuals(f"logged in for {en} months", f"{de} Monate angemeldet")
        en, de = WORDS[LINK_LOCK_AFTER_FAILURES]
        self.assertInManuals(f"After {en} invalid links within {LINK_LOCK_MINUTES} minutes",
                             f"Nach {de} ungültigen Links innerhalb von {LINK_LOCK_MINUTES} Minuten")

    def test_waiting_for_a_new_card(self):
        from dashboard.views import ALT_UID_CAPTURE_SECONDS
        from portal.views import CARD_ADD_CAPTURE_SECONDS
        self.assertEqual(ALT_UID_CAPTURE_SECONDS, CARD_ADD_CAPTURE_SECONDS)
        self.assertEqual(ALT_UID_CAPTURE_SECONDS, 60, "the manual says 'within a minute'")
        self.assertInManuals("held to the kiosk within a minute", "innerhalb einer Minute am Kiosk")

    def test_kiosk_pillory_length(self):
        import inspect
        from coffee import services
        self.assertEqual(inspect.signature(services.deposit_pillory).parameters["limit"].default, 7,
                         "the manual says 'the seven who owe the most'")
        self.assertInManuals("the seven who owe the most", "die sieben, die bis zum Vormonat am meisten schulden")

    def test_login_rules_table(self):
        from dashboard.models import POLICY_LIMITS, PasswordPolicy
        defaults = {f.name: f.default for f in PasswordPolicy._meta.fields if f.name in POLICY_LIMITS}
        rows = {  # field: (English row, German row) as "| label | default | range |"
            "min_length": ("| Minimum length | {} characters | {}–{} |", "| Mindestlänge | {} Zeichen | {}–{} |"),
            "min_kinds": ("| {} | {}–{} |", "| {} | {}–{} |"),
            "lock_after_failures": ("| Lock after failed logins | {} | {}–{} |",
                                    "| Sperre nach Fehlversuchen | {} | {}–{} |"),
            "lock_minutes": ("| Locked for | {} minutes | {}–{} |", "| Gesperrt für | {} Minuten | {}–{} |"),
            "idle_logout_minutes": ("| Logged out when idle for | {} minutes | {}–{} |",
                                    "| Abmelden bei Inaktivität nach | {} Minuten | {}–{} |"),
        }
        for field, (english, german) in rows.items():
            low, high = POLICY_LIMITS[field]
            self.assertIn(english.format(defaults[field], low, high), self.manual("en"), field)
            self.assertIn(german.format(defaults[field], low, high), self.manual("de"), field)
        self.assertIn(f"up to {POLICY_LIMITS['passphrase_length'][1]}; ", self.manual("en"))

    def test_provisioning_settings_table(self):
        """The installation chapter's table has one row per line of provision.sh's settings menu."""
        script = (Path(settings.BASE_DIR) / "scripts" / "provision.sh").read_text()
        menu = re.search(r"^step_settings\(\) \{.*?local items=\((.*?)\"---\"", script, re.S | re.M).group(1)
        labels = []
        for item in re.findall(r'^\s*"(.*?)\|', menu, re.M):
            pair = re.match(r'\$\(t (?:"([^"]+)"|(\w+)) (?:"([^"]+)"|(\w+))\)', item)
            labels.append((pair.group(1) or pair.group(2), pair.group(3) or pair.group(4)) if pair else (item, item))
        for language, index in (("en", 0), ("de", 1)):
            text = (DOCS / f"MANUAL.{language}.md").read_text()
            table = text[text.index("provision-settings.png"):]
            table = table[:table.index("\n\n", table.index("|"))]
            rows = [line for line in table.splitlines() if line.startswith("|")][2:]  # past header and rule
            self.assertEqual([row.split("|")[1].strip() for row in rows],
                             [label[index] for label in labels], language)

    def test_every_dashboard_page_is_in_the_manual(self):
        from django.utils import translation
        from dashboard.templatetags.dashboard_nav import DASHBOARD_NAV
        for language in ("en", "de"):
            text = self.manual(language)
            with translation.override(language):
                for _heading, _superuser, items in DASHBOARD_NAV:
                    for _name, label, *_rest in items:
                        self.assertTrue(f"**{label}**" in text or f"### {label}" in text, f"{language}: {label}")
