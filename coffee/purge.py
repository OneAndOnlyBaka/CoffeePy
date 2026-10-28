"""Bulk deletion of existing data, in two scopes.

IMPORT_SCOPE clears exactly what a legacy-DB import refills, so an import can start from
a clean slate instead of merging into whatever is already there. FULL_SCOPE is the
dashboard's "reset database" action and additionally drops payment methods, NFC scans
and the audit log.

Neither scope ever touches Django's auth tables: an admin who deletes their own dashboard
login has no way back in on a kiosk box that nobody can SSH into.
"""

from django.apps import apps
from django.contrib.sessions.models import Session
from django.db import transaction

from coffee.models import (
    AltUidCapture,
    AuditLogEntry,
    CoffeeSort,
    Member,
    MemberCard,
    MemberDevice,
    MemberLinkToken,
    NfcScan,
    Order,
    Payment,
    PaymentMethod,
)

# Children before parents. The member-owned rows (cards, devices, tokens, captures) would
# cascade with Member anyway; deleting them explicitly just keeps the order obvious.
IMPORT_SCOPE = [
    Order,
    Payment,
    AltUidCapture,
    MemberLinkToken,
    MemberDevice,
    MemberCard,
    Member,
    CoffeeSort,
]

# Not NfcReaderStatus: it is the device's state, not data - deleted, the kiosk says "card reader
# not available" with a working reader until the bridge reports a change.
FULL_SCOPE = IMPORT_SCOPE + [NfcScan, PaymentMethod, AuditLogEntry]


def purge(scope) -> dict:
    """Deletes every row of the models in `scope` and returns {model label: rows deleted}.

    Runs in one transaction (a savepoint when the caller already opened one, as the
    import does), so a failure half-way leaves the data as it was."""
    counts = {}
    with transaction.atomic():
        _log_out_member_devices()
        for model in scope:
            _, per_model = model.objects.all().delete()
            for label, count in per_model.items():
                counts[label] = counts.get(label, 0) + count
    return {label: count for label, count in counts.items() if count}


def describe(counts: dict) -> str:
    """"12 members, 340 orders" - for the audit log and the dashboard's result message."""
    parts = []
    for label, count in sorted(counts.items()):
        name = apps.get_model(label)._meta.verbose_name_plural
        parts.append(f"{count} {name}")
    return ", ".join(parts) if parts else "nothing"


def _log_out_member_devices() -> None:
    """Members' linked phones authenticate through a Django session that outlives the
    MemberDevice row (see portal views), so those sessions have to go with them - other-
    wise a phone stays logged in as a member that no longer exists. Dashboard/admin
    sessions are untouched: they are not linked to a MemberDevice."""
    session_keys = list(MemberDevice.objects.values_list("session_key", flat=True))
    Session.objects.filter(pk__in=session_keys).delete()
