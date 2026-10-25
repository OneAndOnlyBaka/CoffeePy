import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from coffee import services
from coffee.models import (
    AltUidCapture,
    CASH_METHOD_NAME,
    CoffeeSort,
    Member,
    MemberCard,
    MemberLinkToken,
    NfcReaderStatus,
    NfcScan,
    Order,
    Payment,
    PaymentMethod,
)
from coffee.network import public_base_url

MODE_ORDER = "order"
MODE_INFO = "info"
MODE_SETTINGS = "settings"
MODE_RANKING = "ranking"
VALID_MODES = (MODE_ORDER, MODE_INFO, MODE_SETTINGS, MODE_RANKING)

ORDER_AUTO_CHECKOUT_SECONDS = 20
AUTO_LOGOUT_SECONDS = 20
RANKING_VIEWS = ["alltime", "month", "pillory"]
LINK_TOKEN_TTL_SECONDS = 300

THANK_YOU_SECONDS = 20
# (max cups today inclusive, gif filename, message) - first match wins; the last row's
# max_cups is None so it also catches anything above the previous tier.
THANK_YOU_TIERS = [
    (1, "thankyou_1.gif", "Enjoy your coffee!"),
    (3, "thankyou_2_3.gif", "Cheers - enjoy!"),
    (5, "thankyou_4_5.gif", "You're on a roll today!"),
    (8, "thankyou_6_8.gif", "Whoa, pace yourself!"),
    (None, "thankyou_9plus.gif", "Certified caffeine junkie today!"),
]
# Overrides the tier above entirely once a member's *weekly* total gets extreme, rather
# than adding yet another daily-tier row - checked first, in _start_thank_you.
WEEK_CUPS_GIF_THRESHOLD = 100
WEEK_CUPS_GIF = "thankyou_week100.gif"
WEEK_CUPS_MESSAGE = "100+ cups this week?! Maybe take a break..."
# Nags members who are still just a card number (Member.has_placeholder_name) every time
# they order, so the ranking and the pillory stop being a list of numbers. Takes priority
# over both the daily tier and the weekly one - it's the only tier that the member can
# actually do something about, and it stops as soon as they do.
NO_NICKNAME_GIF = "thankyou_no_nickname.gif"
NO_NICKNAME_MESSAGE = "Who are you?! Set a nickname under Settings."


def index(request):
    request.session.setdefault("mode", MODE_ORDER)
    request.session.setdefault(
        "last_scan_id", NfcScan.objects.order_by("-id").values_list("id", flat=True).first() or 0
    )
    request.session.setdefault("ranking_view", "alltime")
    return render(request, "kiosk/index.html", {"mode": request.session["mode"]})


def nav(request, mode):
    if mode in VALID_MODES:
        request.session["mode"] = mode
        _reset_session_state(request)
    # The nav buttons swap only #panel via htmx (index.html): a full page load per tap took
    # ~370ms on a Pi 3, most of it Chromium rebuilding the page. The redirect stays for
    # anything that follows the plain link.
    if request.headers.get("HX-Request"):
        return _render_panel(request)
    return redirect("kiosk:index")


@require_GET
def panel(request):
    return _render_panel(request)


@require_POST
def ranking_next(request):
    current = request.session.get("ranking_view", "alltime")
    idx = (RANKING_VIEWS.index(current) + 1) % len(RANKING_VIEWS) if current in RANKING_VIEWS else 0
    request.session["ranking_view"] = RANKING_VIEWS[idx]
    return _render_panel(request)


@require_POST
def order_select(request):
    if request.session.get("logged_in") and request.session.get("mode") == MODE_ORDER:
        coffee_id = request.POST.get("coffee_id")
        if coffee_id:
            request.session["order_coffee_id"] = int(coffee_id)
        request.session["order_auto_checkout"] = False
    return _render_panel(request)


