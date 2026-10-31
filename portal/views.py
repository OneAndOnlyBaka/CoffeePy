import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from coffee import services
from coffee.models import (
    AltUidCapture, AuditLogEntry, CASH_METHOD_NAME, MemberDevice, MemberLinkToken, Payment, PaymentMethod,
)

from dashboard import audit

from .decorators import ROLLING_SESSION_SECONDS, portal_login_required

CARD_ADD_CAPTURE_SECONDS = 60
# Generous: a member re-opening an old QR photo a few times must not be locked out.
LINK_LOCK_AFTER_FAILURES = 10
LINK_LOCK_MINUTES = 15


def _label_for_device(request) -> str:
    ua = request.META.get("HTTP_USER_AGENT", "")
    for hint in ("iPhone", "iPad", "Android", "Macintosh", "Windows", "Linux"):
        if hint in ua:
            return hint
    return "Browser"


def _link_locked(request) -> bool:
    """Whether this address has opened too many unknown or expired links lately. The token
    can't be guessed (256 bits), but every failed try is an audit row - unthrottled, anyone
    on the network could fill the SD card with them and keep the kiosk's ranking cache
    (keyed on the newest audit id) recomputing. Counted from the audit log, like the
    dashboard's lock, so it holds across all gunicorn workers; the first refused request of
    a lock is logged, the rest aren't - that is the point."""
    ip = audit.client_ip(request)
    since = timezone.now() - timedelta(minutes=LINK_LOCK_MINUTES)
    recent = AuditLogEntry.objects.filter(ip=ip, created_at__gte=since, source=AuditLogEntry.SOURCE_PORTAL)
    if recent.filter(action="portal.login", outcome=AuditLogEntry.OUTCOME_FAILURE).count() < LINK_LOCK_AFTER_FAILURES:
        return False
    if not recent.filter(action="portal.link_locked").exists():
        audit.log(request, "portal.link_locked", actor="", outcome=AuditLogEntry.OUTCOME_FAILURE,
                  details=f"{LINK_LOCK_AFTER_FAILURES} invalid links within {LINK_LOCK_MINUTES} minutes")
    return True


def link(request, token):
    if _link_locked(request):
        # Also for a valid token: otherwise the lock would only slow down the guessing.
        return render(request, "portal/link_invalid.html", {"locked_minutes": LINK_LOCK_MINUTES}, status=429)
    link_token = MemberLinkToken.objects.select_related("member").filter(token=token).first()
    if link_token is None or link_token.is_expired():
        audit.log(request, "portal.login", actor="", details="link unknown or expired", outcome="failure")
        return render(request, "portal/link_invalid.html", status=410)

    member = link_token.member
    link_token.delete()

    request.session.flush()
    request.session["portal_member_uid"] = member.pk
    request.session.set_expiry(ROLLING_SESSION_SECONDS)
    if not request.session.session_key:
        request.session.save()

    MemberDevice.objects.create(
        member=member, session_key=request.session.session_key, label=_label_for_device(request)
    )
    who = audit.member_actor(member)
    audit.log(request, "portal.login", target=who, actor=who, details=f"phone linked ({_label_for_device(request)})")
    return redirect("portal:index")


def link_invalid(request):
    return render(request, "portal/link_invalid.html")


@portal_login_required
def index(request):
    member = request.member
    balance = services.account_balance(member)
    ctx = {
        "member": member,
        "total_cups": services.total_cups(member),
        "month_cups": services.month_cups(member),
        "balance": balance,
        "owed": -balance if balance < 0 else Decimal("0.00"),
        "recent_orders": member.orders.select_related("coffee_sort")[:10],
        "recent_payments": member.payments.all()[:10],
    }
    return render(request, "portal/index.html", ctx)


@portal_login_required
def stats(request):
    """The same numbers the admin dashboard charts, but told as "you against everyone
    else": each chart puts this member's own habits next to everyone else's."""
    member = request.member
    ctx = {
        "member": member,
        "summary": services.member_comparison_summary(member),
        "monthly": json.dumps(services.member_vs_others_monthly(member)),
        "hourly": json.dumps(services.member_vs_others_hourly(member)),
        "weekday": json.dumps(services.member_vs_others_weekday(member)),
        "sorts": json.dumps(services.member_vs_others_sorts(member)),
    }
    return render(request, "portal/stats.html", ctx)


def _payment_methods_context(balance: Decimal):
    """Cash is always offered and isn't a DB row; configured methods only show a link (or
    for WERO, an instruction - it has no public deep-link scheme) once there's actually
    something owed (a positive balance means the member is in credit, and pool/PayPal
    links can't sensibly represent "pay me nothing")."""
    owed = -balance if balance < 0 else Decimal("0.00")
    methods = []
    for method in PaymentMethod.objects.filter(is_active=True):
        link = method.build_link(owed) if owed > 0 else ""
        instructions = method.instructions(owed) if owed > 0 and not link else ""
        methods.append({"method": method, "link": link, "instructions": instructions})
    return {"payment_methods": methods, "owed": owed}


