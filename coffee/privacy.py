"""The privacy notice every member confirms before their first booking - at the kiosk, on the
phone, the same for everyone: what this device records about them, what for, where it stays and
where it is documented in full (docs/PRIVACY.md, which the notice links to on GitHub).

Until a member has confirmed the current VERSION, nothing is booked for them - no order, no
payment, no purchase, no stock-take, no name of a new card. Members from before the notice (and
everyone a legacy import or the dashboard created) confirm it at their next tap. It is information
under Art. 13 GDPR that the member acknowledges, not their consent as the legal basis: that is the
tally the group agreed on (docs/PRIVACY.md, "Purpose") - and so a "no" ends the session, nothing more.
"""
from django.utils import timezone
from django.utils.translation import gettext as _

# Raise it when the notice says something new that members have to see: everyone confirms again.
VERSION = 1
DOCS_URL = "https://github.com/OneAndOnlyBaka/CoffeePy/blob/master/docs/PRIVACY.md"


def confirmed(member) -> bool:
    return member is not None and member.privacy_version >= VERSION


def confirm(member) -> None:
    member.privacy_version = VERSION
    member.privacy_confirmed_at = timezone.now()
    member.save(update_fields=["privacy_version", "privacy_confirmed_at"])


def notice_context() -> dict:
    """What templates/includes/privacy_notice.html needs: who is responsible (dashboard → System), the
    boards that show members' data to everyone at the kiosk, the link to the full documentation."""
    from dashboard.models import KioskBoards, PrivacyContact  # noqa: PLC0415 - dashboard imports coffee

    views = KioskBoards.current().kiosk_views()
    return {
        "controller": PrivacyContact.current().controller,
        "shown_boards": [label for view, label in (
            ("alltime", _("the ranking (who drinks the most)")),
            ("pillory", _("the pillory (who owes money)")),
            ("runaways", _("\"Ran off\" (who left the machine for the next one)")),
        ) if view in views],
        "docs_url": DOCS_URL,
    }