@require_POST
def order_confirm(request):
    if request.session.get("logged_in") and request.session.get("mode") == MODE_ORDER:
        order = _place_order(request)
        if order is not None:
            _start_thank_you(request, request.session["uid"])
        _reset_session_state(request)
    return _render_panel(request)


@require_POST
def order_cancel(request):
    if request.session.get("logged_in") and request.session.get("mode") == MODE_ORDER:
        _reset_session_state(request)
    return _render_panel(request)


@require_POST
def settings_save(request):
    if request.session.get("logged_in") and request.session.get("mode") == MODE_SETTINGS:
        uid = request.session["uid"]
        nickname = (request.POST.get("nickname") or str(uid)).strip()
        favourite_id = request.POST.get("favourite_coffee_id") or None
        Member.objects.update_or_create(
            uid=uid,
            defaults={
                "nickname": nickname,
                "favourite_coffee_id": favourite_id,
            },
        )
        _reset_session_state(request)
    return _render_panel(request)


@require_POST
def settings_touch(request):
    """Keeps the settings/info auto-logout timer alive while the user is actively
    editing a field (mirrors the Tkinter app resetting its logout timer while a
    keyboard/selector dialog is open)."""
    if request.session.get("logged_in"):
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
    return _render_panel(request)


@require_POST
def pay(request):
    if request.session.get("logged_in") and request.session.get("mode") == MODE_INFO:
        try:
            amount = Decimal(request.POST.get("amount", "0"))
        except InvalidOperation:
            amount = Decimal("0")
        method_name = (request.POST.get("method") or CASH_METHOD_NAME).strip() or CASH_METHOD_NAME
        if amount > 0:
            Payment.objects.create(member_id=request.session["uid"], value=amount, method_name=method_name)
        request.session.pop("pending_amount", None)
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
    return _render_panel(request)


PENDING_SESSION_KEYS = {"nickname": "pending_nickname", "amount": "pending_amount"}


def keyboard_edit(request):
    """A separate, non-polling page for touchscreens with no physical keyboard: typing
    here never fights the panel's own periodic refresh, since this page is never
    re-rendered while the user is on it. Reachable from an optional link next to the
    nickname/amount fields; those fields stay plain, directly-editable inputs so a
    physical keyboard works on the main panel too, without ever visiting this page."""
    if not request.session.get("logged_in"):
        return redirect("kiosk:index")
    field = request.POST.get("field") or request.GET.get("field")
    session_key = PENDING_SESSION_KEYS.get(field)
    if session_key is None:
        return redirect("kiosk:index")

    request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
    if request.method == "POST":
        request.session[session_key] = request.POST.get("value", "")
        return redirect("kiosk:index")

    return render(
        request,
        "kiosk/keyboard.html",
        {"field": field, "value": request.GET.get("value", ""), "numeric": field == "amount"},
    )


def doom(request):
    """Easter egg: reachable only via the secret nav-bar tap sequence (see
    secret_sequence.js). A standalone page like the keyboard, not tied to the panel's
    poll/session machinery - it's just a diversion, not part of the kiosk flow."""
    return render(request, "kiosk/doom.html")


# --- internal helpers -------------------------------------------------


def _reset_session_state(request):
    request.session["logged_in"] = False
    for key in (
        "uid", "deadline", "order_coffee_id", "order_auto_checkout", "is_new_user",
        "pending_nickname", "pending_amount",
    ):
        request.session.pop(key, None)


def _start_session(request, mode, uid, is_new_user=False):
    request.session["mode"] = mode
    request.session["logged_in"] = True
    request.session["uid"] = uid
    request.session["is_new_user"] = is_new_user
    request.session["deadline"] = time.time() + (
        ORDER_AUTO_CHECKOUT_SECONDS if mode == MODE_ORDER else AUTO_LOGOUT_SECONDS
    )
    request.session["order_coffee_id"] = None
    request.session["order_auto_checkout"] = True


