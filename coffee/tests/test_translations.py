import ast
import gettext
import re

from django.conf import settings
from django.test import SimpleTestCase


def read_po(path):
    """(msgctxt, msgid, msgstr) for every entry of a .po file - just enough of the format
    for the catalog makemessages writes here (no plurals are used)."""
    entries, current, key = [], {}, None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            if current.get("msgid"):
                entries.append(current)
            if not line:
                current, key = {}, None
            continue
        match = re.match(r'^(msgctxt|msgid|msgstr) (".*")$', line)
        if match:
            key = match.group(1)
            current[key] = ast.literal_eval(match.group(2))
        elif line.startswith('"') and key:
            current[key] += ast.literal_eval(line)
    if current.get("msgid"):
        entries.append(current)
    return [(e.get("msgctxt"), e["msgid"], e.get("msgstr", "")) for e in entries]


class GermanCatalogTestCase(SimpleTestCase):
    """locale/de: every string translated, and the committed .mo compiled from the current
    .po - a .po edited without `manage.py compilemessages` changes nothing on screen."""

    def setUp(self):
        directory = settings.BASE_DIR / "locale" / "de" / "LC_MESSAGES"
        self.entries = read_po(directory / "django.po")
        with open(directory / "django.mo", "rb") as mo:
            self.compiled = gettext.GNUTranslations(mo)

    def test_every_string_is_translated(self):
        untranslated = [msgid for _, msgid, msgstr in self.entries if not msgstr]
        self.assertEqual(untranslated, [])

    def test_mo_matches_po(self):
        stale = []
        for context, msgid, msgstr in self.entries:
            shown = self.compiled.pgettext(context, msgid) if context else self.compiled.gettext(msgid)
            if shown != msgstr:
                stale.append(msgid)
        self.assertEqual(stale, [], "run: manage.py compilemessages -l de --ignore=.venv")