@portal_login_required
def pay(request):
    member = request.member
    balance = services.account_balance(member)
    if request.method == "POST":
        amount = request.POST.get("amount", "").strip().replace(",", ".")
        method_name = request.POST.get("method", CASH_METHOD_NAME).strip() or CASH_METHOD_NAME
        try:
            value = Decimal(amount)
        except InvalidOperation:
            value = None
        if value is not None and value > 0:
            payment = Payment.objects.create(member=member, value=value, method_name=method_name)
            who = audit.member_actor(member)
            audit.log(request, "payment.create", target=who, actor=who, details=f"#{payment.pk}: {value} EUR ({method_name})")
            return redirect("portal:index")
    default_suggestion = -balance if balance < 0 else Decimal("0.00")
    ctx = {"member": member, "balance": balance, "pay_default": default_suggestion}
    ctx.update(_payment_methods_context(balance))
    return render(request, "portal/pay.html", ctx)


@portal_login_required
def devices(request):
    return render(
        request,
        "portal/devices.html",
        {"devices": request.member.devices.all(), "current_key": request.session.session_key},
    )


@portal_login_required
def my_data(request):
    """Everything stored about the member, as a file - their right of access (Art. 15 GDPR)
    without asking an admin. Other people in it (who changed a booking) appear by role only."""
    who = audit.member_actor(request.member)
    audit.log(request, "member.export", target=who, actor=who, details="own, portal")
    return services.member_data_response(request.member, own=True)


@portal_login_required
@require_POST
def revoke_device(request, device_id):
    device = get_object_or_404(MemberDevice, pk=device_id, member=request.member)
    Session.objects.filter(pk=device.session_key).delete()
    device.delete()
    who = audit.member_actor(request.member)
    audit.log(request, "portal.device_revoke", target=who, actor=who, details=device.label)
    return redirect("portal:devices")


@portal_login_required
@require_POST
def logout_view(request):
    MemberDevice.objects.filter(session_key=request.session.session_key).delete()
    who = audit.member_actor(request.member)
    audit.log(request, "portal.logout", target=who, actor=who)
    request.session.flush()
    return redirect("portal:link_invalid")


@portal_login_required
def cards(request):
    return render(
        request,
        "portal/cards.html",
        {
            "member": request.member,
            "alt_cards": request.member.alt_cards.all(),
            "capture": AltUidCapture.objects.filter(member=request.member).first(),
        },
    )


@portal_login_required
def card_add_start(request):
    """Self-service version of the dashboard's "Add card" - same AltUidCapture the
    kiosk watches for (see kiosk.views._handle_new_scan), just armed from the member's
    own phone instead of an admin's dashboard session. Only one capture is meant to be
    armed at a time (single physical reader), so arming a new one clears any previous
    one first - matches dashboard.views.member_alt_uid_start."""
    AltUidCapture.objects.all().delete()
    AltUidCapture.objects.create(
        member=request.member, expires_at=timezone.now() + timedelta(seconds=CARD_ADD_CAPTURE_SECONDS)
    )
    who = audit.member_actor(request.member)
    audit.log(request, "member.card_capture", target=who, actor=who, details="waiting for a new card at the kiosk")
    return redirect("portal:cards")


@portal_login_required
@require_GET
def card_add_poll(request):
    capture = AltUidCapture.objects.filter(member=request.member).first()
    if capture is None:
        # The kiosk deletes the row the moment it successfully applies a card - so its
        # absence here means success, not merely "nothing happened yet".
        response = HttpResponse("")
        response["HX-Redirect"] = reverse("portal:cards")
        return response

    if capture.is_expired():
        capture.delete()
        timed_out = _("Timed out - no card was tapped within %(seconds)s seconds.") % {
            "seconds": CARD_ADD_CAPTURE_SECONDS
        }
        return HttpResponse(
            f'<p class="msg error">{escape(timed_out)}</p>'
            f'<p><a href="{reverse("portal:card_add_start")}">{escape(_("Try again"))}</a></p>'
        )

    seconds_left = int((capture.expires_at - timezone.now()).total_seconds())
    # Stored in English by the kiosk (kiosk.views.CARD_CONFLICT_ERROR) and translated here,
    # in this device's language.
    error_html = f'<p class="msg error">{escape(_(capture.last_error))}</p>' if capture.last_error else ""
    waiting = _("Waiting for a card to be tapped on the kiosk reader… (%(seconds)ss left)") % {"seconds": seconds_left}
    return HttpResponse(f"{error_html}<p>{escape(waiting)}</p>")