def _consume_new_scan(request):
    """Returns the newest unseen card_inserted scan (or None), advancing last_scan_id
    past every unseen row so none are re-processed."""
    last_id = request.session.get("last_scan_id", 0)
    new_scans = list(NfcScan.objects.filter(id__gt=last_id).order_by("id"))
    if not new_scans:
        return None
    request.session["last_scan_id"] = new_scans[-1].id
    inserted = [s for s in new_scans if s.event == NfcScan.EVENT_INSERTED]
    return inserted[-1] if inserted else None


def _place_order(request):
    """Returns the created Order, or None if there was no CoffeeSort to order at all
    (shouldn't normally happen - the order screen requires one to exist) - callers use
    this to decide whether an order actually went through before e.g. showing thanks.

    Also None when the logged-in member no longer exists: an admin can delete or merge
    them away during the 20s the order screen is open, and the insert would then fail its
    foreign key on every poll, leaving the kiosk stuck on an error for good."""
    uid = request.session["uid"]
    member = Member.objects.filter(uid=uid).first()
    if member is None:
        return None
    coffee_id = request.session.get("order_coffee_id")
    coffee_sort = None
    if coffee_id:
        coffee_sort = CoffeeSort.objects.filter(pk=coffee_id).first()
    if coffee_sort is None:
        coffee_sort = member.favourite_coffee
    if coffee_sort is None:
        coffee_sort = CoffeeSort.objects.first()
    if coffee_sort is None:
        return None
    return Order.objects.create(
        member_id=uid,
        coffee_sort=coffee_sort,
        value=coffee_sort.price,
        machine_strokes=coffee_sort.machine_strokes,
        info=f"Ordered [{coffee_sort.pk}] {coffee_sort.name}",
    )


def _thank_you_tier(cups_today):
    for max_cups, gif, message in THANK_YOU_TIERS:
        if max_cups is None or cups_today <= max_cups:
            return gif, message
    return THANK_YOU_TIERS[-1][1], THANK_YOU_TIERS[-1][2]  # pragma: no cover - unreachable, last row always matches


def _start_thank_you(request, uid):
    """Arms the ~20s "Thank you" interlude shown after an order - see _state_context's
    not-logged-in branch. Deliberately doesn't touch "logged_in"/"uid" itself: those get
    cleared right after by _reset_session_state, which is what lets a *different*
    member's card tap interrupt this and start their own order immediately (the same
    scan-handling that already runs on every poll while logged out - see
    _render_panel)."""
    member = Member.objects.filter(uid=uid).first()
    if member is None or member.has_placeholder_name:
        gif, message = NO_NICKNAME_GIF, NO_NICKNAME_MESSAGE
    elif services.week_cups(member) > WEEK_CUPS_GIF_THRESHOLD:
        gif, message = WEEK_CUPS_GIF, WEEK_CUPS_MESSAGE
    else:
        gif, message = _thank_you_tier(services.today_cups(member))
    request.session["thank_you_name"] = member.display_name() if member is not None else str(uid)
    request.session["thank_you_gif"] = gif
    request.session["thank_you_message"] = message
    request.session["thank_you_until"] = time.time() + THANK_YOU_SECONDS


THANK_YOU_SESSION_KEYS = ("thank_you_until", "thank_you_name", "thank_you_gif", "thank_you_message")


def _thank_you_context(request):
    """Returns the "thank you" panel context while it's still armed and not yet expired,
    None otherwise (and clears it once expired, so a stale timestamp can't linger)."""
    until = request.session.get("thank_you_until")
    if until is None:
        return None
    if time.time() >= until:
        for key in THANK_YOU_SESSION_KEYS:
            request.session.pop(key, None)
        return None
    return {
        "state": "thank_you",
        "member_name": request.session.get("thank_you_name"),
        "gif": request.session.get("thank_you_gif"),
        "message": request.session.get("thank_you_message"),
    }


