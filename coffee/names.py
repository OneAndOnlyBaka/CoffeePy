"""What a member's name has to look like: a first name and the start of the last name -
"Anna B." - or a short code of capitals ("AB", as offices use them), so that colleagues
know who is on the ranking and the pillory.

The legacy device let anyone skip the name, and after a year 7 of its 24 members were their
card number and others a few letters nobody could place. So the kiosk asks for a name like
this before anyone can order (kiosk.views._handle_new_scan), and the same rules apply where
an admin edits a member (dashboard.forms.MemberForm). Software can't tell a real first name
from a made-up one - these rules catch the placeholders, the keyboard mashing and the
duplicates; the rest is up to the people who read the ranking.
"""
import re

from django.utils.translation import gettext_lazy as _

MAX_LENGTH = 20
LOWER = "a-zäöüßéèêáàâçñ'"
UPPER = "A-ZÄÖÜ"
# One or more first names (Anna, Anna-Lena, Anna Lena), then one to three letters of the
# last name with a full stop: "Anna B.", "Anna Be." when there already is an "Anna B.".
FORMAT = re.compile(
    rf"^(?P<first>[{UPPER}][{UPPER}{LOWER}]+(?:[- ][{UPPER}][{UPPER}{LOWER}]+)*) (?P<last>[{UPPER}][{LOWER}]{{0,2}})\.$")
# Or the short code many workplaces know everyone by: two to four capitals, letters only.
SHORT_CODE = re.compile(rf"^[{UPPER}]{{2,4}}$")

# Placeholders people type to get past a name field - matched against the first name, in
# German and English. Settings → "Blocked names" in the dashboard adds more (NamePolicy).
BLOCKED = {
    "test", "tester", "name", "vorname", "firstname", "user", "nutzer", "benutzer", "member", "mitglied",
    "ich", "me", "myself", "du", "you", "kaffee", "coffee", "admin", "root", "nix", "nichts", "nothing",
    "anonym", "anonymous", "anon", "keiner", "niemand", "nobody", "none", "unbekannt", "unknown", "gast",
    "guest", "karte", "card", "hallo", "hello", "hi", "foo", "bar", "xxx", "abc",
}
KEYBOARD_ROWS = ("qwertzuiopü", "qwertyuiop", "asdfghjklöä", "yxcvbnm", "zxcvbnm", "1234567890")

MESSAGES = {
    "empty": _("Please enter your name."),
    "format": _('Your first name and the first letter of your last name, like "Anna B." - or your short code, like "AB".'),
    "too_long": _("At most 20 characters."),
    "made_up": _('That doesn\'t look like a first name - use your real one, like "Anna B."'),
    "taken": _('Somebody already goes by that name - add a letter of your last name, like "Anna Be."'),
}


def normalize(raw: str) -> str:
    """What a touchscreen keyboard makes easy to get slightly wrong, put right: spacing,
    capitals ("anna b" -> "Anna B."), the full stop after the initial."""
    name = " ".join((raw or "").split())
    if SHORT_CODE.match(name):
        return name  # "AB" is a short code, not a first name to capitalise
    parts = name.split(" ")
    if len(parts) >= 2 and re.fullmatch(rf"[{UPPER}{LOWER}]{{1,3}}\.?", parts[-1], re.IGNORECASE):
        last = parts[-1].rstrip(".")
        parts[-1] = last[:1].upper() + last[1:].lower() + "."
    for index, part in enumerate(parts[:-1] if len(parts) >= 2 else parts):
        # Capitalise each first name - but leave one typed with care (McKenzie) as it is.
        parts[index] = "-".join(_capitalised(piece) for piece in part.split("-"))
    return " ".join(parts)


def _capitalised(word: str) -> str:
    if word.islower() or word.isupper():
        return word[:1].upper() + word[1:].lower()
    return word[:1].upper() + word[1:]


def problem(name: str, member=None, extra_blocked=None) -> str | None:
    """None if `name` (already normalized) is a usable name for `member` (None for a new
    one), otherwise a key of MESSAGES."""
    from coffee.models import Member  # noqa: PLC0415 - models import this module's rules

    if not name or not name.strip():
        return "empty"
    if len(name) > MAX_LENGTH:
        return "too_long"
    match = FORMAT.match(name)
    if match:
        first = match.group("first").lower()
    elif SHORT_CODE.match(name):
        first = name.lower()
    else:
        return "format"
    blocked = BLOCKED | set(extra_blocked if extra_blocked is not None else _extra_blocked())
    if any(part in blocked for part in re.split(r"[- ]", first)) or _mashed(first):
        return "made_up"
    others = Member.objects.filter(nickname__iexact=name, departed_at__isnull=True)
    if member is not None and member.pk is not None:
        others = others.exclude(pk=member.pk)
    if others.exists():
        return "taken"
    return None


def fixable(member) -> str | None:
    """The member's stored name put into the usual form, if that is all it lacks ("Anna B"
    without the full stop - three of the legacy device's heaviest users): the kiosk then
    takes it as it is instead of asking."""
    name = normalize(member.nickname)
    if name != member.nickname and problem(name, member=member) is None:
        return name
    return None


def suggestion(old: str) -> str:
    """What the name field starts with for someone whose name won't do - so the most common
    case, a bare first name, only needs its initial: "Anna" -> "Anna ", "Max Mustermann" ->
    "Max M.". Nothing for card numbers."""
    words = [w for w in re.split(r"[\s_.]+", old or "") if re.fullmatch(rf"[{UPPER}{LOWER}-]+", w, re.IGNORECASE)]
    if not words:
        return ""
    first = "-".join(_capitalised(piece) for piece in words[0].split("-") if piece)
    if len(words) >= 2:
        return f"{first} {words[1][:1].upper()}."
    return f"{first} "


def message(key: str | None):
    return MESSAGES.get(key) if key else None


def _mashed(first: str) -> bool:
    letters = first.replace("-", "").replace(" ", "")
    if re.search(r"(.)\1\1", letters):  # "aaa" - two in a row is a name (Anna, Otto), three isn't
        return True
    return any(letters[i:i + 4] in row for row in KEYBOARD_ROWS for i in range(len(letters) - 3))


def _extra_blocked() -> list[str]:
    from dashboard.models import NamePolicy  # noqa: PLC0415 - dashboard depends on coffee, not the other way

    return NamePolicy.current().blocked_list()
