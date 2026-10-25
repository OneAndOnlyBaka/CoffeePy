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


class TemplatesMarkedTestCase(SimpleTestCase):
    """Every piece of visible text in a template goes through {% translate %} - a forgotten
    one shows up in English on a German screen (it happened with the kiosk's logout countdown)."""

    # Text that is the same in both languages, or not text at all.
    ALLOWED = re.compile(r"[\W\d_]*(CoffeePy|SHA-256|CPU|UID|PEM|CSV|Esc|EN|DE|MB|GB|uid|https:///|- &deg;C)?[\W\d_]*")

    def test_no_unmarked_text(self):
        unmarked = []
        for path in sorted(settings.BASE_DIR.glob("*/templates/**/*.html")) + sorted((settings.BASE_DIR / "templates").glob("*.html")):
            if path.name == "maintenance.html":  # rendered with {% language %} blocks instead
                continue
            text = path.read_text()
            text = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "", text, flags=re.S)
            text = re.sub(r"{%\s*blocktranslate.*?{%\s*endblocktranslate\s*%}", "", text, flags=re.S)
            text = re.sub(r"<(script|style|svg)\b.*?</\1>", "", text, flags=re.S)
            text = re.sub(r"{%.*?%}|{{.*?}}|{#.*?#}", "", text, flags=re.S)
            for match in re.finditer(r">([^<>]*[A-Za-z]{3,}[^<>]*)<", text):
                piece = match.group(1).strip()
                if piece and piece != path.parent.name and not self.ALLOWED.fullmatch(piece) and "&middot;" != piece:
                    unmarked.append(f"{path.relative_to(settings.BASE_DIR)}: {piece}")
        self.assertEqual(unmarked, [])