def _render_panel(request):
    mode = request.session.get("mode", MODE_ORDER)
    ctx = {"mode": mode}

    if not request.session.get("logged_in"):
        scan = _consume_new_scan(request)
        if scan is not None:
            _handle_new_scan(request, mode, scan, ctx)
    else:
        _tick(request)

    # _handle_new_scan/_tick may have changed the active mode (e.g. an unknown card
    # redirects into the settings flow, or a timeout logs out) - re-read it before
    # rendering so the panel reflects the *new* state, not the one at request start.
    mode = request.session.get("mode", mode)
    ctx["mode"] = mode
    if ctx.get("state") is None:
        ctx.update(_state_context(request, mode))

    # Who the panel currently belongs to, for panel_modal.js: the QR modal sits outside
    # #panel (so the poll can't wipe it mid-scan) and therefore has to be told when the
    # panel moved on without it - an auto-logout, or the next member's card.
    logged_in_uid = request.session.get("uid") if request.session.get("logged_in") else ""
    ctx["panel_key"] = f"{ctx['state']}:{logged_in_uid}"

    return render(request, "kiosk/panel.html", ctx)


def _qr_svg(data):
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    return img.to_string(encoding="unicode")


def _wifi_qr_context():
    """QR to join the kiosk's own WiFi, when it runs one (AP_SSID/AP_PASSPHRASE - blank on
    deployments that join an existing network). Belongs next to *every* QR the kiosk shows
    a phone: they all point at an address that only exists on that network, so a phone
    that isn't on it yet scans a link it then can't open."""
    if not (settings.AP_SSID and settings.AP_PASSPHRASE):
        return {}
    return {
        "wifi_qr_svg": _qr_svg(f"WIFI:T:WPA;S:{settings.AP_SSID};P:{settings.AP_PASSPHRASE};;"),
        "ap_ssid": settings.AP_SSID,
    }


def _qr_context(request):
    """QR codes for remote setup: join the AP's WiFi, and open the admin dashboard - both
    scannable from a phone/PC, since the touchscreen itself has no keyboard."""
    return {
        "dashboard_qr_svg": _qr_svg(public_base_url(request) + reverse("dashboard:index")),
        **_wifi_qr_context(),
    }


def _setup_wizard_context(request):
    """First-run kiosk state while no CoffeeSort exists yet."""
    return {"state": "no_coffee_sorts", **_qr_context(request)}


def qr_popup(request):
    """Same QR codes as the first-run wizard, but reachable any time via the round info
    button on the wait-for-card screen - rendered into a modal, not a full nav state."""
    return render(request, "kiosk/_qr_popup.html", _qr_context(request))


def link_phone_popup(request):
    """Modal QR for the "Link this phone" button on the Settings screen - lets the
    logged-in member scan a one-time bootstrap link (see portal.views.link) with their
    own phone to get long-lived stats/pay access there. Only reachable while logged in
    on the kiosk itself (same session that owns the settings screen), so it can't be
    fished for by someone else standing at the machine."""
    uid = request.session.get("uid")
    member = Member.objects.filter(pk=uid).first() if request.session.get("logged_in") and uid is not None else None
    if member is None:
        return HttpResponseForbidden()
    member.link_tokens.all().delete()
    token = MemberLinkToken.objects.create(
        member=member, expires_at=timezone.now() + timedelta(seconds=LINK_TOKEN_TTL_SECONDS)
    )
    link_url = public_base_url(request) + reverse("portal:link", args=[token.token])
    return render(
        request,
        "kiosk/_link_phone_popup.html",
        {
            "link_qr_svg": _qr_svg(link_url),
            "link_ttl_minutes": LINK_TOKEN_TTL_SECONDS // 60,
            **_wifi_qr_context(),
        },
    )


def _active_capture():
    """The dashboard's "set alternative card" flow arms this from a different browser
    session than the kiosk's own, so it's tracked in the DB rather than request.session
    (see AltUidCapture and dashboard.views.member_alt_uid_start/_poll). Expired rows are
    left alone here - the dashboard poll is what tells the admin "timed out" and cleans
    them up, so a kiosk poll racing ahead of it can't quietly turn a timeout into what
    looks like a silent success."""
    capture = AltUidCapture.objects.select_related("member").first()
    if capture is None or capture.is_expired():
        return None
    return capture


def _try_assign_alt_uid(capture, scan):
    """Consumes `scan` for an armed capture. Returns True if it was actually applied
    (capture is deleted on success); an unreadable card, or the account's own primary
    card, is silently ignored (not a mistake worth flagging). A card already linked to
    someone *else* leaves the capture armed so a different card can be tried, but records
    why on `capture.last_error` - see AltUidCapture - so the kiosk and whichever screen
    started this (dashboard/portal poll) can both show it instead of just looking stuck."""
    new_uid = scan.uid_as_int()
    if new_uid is None or new_uid == capture.member_id:
        return False
    conflict = Member.objects.filter(Q(uid=new_uid) | Q(alt_cards__uid=new_uid)).exclude(pk=capture.member_id).exists()
    if conflict:
        capture.last_error = "That card is already linked to another account - please try a different card."
        capture.save(update_fields=["last_error"])
        return False
    MemberCard.objects.create(member=capture.member, uid=new_uid)
    capture.delete()
    return True


def _handle_new_scan(request, mode, scan, ctx):
    capture = _active_capture()
    if capture is not None:
        _try_assign_alt_uid(capture, scan)
        ctx["state"] = "assigning_alt_uid"
        ctx["member"] = capture.member
        ctx["capture_error"] = capture.last_error
        return

    if mode != MODE_RANKING and CoffeeSort.objects.count() == 0:
        ctx.update(_setup_wizard_context(request))
        return

    uid = scan.uid_as_int()
    if uid is None:
        ctx["state"] = "card_not_recognized"
        return

    member = Member.objects.filter(Q(uid=uid) | Q(alt_cards__uid=uid)).distinct().first()
    if member is not None and member.is_departed:
        ctx["state"] = "account_departed"
        return

    # A tap via an alt card must still resolve to the member's real primary key -
    # every other lookup (order/settings/info context, _place_order, settings_save, pay)
    # filters Member by session["uid"] as if it were that pk, so storing the scanned
    # card's own uid here would silently 404 the member on every render after this one.
    session_uid = member.uid if member is not None else uid

    if mode in (MODE_ORDER, MODE_RANKING):
        if member is not None:
            _start_session(request, MODE_ORDER, session_uid)
        else:
            _start_session(request, MODE_SETTINGS, session_uid, is_new_user=True)
    elif mode == MODE_SETTINGS:
        _start_session(request, MODE_SETTINGS, session_uid, is_new_user=member is None)
    elif mode == MODE_INFO:
        if member is not None:
            _start_session(request, MODE_INFO, session_uid)
        else:
            _start_session(request, MODE_SETTINGS, session_uid, is_new_user=True)


def _tick(request):
    """Applies the order auto-checkout / settings & info auto-logout timeout."""
    mode = request.session.get("mode")
    deadline = request.session.get("deadline")
    if deadline is None:
        return
    if time.time() < deadline:
        return
    if mode == MODE_ORDER:
        if request.session.get("order_auto_checkout", True):
            order = _place_order(request)
            if order is not None:
                _start_thank_you(request, request.session["uid"])
    _reset_session_state(request)


def _state_context(request, mode):
    if not request.session.get("logged_in"):
        if mode == MODE_RANKING:
            return {"state": "ranking", **_ranking_context(request)}
        capture = _active_capture()
        if capture is not None:
            return {"state": "assigning_alt_uid", "member": capture.member, "capture_error": capture.last_error}
        if mode == MODE_ORDER:
            thank_you_ctx = _thank_you_context(request)
            if thank_you_ctx is not None:
                return thank_you_ctx
        if CoffeeSort.objects.count() == 0:
            return _setup_wizard_context(request)
        reader_online = NfcReaderStatus.objects.filter(pk=1, is_online=True).exists()
        return {
            "state": "wait_for_card",
            "reader_online": reader_online,
            # Each mode explains what a tap is about to do, with its own animation.
            "wait_mode": mode if mode in (MODE_INFO, MODE_SETTINGS) else MODE_ORDER,
            "checkout_seconds": ORDER_AUTO_CHECKOUT_SECONDS,
            "logout_seconds": AUTO_LOGOUT_SECONDS,
        }

    uid = request.session["uid"]
    deadline = request.session.get("deadline")
    seconds_left = max(0, int(deadline - time.time())) if deadline else None

    if mode == MODE_ORDER:
        return _order_context(request, uid, seconds_left)
    if mode == MODE_SETTINGS:
        return _settings_context(request, uid, seconds_left)
    if mode == MODE_INFO:
        return _info_pay_context(request, uid, seconds_left)
    return {"state": "wait_for_card"}


RANK_BADGES = {1: "🥇", 2: "🥈", 3: "🥉"}


def _rank_badge(uid, ranking_rows):
    """Medal emoji if `uid` is among the top 3 of `ranking_rows` (an "alltime"/"month"
    _ranking_rows() result), None otherwise - same medals the ranking screen itself shows
    for these two views."""
    for position, row in enumerate(ranking_rows[:3], start=1):
        if row["uid"] == uid:
            return RANK_BADGES.get(position)
    return None


def _order_context(request, uid, seconds_left):
    member = Member.objects.filter(uid=uid).first()
    coffee_sorts = list(CoffeeSort.objects.all())
    selected_id = (
        request.session.get("order_coffee_id")
        or (member.favourite_coffee_id if member else None)
        or (coffee_sorts[0].pk if coffee_sorts else None)
    )
    # Nudges members who still owe from before this month rolled over - same "ab dem
    # Vormonat" balance the dashboard pillory uses, so it can't say two different things.
    month_reminder_balance = None
    alltime_badge = None
    month_badge = None
    if member is not None:
        prior_balance = services.balance_before_current_month(member)
        if prior_balance < 0:
            month_reminder_balance = -prior_balance
        # Shares the ranking screen's cached rows - the order screen is polled as well.
        alltime_badge = _rank_badge(uid, _ranking_rows(request, "alltime"))
        month_badge = _rank_badge(uid, _ranking_rows(request, "month"))
    return {
        "state": "order",
        "member": member,
        "coffee_sorts": coffee_sorts,
        "selected_coffee_id": selected_id,
        "auto_checkout": request.session.get("order_auto_checkout", True),
        "month_reminder_balance": month_reminder_balance,
        "alltime_badge": alltime_badge,
        "month_badge": month_badge,
        "seconds_left": seconds_left,
    }


def _settings_context(request, uid, seconds_left):
    member = Member.objects.filter(uid=uid).first()
    pending = request.session.get("pending_nickname")
    nickname = pending if pending is not None else (member.nickname if member else "")
    return {
        "state": "settings",
        "uid": uid,
        "is_new_user": request.session.get("is_new_user", False),
        "nickname": nickname,
        "favourite_coffee_id": member.favourite_coffee_id if member else None,
        "coffee_sorts": list(CoffeeSort.objects.all()),
        "seconds_left": seconds_left,
    }


def _payment_method_qr_row(balance):
    """QR codes so a member can scan-to-pay what they owe via an admin-configured
    method (PayPal, a PayPal pool, ...) straight from the kiosk screen - only methods
    with a link and only while something is actually owed (see PaymentMethod)."""
    owed = -balance if balance < 0 else Decimal("0.00")
    if owed <= 0:
        return []
    entries = []
    for method in PaymentMethod.objects.filter(is_active=True):
        link = method.build_link(owed)
        if link:
            entries.append({"name": method.name, "qr_svg": _qr_svg(link)})
    return entries


def _payment_method_instructions(balance):
    """Plain-text payment instructions for methods with no scannable link (e.g. WERO,
    which has no public deep-link scheme) - only while something is actually owed."""
    owed = -balance if balance < 0 else Decimal("0.00")
    if owed <= 0:
        return []
    return [
        text
        for method in PaymentMethod.objects.filter(is_active=True)
        if not method.build_link(owed) and (text := method.instructions(owed))
    ]


def _info_pay_context(request, uid, seconds_left):
    member = Member.objects.filter(uid=uid).first()
    balance = services.account_balance(member) if member else Decimal("0.00")
    default_suggestion = -balance if balance < 0 else Decimal("0.00")
    pending = request.session.get("pending_amount")
    pay_default = pending if pending is not None else default_suggestion
    return {
        "state": "info_pay",
        "member": member,
        "total_cups": services.total_cups(member) if member else 0,
        "month_cups": services.month_cups(member) if member else 0,
        "balance": balance,
        "pay_default": pay_default,
        "payment_method_names": [CASH_METHOD_NAME] + list(
            PaymentMethod.objects.filter(is_active=True).values_list("name", flat=True)
        ),
        "payment_method_qr_row": _payment_method_qr_row(balance),
        "payment_method_instructions": _payment_method_instructions(balance),
        "seconds_left": seconds_left,
    }


def _ranking_context(request):
    view = request.session.get("ranking_view", "alltime")
    if view not in ("month", "pillory"):
        view = "alltime"
    return {"ranking_view": view, "rows": _ranking_rows(request, view)}


RANKING_CACHE_SECONDS = 300


def _ranking_rows(request, view):
    """The rows of one ranking view, recomputed only when services.ranking_fingerprint()
    says the data behind them changed. The ranking screen is re-rendered on every ~500ms
    poll for as long as the kiosk idles on it, and its aggregates run over the whole order
    history - 100-200ms of Postgres per poll on a Pi 3 with a few years of data.

    Django's per-process cache rather than the session: a poll writing the session can
    overwrite a nav tap that landed meanwhile (see index.html's hx-sync). The session key
    is part of the cache key only to keep separate kiosk sessions - and test clients -
    from ever sharing an entry."""
    fingerprint = getattr(request, "_ranking_fingerprint", None)
    if fingerprint is None:
        fingerprint = request._ranking_fingerprint = services.ranking_fingerprint()
    session_key = request.session.session_key
    key = f"kiosk-ranking:{session_key}:{view}:{fingerprint}"
    rows = cache.get(key) if session_key else None
    if rows is None:
        rows = _compute_ranking_rows(view)
        if session_key:
            cache.set(key, rows, RANKING_CACHE_SECONDS)
    return rows


def _compute_ranking_rows(view):
    # ranking_all_time()/ranking_current_month() already annotate each member with the
    # cups total the ranking itself is sorted by - only the *other* figure (ccm/cct)
    # needs a lookup, and that's batched below in one query rather than one per member.
    if view == "month":
        members = list(services.ranking_current_month())
        alltime_cups = services.cups_by_member(members)
        return [
            {"uid": m.uid, "name": m.display_name(), "cct": alltime_cups.get(m.uid, 0), "ccm": m.cups}
            for m in members
        ]
    if view == "pillory":
        return [
            {"name": e["member"].display_name(), "balance": e["balance"]}
            for e in services.deposit_pillory()
        ]
    members = list(services.ranking_all_time())
    month_cups = services.cups_by_member(members, since=services.current_month_start())
    return [
        {"uid": m.uid, "name": m.display_name(), "cct": m.cups, "ccm": month_cups.get(m.uid, 0)}
        for m in members
    ]
