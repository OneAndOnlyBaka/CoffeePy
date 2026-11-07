import json
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy, gettext_noop, pgettext
from django.views.decorators.http import require_GET, require_POST

from coffee import backups, clock, machine, names, privacy, rtc, services
from coffee import slots as my_coffees
from coffee.models import (
    AltUidCapture,
    CASH_METHOD_NAME,
    CoffeeSort,
    InventoryItem,
    MachineDispense,
    MachineProduct,
    MemberSlot,
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
from dashboard import audit
from dashboard.models import InventoryPolicy, KioskBoards, MemberRetention

MODE_ORDER = "order"
MODE_INFO = "info"
MODE_SETTINGS = "settings"
MODE_RANKING = "ranking"
MODE_PURCHASE = "purchase"
VALID_MODES = (MODE_ORDER, MODE_INFO, MODE_SETTINGS, MODE_RANKING, MODE_PURCHASE)

ORDER_AUTO_CHECKOUT_SECONDS = 20
AUTO_LOGOUT_SECONDS = 20
# The privacy notice is read, not tapped through: three times the usual logout (coffee/privacy.py).
PRIVACY_SECONDS = 60
LINK_TOKEN_TTL_SECONDS = 300

THANK_YOU_SECONDS = 20
# The coffee machine (coffee/machine.py): how long the "put your cup under it" screen waits for its
# Start before it gives up (nothing booked), and how long a failed brew's message stays.
CUP_SECONDS = 30
BREW_MESSAGE_SECONDS = 12
# A brew that runs longer than this without the machine saying "done" is left to the listener
# (it books it after coffee.machine.STARTED_TIMEOUT) and the kiosk goes back to its cards.
BREW_GIVE_UP_SECONDS = 240
# Why a brew didn't happen, as the bridge says it - shown in the kiosk's language.
BREW_REASONS = {
    "blocked": gettext_noop("The machine needs attention first."),
    "busy": gettext_noop("The machine is busy with another product."),
    "disconnected": gettext_noop("The connection to the machine was lost."),
    "unreachable": gettext_noop("The machine can't be reached right now."),
}
BREW_REASON_OTHER = gettext_noop("The machine didn't make it.")
# Settings, Pay and the ranking go back to ordering when nobody used them for this long -
# otherwise the next person walks up to an "apply your card" screen that won't order anything.
IDLE_RETURN_SECONDS = 30
# (max cups today inclusive, gif filename, message) - first match wins; the last row's
# max_cups is None so it also catches anything above the previous tier. The messages (and
# the two below) are only marked here: they travel through the JSON session as their English
# msgid and are translated when the panel renders them - see _thank_you_context.
THANK_YOU_TIERS = [
    (1, "thankyou_1.gif", gettext_noop("Enjoy your coffee!")),
    (3, "thankyou_2_3.gif", gettext_noop("Cheers - enjoy!")),
    (5, "thankyou_4_5.gif", gettext_noop("You're on a roll today!")),
    (8, "thankyou_6_8.gif", gettext_noop("Whoa, pace yourself!")),
    (None, "thankyou_9plus.gif", gettext_noop("Certified caffeine junkie today!")),
]
# Overrides the tier above entirely once a member's *weekly* total gets extreme, rather
# than adding yet another daily-tier row - checked first, in _start_thank_you.
WEEK_CUPS_GIF_THRESHOLD = 100
WEEK_CUPS_GIF = "thankyou_week100.gif"
WEEK_CUPS_MESSAGE = gettext_noop("100+ cups this week?! Maybe take a break...")
# For a member whose name doesn't pass coffee/names.py. Rare now that the kiosk asks for a
# proper name before the order (_handle_new_scan) - left for an admin renaming someone to
# something the rules refuse mid-session. Takes priority over both the daily tier and the
# weekly one - it's the only tier that the member can actually do something about.
NO_NICKNAME_GIF = "thankyou_no_nickname.gif"
NO_NICKNAME_MESSAGE = gettext_noop("Who are you?! Set your name under Settings.")


# What the "back in a moment" screen (static/kiosk/maintenance.js) says for each step that
# install.sh and the update watcher report (install/maintenance.sh).
MAINTENANCE_STEPS = {
    "starting": gettext_lazy("Getting started"),
    "preflight": gettext_lazy("Checking the device"),
    "packages": gettext_lazy("Installing system packages"),
    "app": gettext_lazy("Copying the new version"),
    "hardware": gettext_lazy("Setting up the hardware"),
    "kiosk": gettext_lazy("Setting up this screen"),
    "services": gettext_lazy("Installing services"),
    "unpack": gettext_lazy("Unpacking the update"),
    "images": gettext_lazy("Preparing the new version"),
    "save": gettext_lazy("Saving the current data"),
    "restart": gettext_lazy("Restarting"),
    "rollback": gettext_lazy("Going back to the previous version"),
    "recover": gettext_lazy("Repairing an interrupted update"),
    "ap": gettext_lazy("Setting up the WiFi"),
    "ssh": gettext_lazy("Securing remote access"),
    "verify": gettext_lazy("Starting CoffeePy"),
    # dashboard/restore_jobs.py
    "restore_safety": gettext_lazy("Saving the current data"),
    "restore": gettext_lazy("Restoring a backup"),
    "migrate": gettext_lazy("Updating the database"),
}


def index(request):
    request.session.setdefault("mode", MODE_ORDER)
    # Every page load starts from the newest scan: a browser that comes back - restarted by the
    # watchdog, or in the morning after the night (dashboard/night.py) - must not book the taps
    # people made at the dark screen.
    request.session["last_scan_id"] = NfcScan.objects.order_by("-id").values_list("id", flat=True).first() or 0
    request.session.setdefault("ranking_view", "alltime")
    return render(request, "kiosk/index.html", {
        "mode": request.session["mode"],
        "ranking_on": bool(_ranking_views(request)),
        "inventory_on": _inventory_on(request),
        "texts": _page_texts(),
        "language": translation.get_language(),
        "all_texts": _all_page_texts(),
    })


def _page_texts():
    """Every text index.html shows outside #panel, in the active language - the template takes
    them from here, and the language button hands them to static/kiosk/language.js, which relabels
    the page in place. Loading the page anew would be a navigation, and every navigation risks
    Chromium's renderer crash (CLAUDE.md): a few taps on the button in a row crashed it (2026-11-01)."""
    return {
        "title": _("CoffeePy Kiosk"),
        "order": _("Order"),
        "pay": _("Pay"),
        "settings": _("Settings"),
        "purchase": pgettext("kiosk screen", "Purchase"),
        "ranking": _("Ranking"),
        "loading": _("Loading…"),
        "space": _("Space"),
        "cancel": _("Cancel"),
        "done": _("Done"),
        "back_title": _("We'll be right back"),
        "back_text": _("CoffeePy is being updated - ordering is paused for a moment."),
        "steps": {key: str(label) for key, label in MAINTENANCE_STEPS.items()},
    }


def _all_page_texts():
    """_page_texts in every language, for the wait screen's language button: it relabels the page
    from these without asking the server (static/kiosk/language.js)."""
    texts = {}
    for code, _name in settings.LANGUAGES:
        with translation.override(code):
            texts[code] = _page_texts()
    return texts


def _wait_variants(request, ctx):
    """The wait screen once per language (kiosk/_wait.html), stacked by panel.html - its language
    button only shows the other one. Costs a second render per poll of the wait screen: measured on
    the Pi 3, see CLAUDE.md."""
    variants = []
    for code, _name in settings.LANGUAGES:
        with translation.override(code):
            variants.append((code, render_to_string("kiosk/_wait.html", ctx, request)))
    return variants


def _ranking_views(request):
    """The ranking screen's tabs the admin allows (dashboard → System, KioskBoards) - asked once
    per request; empty means no ranking screen at all."""
    views = getattr(request, "_ranking_views", None)
    if views is None:
        views = request._ranking_views = KioskBoards.current().kiosk_views()
    return views


def _inventory_on(request):
    """Whether members book purchases and take stock here (dashboard → System: "for members too") -
    asked once per request, like the ranking."""
    on = getattr(request, "_inventory_on", None)
    if on is None:
        on = request._inventory_on = InventoryPolicy.current().members_book
    return on


def _mode_available(request, mode):
    if mode == MODE_RANKING:
        return bool(_ranking_views(request))
    if mode == MODE_PURCHASE:
        return _inventory_on(request)
    return mode in VALID_MODES


def language(request):
    """The kiosk's language button: the other of the two languages, from now on, for this
    kiosk. A plain link rather than django.views.i18n.set_language's POST form - a form's
    csrf token differs on every render, and the wait screen it sits on must not (panel.html).
    Through htmx it answers with the panel in the new language and, for the rest of the page,
    its texts (_page_texts) - the page itself is never loaded again. Only reachable on the
    device itself, like the rest of /kiosk/."""
    chosen = request.GET.get("to")
    target = chosen if chosen in dict(settings.LANGUAGES) else ("en" if translation.get_language() == "de" else "de")
    _touched(request)
    if chosen:
        # The wait screen's button: the page already shows that language (language.js) - only keep it.
        # Named, not toggled: two quick taps must not end in whichever request came last.
        response = HttpResponse(status=204)
    elif request.headers.get("HX-Request"):
        translation.activate(target)
        request.LANGUAGE_CODE = target
        response = _render_panel(request)
        response["Content-Language"] = target
        # ASCII-escaped JSON: a header can't carry "…" or umlauts as they are.
        response["HX-Trigger"] = json.dumps({"kiosk-language": {"lang": target, "texts": _page_texts()}})
    else:
        response = redirect("kiosk:index")
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME, target,
        max_age=settings.LANGUAGE_COOKIE_AGE, path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN, secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY, samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response


def nav(request, mode):
    # A ranking or purchase button the admin has just switched off can still be on a screen that
    # hasn't polled since (nav_state.js hides it then) - its tap changes nothing.
    if _mode_available(request, mode):
        request.session["mode"] = mode
        _reset_session_state(request)
        if mode == MODE_RANKING:  # always opens on its first tab, not on whichever was left open
            request.session["ranking_view"] = _ranking_views(request)[0]
    # The nav buttons swap only #panel via htmx (index.html): a full page load per tap took
    # ~370ms on a Pi 3, most of it Chromium rebuilding the page. The redirect stays for
    # anything that follows the plain link.
    if request.headers.get("HX-Request"):
        return _render_panel(request)
    return redirect("kiosk:index")


# The kiosk's sign of life for the host's watchdog (install/kiosk_watchdog.sh): a browser
# that froze, or sits on a page that never loaded its scripts, stops polling - which nothing
# else notices. A file's mtime, touched at most this often per worker: the poll comes twice a
# second, and the SD card should not hear about every one of them.
HEARTBEAT_SECONDS = 30
_heartbeat_at = 0.0


def _heartbeat():
    global _heartbeat_at
    now = time.monotonic()
    if now - _heartbeat_at < HEARTBEAT_SECONDS:
        return
    _heartbeat_at = now
    try:
        (settings.STORAGE_DIR / "kiosk-heartbeat").touch()
    except OSError:
        pass  # a development checkout without data/storage


@require_GET
def panel(request):
    _heartbeat()
    return _render_panel(request)


@require_GET
def ranking_view(request, view):
    """A tab of the ranking screen. A GET link like the language button, for the same
    reason: a form's csrf token would make the screen differ on every poll, and the podium
    animation restart with it (panel.html)."""
    if view in _ranking_views(request):
        request.session["ranking_view"] = view
    _touched(request)
    if request.headers.get("HX-Request"):
        return _render_panel(request)
    return redirect("kiosk:index")


@require_POST
def order_select(request):
    if _in_session(request, MODE_ORDER):
        # The panel goes on showing what it showed at the card: the pick is the browser's (a
        # checked tile; countdown.js switches the countdown). Re-rendering it swapped the panel
        # away under the finger, and that teardown crashed Chromium in 3 of 5 picks on the device
        # (2026-10-31) - the swap a poll would do, if this changed the panel, as well.
        request.session.setdefault("shown_deadline", request.session.get("deadline"))
        request.session.setdefault("shown_order", [request.session.get("order_coffee_id"),
                                                   request.session.get("order_auto_checkout", True),
                                                   request.session.get("order_slot")])
        coffee_id = request.POST.get("coffee_id") or ""
        if coffee_id.startswith("slot:"):  # one of "my coffees" - the same group of tiles
            request.session["order_slot"] = _int(coffee_id[5:])
            request.session["order_coffee_id"] = None
        elif coffee_id:
            request.session["order_coffee_id"] = int(coffee_id)
            request.session["order_slot"] = None
        # Whoever picks a sort confirms it themselves - with time for that from the pick on, and a
        # logout countdown in place of the checkout one.
        request.session["order_auto_checkout"] = False
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
    return HttpResponse(status=204)


@require_POST
def order_confirm(request):
    if _in_session(request, MODE_ORDER):
        _checkout(request)
    return _render_panel(request)


@require_POST
def machine_start(request):
    """The Start on "put your cup under it": now the machine is asked. Booked only once it is done."""
    brew = request.session.get("brew")
    if brew and brew.get("stage") == "cup":
        product = MachineProduct.objects.filter(code=brew["code"], present=True).first()
        member = Member.objects.filter(uid=brew.get("uid")).first() if brew.get("uid") is not None else None
        sort = CoffeeSort.objects.filter(pk=brew.get("sort_id")).first()
        state = machine.status()
        if product is None or not state["connected"] or (not brew.get("free") and (member is None or sort is None)):
            _brew_message(request, "unreachable")
        elif state["blocking"]:
            pass  # the screen shows what to do; Start stays until it is done
        else:
            dispense = machine.request_brew(
                product, member, sort, MachineDispense.SOURCE_FREE if brew.get("free") else MachineDispense.SOURCE_KIOSK,
                options=brew.get("options"), count=brew.get("count", 1))
            request.session["brew"] = {**brew, "stage": "brewing", "dispense_id": dispense.pk,
                                       "since": time.time()}
            if member is not None:
                who = audit.member_actor(member)
                extra = ", ".join([f"{k} {v}" for k, v in (brew.get("options") or {}).items()]
                                  + ([f"{brew['count']} cups"] if brew.get("count", 1) > 1 else []))
                audit.log(request, "machine.brew", target=who, actor=who,
                          details=f"{product.name} for {sort.name}{' (' + extra + ')' if extra else ''}")
            else:
                audit.log(request, "machine.brew", target=product.name, actor="", details="free")
    _touched(request)
    return _render_panel(request)


@require_POST
def machine_cancel(request):
    """Back from "put your cup under it" - nothing was asked of the machine, nothing is booked."""
    brew = request.session.get("brew")
    if brew and brew.get("stage") == "cup":
        request.session.pop("brew", None)
        _reset_session_state(request)
    return _render_panel(request)


@require_POST
def machine_free(request):
    """A free product (hot water) from the waiting screen: no card, no booking - with the machine's
    keys locked the only way to get it. Only while nobody is logged in and the machine can do it."""
    product = MachineProduct.objects.filter(code=_int(request.POST.get("code")), free=True, present=True).first()
    if (not request.session.get("logged_in") and request.session.get("brew") is None and product is not None
            and machine.can_brew(product) and machine.brewing_available()):
        request.session["brew"] = {"stage": "cup", "code": product.code, "free": True,
                                   "until": time.time() + CUP_SECONDS}
    _touched(request)
    return _render_panel(request)


@require_POST
def order_cancel(request):
    if _in_session(request, MODE_ORDER):
        _reset_session_state(request)
    return _render_panel(request)


@require_POST
def settings_save(request):
    if _in_session(request, MODE_SETTINGS):
        uid = request.session["uid"]
        # No falling back to the card's uid any more: that is how the legacy device ended up
        # with a third of its members listed as numbers (coffee/names.py).
        nickname = names.normalize(request.POST.get("nickname", ""))
        favourite_id = request.POST.get("favourite_coffee_id") or None
        problem = names.problem(nickname, member=Member.objects.filter(uid=uid).first())
        if problem:
            request.session["pending_nickname"] = nickname
            request.session["pending_favourite"] = favourite_id  # or the re-render undid the pick
            request.session["name_error"] = problem
            request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
            request.session.pop("shown_deadline", None)
            return _render_panel(request)
        member, created = Member.objects.update_or_create(
            uid=uid,
            defaults={
                "nickname": nickname,
                "favourite_coffee_id": favourite_id,
            },
        )
        audit.log(request, "member.create" if created else "member.update", target=audit.member_actor(member),
                  actor=audit.member_actor(member), details="own settings at the kiosk")
        if request.session.get("privacy_confirmed") and not privacy.confirmed(member):
            privacy.confirm(member)
            audit.log(request, "member.privacy_confirm", target=audit.member_actor(member),
                      actor=audit.member_actor(member), details=f"notice {privacy.VERSION}, new card")
        then_mode = request.session.get("then_mode")
        _reset_session_state(request)
        if then_mode in (MODE_ORDER, MODE_PURCHASE):
            # They came to order or to book a purchase and were asked for their name first: on to it.
            _start_session(request, then_mode, member.uid)
    return _render_panel(request)


@require_POST
def settings_touch(request):
    """Keeps the settings/info auto-logout timer alive while the user is actively
    editing a field (mirrors the Tkinter app resetting its logout timer while a
    keyboard/selector dialog is open)."""
    if request.session.get("logged_in"):
        # The panel keeps showing the deadline it already shows (countdown.js extends the visible
        # countdown on the same tap): a new one would make the next poll differ, and that swap
        # threw away what was being typed or picked and tore the layout down - Chromium's crash.
        request.session.setdefault("shown_deadline", request.session.get("deadline"))
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
    return _render_panel(request)


@require_POST
def pay(request):
    if _in_session(request, MODE_INFO):
        try:
            # A German keyboard - and the German panel's own number format - says "1,50".
            amount = Decimal(request.POST.get("amount", "0").replace(",", "."))
        except InvalidOperation:
            amount = Decimal("0")
        if not amount.is_finite():  # "NaN"/"Infinity" parse fine, then fail every comparison
            amount = Decimal("0")
        method_name = (request.POST.get("method") or CASH_METHOD_NAME).strip() or CASH_METHOD_NAME
        if amount > 0:
            payment = Payment.objects.create(member_id=request.session["uid"], value=amount, method_name=method_name)
            who = audit.member_actor(payment.member)
            audit.log(request, "payment.create", target=who, actor=who, details=f"#{payment.pk}: {amount} EUR ({method_name})")
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
        request.session.pop("shown_deadline", None)
    return _render_panel(request)


def _purchase_notice(request, notice):
    request.session["inventory_notice"] = notice
    request.session.pop("inventory_error", None)


@require_POST
def purchase_book(request):
    """A purchase from the kiosk: the member is credited what they paid (services.book_purchase)."""
    if _in_session(request, MODE_PURCHASE) and _inventory_on(request):
        member = Member.objects.filter(uid=request.session["uid"]).first()
        item = InventoryItem.objects.filter(is_active=True, pk=_int(request.POST.get("item_id"))).first()
        quantity = services.parse_quantity(request.POST.get("quantity", ""))
        price = services.parse_quantity(request.POST.get("price", ""))
        request.session["inventory_page"] = "purchase"
        if member is None or item is None or not quantity or not price:
            request.session["inventory_error"] = PURCHASE_ERROR
        else:
            purchase = services.book_purchase(member, item, quantity, price.quantize(Decimal("0.01")))
            who = audit.member_actor(member)
            audit.log(request, "purchase.create", target=who, actor=who, details=services.describe_purchase(purchase))
            _purchase_notice(request, {"kind": "purchase", "item": item.name, "unit": item.unit,
                                       "quantity": str(purchase.quantity), "price": str(purchase.price)})
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
        request.session.pop("shown_deadline", None)
    return _render_panel(request)


@require_POST
def stock_count(request):
    """A stock-take of one item, by one of the members the admin picked (Member.may_take_stock)."""
    if _in_session(request, MODE_PURCHASE) and _inventory_on(request):
        member = Member.objects.filter(uid=request.session["uid"], may_take_stock=True).first()
        item = InventoryItem.objects.filter(is_active=True, pk=_int(request.POST.get("count_item_id"))).first()
        quantity = services.parse_quantity(request.POST.get("count", ""))
        request.session["inventory_page"] = "stock"
        if member is None or item is None or quantity is None:
            request.session["inventory_error"] = STOCK_ERROR
        else:
            who = audit.member_actor(member)
            services.record_stock_count(item, quantity, member.display_name())
            audit.log(request, "stock.count", target=item.name, actor=who, details=f"{item.name} {quantity.normalize():f}")
            _purchase_notice(request, {"kind": "count", "item": item.name, "unit": item.unit,
                                       "quantity": str(quantity)})
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
        request.session.pop("shown_deadline", None)
    return _render_panel(request)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Stored in the session as the English msgid, translated when the panel shows it.
PURCHASE_ERROR = gettext_noop("Pick an item and enter a quantity and a price above 0.")
STOCK_ERROR = gettext_noop("Pick an item and enter what you counted.")


# --- internal helpers -------------------------------------------------


def _in_session(request, mode):
    """A member is logged in on this screen and may book: never while the privacy notice is still
    waiting for them (coffee/privacy.py) - the screen shows it, and a request sent past it is refused."""
    return (request.session.get("logged_in") and request.session.get("mode") == mode
            and not request.session.get("privacy_pending"))


def _reset_session_state(request):
    request.session["logged_in"] = False
    for key in (
        "uid", "deadline", "shown_deadline", "shown_order", "sort_order", "order_coffee_id", "order_slot",
        "order_auto_checkout", "is_new_user", "settings_view", "slots_notice",
        "brew",
        "pending_nickname", "pending_favourite", "name_error", "then_mode",
        "inventory_page", "inventory_notice", "inventory_error", "privacy_pending", "privacy_confirmed",
    ):
        request.session.pop(key, None)
    _touched(request)


def _touched(request):
    """Restarts the idle countdown of _idle_return - every tap and every logout ends up here."""
    request.session["idle_since"] = time.time()


def _idle_return(request):
    """Falls back from Settings, Pay or the ranking to ordering after IDLE_RETURN_SECONDS
    without a tap. Not while a card is being added: that screen waits for a second card."""
    if request.session.get("logged_in") or request.session.get("mode", MODE_ORDER) == MODE_ORDER:
        return
    idle_since = request.session.setdefault("idle_since", time.time())
    if time.time() - idle_since >= IDLE_RETURN_SECONDS and _active_capture() is None:
        request.session["mode"] = MODE_ORDER
        request.session["ranking_view"] = "alltime"


def _start_session(request, mode, uid, is_new_user=False):
    member = Member.objects.filter(uid=uid).first()
    if member is not None:
        machine.note_visitor(member)  # the likeliest to see to what someone left at the machine
    request.session["mode"] = mode
    request.session["logged_in"] = True
    request.session["uid"] = uid
    request.session["is_new_user"] = is_new_user
    request.session["deadline"] = time.time() + (
        ORDER_AUTO_CHECKOUT_SECONDS if mode == MODE_ORDER else AUTO_LOGOUT_SECONDS
    )
    request.session.pop("shown_deadline", None)
    request.session.pop("shown_order", None)
    request.session.pop("sort_order", None)
    # A free product on its cup screen is left to whoever tapped a card; one that runs, runs on.
    request.session.pop("brew", None)
    request.session["order_coffee_id"] = None
    request.session["order_slot"] = None
    request.session["order_auto_checkout"] = True
    request.session.pop("settings_view", None)


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


def _chosen_sort(request, member):
    """The sort the member is about to get: the one picked, or the favourite, or the first one.
    Never one the machine sync made obsolete - not even a favourite chosen before."""
    coffee_id = request.session.get("order_coffee_id")
    coffee_sort = CoffeeSort.offered().filter(pk=coffee_id).first() if coffee_id else None
    if coffee_sort is None and member.favourite_coffee is not None and not member.favourite_coffee.is_obsolete:
        coffee_sort = member.favourite_coffee
    return coffee_sort or CoffeeSort.offered().first()


def _chosen(request, member, picked=None):
    """(sort, slot) the member is about to get: a slot of "my coffees" picked, or a sort picked, or
    the slot they preselect (Member.favourite_slot), or the favourite sort as always. `picked` is
    (coffee_id, slot position) - the session's picks unless given."""
    coffee_id, position = picked if picked is not None else (
        request.session.get("order_coffee_id"), request.session.get("order_slot"))
    slots = {slot.position: slot for slot in my_coffees.slots_for(member)}
    if position in slots:
        return slots[position].coffee_sort, slots[position]
    if coffee_id:
        sort = CoffeeSort.offered().filter(pk=coffee_id).first()
        if sort is not None:
            return sort, None
    if member.favourite_slot in slots:
        return slots[member.favourite_slot].coffee_sort, slots[member.favourite_slot]
    if member.hide_standard_sorts and slots:
        # The sorts are a page on: what the screen preselects - and checks out after 20 s - has to be
        # in sight (the legacy device once booked a favourite nobody could see).
        first = slots[min(slots)]
        return first.coffee_sort, first
    favourite = member.favourite_coffee
    return (favourite if favourite is not None and not favourite.is_obsolete else CoffeeSort.offered().first()), None


def _checkout(request):
    """Order now, or the automatic checkout: with a coffee machine that makes this sort, the member is
    guided through the brew and it is booked once the machine is done (coffee.machine.book) - else it
    is booked here, as it always was."""
    uid = request.session["uid"]
    member = Member.objects.filter(uid=uid).first()
    sort, slot = _chosen(request, member) if member is not None else (None, None)
    count = slot.count if slot is not None else 1
    product = machine.product_for(sort) if sort is not None and machine.brewing_available() else None
    if product is not None:
        request.session["brew"] = {"stage": "cup", "uid": uid, "sort_id": sort.pk, "code": product.code,
                                   "until": time.time() + CUP_SECONDS, "count": count,
                                   "options": my_coffees.options(slot) if slot is not None else {}}
        # The deadline of the order screen is over: the brew has timeouts of its own.
        request.session.pop("deadline", None)
        request.session.pop("shown_deadline", None)
        return
    order = _place_order(request, sort, count)
    if order is not None:
        _start_thank_you(request, uid)
    _reset_session_state(request)


def _brew_message(request, reason, made=0):
    brew = request.session.get("brew") or {}
    request.session["brew"] = {**brew, "stage": "failed" if reason != "done_free" else "done_free",
                               "reason": reason, "made": made, "until": time.time() + BREW_MESSAGE_SECONDS}


def _brew_tick(request):
    """Moves a brew on: the cup screen times out, a running brew ends in the thank-you (booked by
    the listener as the machine reported it done) or in a message why not."""
    brew = request.session.get("brew")
    if not brew:
        return
    now = time.time()
    stage = brew.get("stage")
    if stage in ("cup", "failed", "done_free"):
        if now >= brew.get("until", 0):
            request.session.pop("brew", None)
            _reset_session_state(request)
        return
    dispense = MachineDispense.objects.filter(pk=brew.get("dispense_id")).first()
    # Several cups: the give-up clock runs per cup, from the start of the one running.
    progress = machine.chain_progress(dispense) if dispense is not None else None
    if dispense is None or now - brew.get("since", now) > BREW_GIVE_UP_SECONDS * (progress["of"] if progress else 1):
        request.session.pop("brew", None)
        _reset_session_state(request)
    elif not progress["over"]:
        return
    elif progress["failed"] is None:
        if brew.get("free"):
            _brew_message(request, "done_free")
        else:
            uid = brew["uid"]
            request.session.pop("brew", None)
            _start_thank_you(request, uid)
            _reset_session_state(request)
    else:
        _brew_message(request, progress["failed"].reason, made=progress["done"])


def _brew_context(request):
    brew = request.session["brew"]
    stage = brew["stage"]
    product = MachineProduct.objects.filter(code=brew.get("code")).first()
    member = Member.objects.filter(uid=brew.get("uid")).first() if brew.get("uid") is not None else None
    sort = CoffeeSort.objects.filter(pk=brew.get("sort_id")).first()
    state = machine.status()
    ctx = {"state": "brew", "stage": stage, "free": bool(brew.get("free")), "member": member,
           "product_name": sort.name if sort is not None else (machine.product_label(product.name) if product else ""),
           "brew_deadline_ms": int(brew["until"] * 1000) if brew.get("until") else None}
    count = brew.get("count", 1)
    water = (brew.get("options") or {}).get("water_ml")
    ctx["count"] = count
    ctx["options_text"] = _options_text(brew.get("options") or {})
    if stage == "cup":
        ctx["blocking"] = [machine.alert_label(key) for key in state["blocking"]]
        ctx["tasks"] = [t for t in machine.tasks_before(product, water, count)
                        if t not in ctx["blocking"]] if product else []
    elif stage == "brewing":
        ctx["tasks"] = machine.tasks_after(product, water, count) if product else []
        first = MachineDispense.objects.filter(pk=brew.get("dispense_id")).first()
        if first is not None and count > 1:
            ctx["cup"] = machine.chain_progress(first)["cup"]
        phase = (state["state"].get("progress") or {}).get("phase", "")
        # Only two pictures: every change of the panel's HTML is a swap, and each swap risks the
        # renderer crash (CLAUDE.md) - no percentages here, the animation runs on its own.
        ctx["phase"] = "brewing" if phase in ("starting", "dispensing", "done", "busy") else "heating"
    elif stage == "failed":
        reason = brew.get("reason", "")
        ctx["reason"] = _(BREW_REASONS.get(reason.split(":")[0], BREW_REASON_OTHER))
        ctx["made"] = brew.get("made", 0)
        if reason == "blocked":
            ctx["blocking"] = [machine.alert_label(key) for key in state["blocking"]]
    return ctx


def _free_products():
    """What the waiting screen offers without a card - only while the machine can make it."""
    if not machine.brewing_available():
        return []
    return [p for p in MachineProduct.objects.filter(free=True, present=True) if machine.can_brew(p)][
        :machine.FREE_ON_KIOSK]


def _place_order(request, coffee_sort=None, count=1):
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
    if coffee_sort is None:
        coffee_sort = _chosen_sort(request, member)
    if coffee_sort is None:
        return None
    # A slot of several cups without the machine: one order per cup, as the machine would book them.
    for _cup in range(max(1, count)):
        order = Order.objects.create(
            member_id=uid,
            coffee_sort=coffee_sort,
            value=coffee_sort.price,
            machine_strokes=coffee_sort.machine_strokes,
            info=f"Ordered [{coffee_sort.pk}] {coffee_sort.name}",
        )
        who = audit.member_actor(member)
        audit.log(request, "order.create", target=who, actor=who,
                  details=f"#{order.pk}: {coffee_sort.name}, {order.value} EUR, {order.machine_strokes} cup(s)")
    return order


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
    if member is None or member.name_problem is not None:
        gif, message = NO_NICKNAME_GIF, NO_NICKNAME_MESSAGE
    elif services.week_cups(member) > WEEK_CUPS_GIF_THRESHOLD:
        gif, message = WEEK_CUPS_GIF, WEEK_CUPS_MESSAGE
    else:
        gif, message = _thank_you_tier(services.today_cups(member))
    request.session["thank_you_name"] = member.display_name() if member is not None else str(uid)
    request.session["thank_you_uid"] = uid
    request.session["thank_you_since"] = time.time()
    request.session.pop("thank_you_tasks", None)
    request.session["thank_you_gif"] = gif
    request.session["thank_you_message"] = message
    request.session["thank_you_until"] = time.time() + THANK_YOU_SECONDS


THANK_YOU_SESSION_KEYS = ("thank_you_until", "thank_you_name", "thank_you_gif", "thank_you_message",
                          "thank_you_uid", "thank_you_since", "thank_you_tasks")


def _thank_you_context(request):
    """Returns the "thank you" panel context while it's still armed and not yet expired,
    None otherwise (and clears it once expired, so a stale timestamp can't linger)."""
    until = request.session.get("thank_you_until")
    if until is None:
        return None
    # What the machine asked of this member during their brew (coffee.machine.track_alerts): the
    # thank-you waits for it - up to the incident's grace - and says thanks once it is done.
    tasks = machine.open_tasks(Member.objects.filter(uid=request.session.get("thank_you_uid")).first())
    if tasks:
        request.session["thank_you_tasks"] = True
        until = max(until, min(time.time() + 2, request.session.get("thank_you_since", 0) + machine.INCIDENT_GRACE))
        request.session["thank_you_until"] = until
    if time.time() >= until:
        for key in THANK_YOU_SESSION_KEYS:
            request.session.pop(key, None)
        return None
    return {
        "state": "thank_you",
        "member_name": request.session.get("thank_you_name"),
        "gif": request.session.get("thank_you_gif"),
        "message": _translated(request.session.get("thank_you_message")),
        "tasks": [t["label"] for t in tasks],
        "tasks_done": not tasks and request.session.get("thank_you_tasks", False),
    }


def _render_panel(request):
    mode = request.session.get("mode", MODE_ORDER)
    if not _mode_available(request, mode):  # switched off in the dashboard while on screen
        mode = request.session["mode"] = MODE_ORDER
        _reset_session_state(request)
    ctx = {"mode": mode}

    _brew_tick(request)
    if not request.session.get("logged_in"):
        scan = _consume_new_scan(request)
        if scan is not None:
            _touched(request)  # also a card that is refused
            _handle_new_scan(request, mode, scan, ctx)
        elif request.session.get("brew") is None:
            _idle_return(request)
    elif request.session.get("brew") is None:
        _tick(request)

    # _handle_new_scan/_tick may have changed the active mode (e.g. an unknown card
    # redirects into the settings flow, or a timeout logs out) - re-read it before
    # rendering so the panel reflects the *new* state, not the one at request start.
    mode = request.session.get("mode", mode)
    ctx["mode"] = mode
    ctx["ranking_views"] = _ranking_views(request)
    ctx["inventory_on"] = _inventory_on(request)
    if ctx.get("state") is None:
        ctx.update(_state_context(request, mode))

    # Who the panel currently belongs to, for panel_modal.js: the QR modal sits outside
    # #panel (so the poll can't wipe it mid-scan) and therefore has to be told when the
    # panel moved on without it - an auto-logout, or the next member's card.
    logged_in_uid = request.session.get("uid") if request.session.get("logged_in") else ""
    ctx["panel_key"] = f"{ctx['state']}:{logged_in_uid}"
    if ctx["state"] in ("wait_for_card", "ranking"):  # the screens with the corner buttons
        ctx["backup_disturbed"] = backups.health()["disturbed"]
        ctx["rtc_disturbed"] = rtc.health()["disturbed"]
        ctx["clock_corrected"] = clock.corrected() is not None
        ctx["admin_task"] = bool(services.departed_members_due(MemberRetention.current().departed_months))
        ctx["machine_level"] = machine.kiosk_level()
    if ctx["state"] == "wait_for_card":
        ctx["wait_variants"] = _wait_variants(request, ctx)

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


def machine_popup(request):
    """The coffee machine's state for the modal behind its ring (_corner_buttons.html): what it
    says, what wants doing, how far its maintenance is. Read once when opened - the modal sits
    outside #panel, the poll never touches it, and it closes when the panel moves on."""
    current = machine.status()
    percent = (current["counters"].get("maintenance") or {}).get("percent") or {}
    return render(request, "kiosk/_machine_popup.html", {
        "level": machine.kiosk_level(), "status": current,
        "name": current["machine"].name or current["state"].get("model", ""),
        "messages": [machine.alert_label(k) for k in current["alerts"]],
        "maintenance": [{"label": machine.MAINTENANCE_LABELS.get(k, k), "percent": v} for k, v in percent.items()],
        **machine.supply_levels(),
    })


@require_POST
def machine_refill(request):
    """"Beans refilled" in the machine's modal - the machine itself only says so once they ran out."""
    machine.refill("beans", actor="kiosk")
    return machine_popup(request)


def link_phone_popup(request):
    """Modal QR for the "Link this phone" button on the Settings screen - lets the
    logged-in member scan a one-time bootstrap link (see portal.views.link) with their
    own phone to get long-lived stats/pay access there. Only reachable while logged in
    on the kiosk itself (same session that owns the settings screen), so it can't be
    fished for by someone else standing at the machine."""
    uid = request.session.get("uid")
    member = Member.objects.filter(pk=uid).first() if request.session.get("logged_in") and uid is not None else None
    if member is None or not privacy.confirmed(member):
        return HttpResponseForbidden()
    member.link_tokens.all().delete()
    audit.log(request, "portal.link_issued", target=audit.member_actor(member), actor=audit.member_actor(member))
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


# Stored in the DB as the English msgid, not in the kiosk's language: the dashboard or portal
# that armed the capture shows the same row, possibly in the other language.
CARD_CONFLICT_ERROR = gettext_noop("That card is already linked to another account - please try a different card.")


def _translated(msgid):
    # gettext("") would return the catalog's header, not "".
    return _(msgid) if msgid else msgid


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
        capture.last_error = CARD_CONFLICT_ERROR
        capture.save(update_fields=["last_error"])
        return False
    MemberCard.objects.create(member=capture.member, uid=new_uid)
    capture.delete()
    return True


def _handle_new_scan(request, mode, scan, ctx):
    capture = _active_capture()
    if capture is not None:
        member = capture.member
        if _try_assign_alt_uid(capture, scan):
            audit.log(request, "member.card_add", target=audit.member_actor(member), actor="kiosk card reader",
                      details=f"card {scan.uid_as_int()}")
        ctx["state"] = "assigning_alt_uid"
        ctx["member"] = capture.member
        ctx["capture_error"] = _translated(capture.last_error)
        return

    if mode != MODE_RANKING and not CoffeeSort.offered().exists():
        ctx.update(_setup_wizard_context(request))
        return

    uid = scan.uid_as_int()
    if uid is None:
        ctx["state"] = "card_not_recognized"
        audit.log(request, "kiosk.card_login", target=scan.uid or "", actor="", details="card not readable",
                  outcome="failure")
        return

    member = Member.objects.filter(Q(uid=uid) | Q(alt_cards__uid=uid)).distinct().first()
    if member is not None and member.is_departed:
        ctx["state"] = "account_departed"
        audit.log(request, "kiosk.card_login", target=audit.member_actor(member), actor=audit.member_actor(member),
                  details="account marked as departed", outcome="failure")
        return
    if member is None:
        audit.log(request, "kiosk.card_login", target=f"card {uid}", actor=f"card {uid}", details="new card")
    else:
        audit.log(request, "kiosk.card_login", target=audit.member_actor(member), actor=audit.member_actor(member),
                  details=mode)

    # A tap via an alt card must still resolve to the member's real primary key -
    # every other lookup (order/settings/info context, _place_order, settings_save, pay)
    # filters Member by session["uid"] as if it were that pk, so storing the scanned
    # card's own uid here would silently 404 the member on every render after this one.
    session_uid = member.uid if member is not None else uid

    if member is not None and member.name_problem is not None and (fixed := names.fixable(member)):
        # Only the form was off ("Anna B" without the full stop): taken as it is, no question.
        member.nickname = fixed
        member.save(update_fields=["nickname"])
        audit.log(request, "member.update", target=audit.member_actor(member), actor=audit.member_actor(member),
                  details="name put into the usual form at the kiosk")

    if mode in (MODE_ORDER, MODE_RANKING):
        if member is not None and member.name_problem is None:
            _start_session(request, MODE_ORDER, session_uid)
        else:
            # A new card, or a member without a usable name (coffee/names.py): the name
            # first, then the order (settings_save).
            _start_session(request, MODE_SETTINGS, session_uid, is_new_user=member is None)
            request.session["then_mode"] = MODE_ORDER
    elif mode == MODE_SETTINGS:
        _start_session(request, MODE_SETTINGS, session_uid, is_new_user=member is None)
    elif mode == MODE_PURCHASE:
        # A purchase is credited to a name everyone can recognise, like an order: the name first.
        if member is not None and member.name_problem is None:
            _start_session(request, MODE_PURCHASE, session_uid)
        else:
            _start_session(request, MODE_SETTINGS, session_uid, is_new_user=member is None)
            request.session["then_mode"] = MODE_PURCHASE
    elif mode == MODE_INFO:
        if member is not None:
            _start_session(request, mode, session_uid)
        else:
            _start_session(request, MODE_SETTINGS, session_uid, is_new_user=True)
    if not privacy.confirmed(member):
        # The notice first, whatever the tap was for - a new card before its name is even asked.
        request.session["privacy_pending"] = True
        request.session["deadline"] = time.time() + PRIVACY_SECONDS


def _tick(request):
    """Applies the order auto-checkout / settings & info auto-logout timeout."""
    mode = request.session.get("mode")
    deadline = request.session.get("deadline")
    if deadline is None:
        return
    if time.time() < deadline:
        return
    if mode == MODE_ORDER and request.session.get("order_auto_checkout", True) and not request.session.get("privacy_pending"):
        _checkout(request)
        return
    _reset_session_state(request)


def _state_context(request, mode):
    if request.session.get("brew"):
        return _brew_context(request)
    if not request.session.get("logged_in"):
        if mode == MODE_RANKING:
            return {"state": "ranking", **_ranking_context(request)}
        capture = _active_capture()
        if capture is not None:
            return {
                "state": "assigning_alt_uid",
                "member": capture.member,
                "capture_error": _translated(capture.last_error),
            }
        if mode == MODE_ORDER:
            thank_you_ctx = _thank_you_context(request)
            if thank_you_ctx is not None:
                return thank_you_ctx
        if not CoffeeSort.offered().exists():
            return _setup_wizard_context(request)
        reader_online = NfcReaderStatus.objects.filter(pk=1, is_online=True).exists()
        return {
            "state": "wait_for_card",
            "reader_online": reader_online,
            # Each mode explains what a tap is about to do, with its own animation.
            "wait_mode": mode if mode in (MODE_INFO, MODE_SETTINGS, MODE_PURCHASE) else MODE_ORDER,
            "checkout_seconds": ORDER_AUTO_CHECKOUT_SECONDS,
            "logout_seconds": AUTO_LOGOUT_SECONDS,
            "free_products": _free_products() if mode == MODE_ORDER else [],
        }

    uid = request.session["uid"]
    deadline = request.session.get("deadline")
    seconds_left = max(0, int(deadline - time.time())) if deadline else None

    if request.session.get("privacy_pending"):
        ctx = _privacy_context(request, uid)
    elif mode == MODE_SETTINGS and request.session.get("settings_view") == "slots":
        ctx = _slots_context(request, uid)
    elif mode == MODE_ORDER:
        ctx = _order_context(request, uid, seconds_left)
    elif mode == MODE_SETTINGS:
        ctx = _settings_context(request, uid, seconds_left)
    elif mode == MODE_INFO:
        ctx = _info_pay_context(request, uid, seconds_left)
    elif mode == MODE_PURCHASE:
        ctx = _purchase_context(request, uid, seconds_left)
    else:
        return {"state": "wait_for_card"}
    # The countdown runs in the browser (static/kiosk/countdown.js) from this fixed moment: the
    # seconds themselves would make every poll's HTML differ, and each swap of a differing panel
    # risks Chromium's renderer crash (CLAUDE.md) - measured: two crashes in three orders.
    shown = request.session.get("shown_deadline") or deadline
    ctx["deadline_ms"] = int(shown * 1000) if shown else None
    ctx["touch_seconds"] = AUTO_LOGOUT_SECONDS
    return ctx


def _privacy_context(request, uid):
    """The privacy notice (coffee/privacy.py) - the same bytes on every poll until it is answered."""
    return {
        "state": "privacy",
        "member": Member.objects.filter(uid=uid).first(),
        "docs_qr_svg": _qr_svg(privacy.DOCS_URL),
        **privacy.notice_context(),
    }


@require_POST
def privacy_confirm(request):
    """"Understood": from now on this member may book, and the session goes on to what the tap was for
    - with its own fresh countdown, so an order isn't checked out the moment the notice is gone."""
    if request.session.get("logged_in") and request.session.get("privacy_pending"):
        uid = request.session["uid"]
        member = Member.objects.filter(uid=uid).first()
        if member is not None:
            privacy.confirm(member)
            who = audit.member_actor(member)
            audit.log(request, "member.privacy_confirm", target=who, actor=who, details=f"notice {privacy.VERSION}")
        else:  # a new card: confirmed with its name (settings_save), the first thing stored about it
            request.session["privacy_confirmed"] = privacy.VERSION
        request.session.pop("privacy_pending", None)
        _start_session(request, request.session["mode"], uid, is_new_user=request.session.get("is_new_user", False))
    return _render_panel(request)


@require_POST
def privacy_decline(request):
    """"No": logged out, nothing booked, nothing stored about a new card."""
    if request.session.get("logged_in") and request.session.get("privacy_pending"):
        uid = request.session["uid"]
        member = Member.objects.filter(uid=uid).first()
        who = audit.member_actor(member) if member is not None else f"card {uid}"
        audit.log(request, "member.privacy_decline", target=who, actor=who, details=f"notice {privacy.VERSION}")
        _reset_session_state(request)
    return _render_panel(request)


def _rank_badge(uid, ranking_rows):
    """`uid`'s place (1-3) if it is among the top 3 of `ranking_rows` (an "alltime"/"month"
    _ranking_rows() result), None otherwise - the template draws the same medal for it
    that the ranking screen itself shows (icons/medal.svg)."""
    for position, row in enumerate(ranking_rows[:3], start=1):
        if row["uid"] == uid:
            return position
    return None


# More sorts than this and they don't fit the screens (measured at 1024x600: three across on
# Order, four on Settings). Then they come in pages, never a scrolling list: a swipe followed by
# leaving the screen crashed Chromium's renderer 2 times in 8 on the device (2026-10-31) - the
# teardown of a scrolled list. The first page is "For you": the favourite, the member's most
# ordered, everyone's most ordered; then the rest, alphabetical, behind "More sorts".
SORTS_WITHOUT_SCROLL = 12
# (tiles per page, "For you" tiles) - a page is the grid's cells: back/next take one each.
SORT_PAGES_ORDER = (9, 6)       # three by three
SORT_PAGES_SETTINGS = (8, 6)    # four by two, next to the name field
# Under a row of "my coffees" (coffee/slots.py) two rows of sorts are left: three by two.
SORTS_WITHOUT_SCROLL_UNDER_SLOTS = 6
SORT_PAGES_ORDER_UNDER_SLOTS = (6, 3)


def _sort_pages(request, sorts, member, selected_id, layout, without_scroll=SORTS_WITHOUT_SCROLL):
    """The pages of sort tiles for the order or settings screen, and which one shows first (the
    one with the selected sort). The order is worked out once a session and kept in it: every
    poll then renders the same bytes (and the Pi isn't counting orders twice a second)."""
    if len(sorts) <= without_scroll:
        return [{"index": 0, "heading": None, "sorts": sorts}], 0
    cells, for_you = layout
    preferred = request.session.get("sort_order")
    if preferred is None:
        preferred = request.session["sort_order"] = services.preferred_sorts(member, for_you)
    by_id = {sort.pk: sort for sort in sorts}
    first = [by_id[pk] for pk in preferred if pk in by_id][:for_you]
    rest = [sort for sort in sorts if sort not in first]
    # A new card on a new device has neither favourite nor history: no empty "For you" page then.
    pages = [{"heading": _("For you"), "sorts": first, "next": _("More sorts")}] if first else []
    per_page = cells - 2
    chunks = [rest[i:i + per_page] for i in range(0, len(rest), per_page)]
    for number, chunk in enumerate(chunks, 1):
        pages.append({
            "heading": _("More sorts") if len(chunks) == 1 else
            _("More sorts (%(page)s of %(pages)s)") % {"page": number, "pages": len(chunks)},
            "sorts": chunk, "next": _("More") if number < len(chunks) else None,
        })
    for index, page in enumerate(pages):
        page["index"] = index
        page["back"] = index > 0
    current = next((page["index"] for page in pages if any(sort.pk == selected_id for sort in page["sorts"])), 0)
    return pages, current


def _options_text(values: dict, recipe: dict | None = None) -> str:
    """"strength 7 · 200 ml · extra hot" - what differs from the machine's default (a slot stores only
    that), in the panel's language."""
    parts = []
    if values.get("strength") is not None:
        parts.append(_("strength %(level)s") % {"level": values["strength"]})
    if values.get("water_ml") is not None:
        parts.append(_("%(ml)s ml") % {"ml": values["water_ml"]})
    if values.get("temperature") is not None:
        parts.append(_("extra hot") if values["temperature"] >= 2 else _("less hot"))
    return " · ".join(parts)


def _slot_tiles(slots):
    """The order screen's tiles of "my coffees" (coffee/slots.py)."""
    tiles = []
    for slot in slots:
        details = _options_text(my_coffees.options(slot))
        if slot.count > 1:
            details = " · ".join(filter(None, [details, f"{slot.count}×"]))  # the tile has room for little
        tiles.append({"value": f"slot:{slot.position}", "position": slot.position, "name": slot.coffee_sort.name,
                      "details": details or _("as the machine makes it"),
                      "price": slot.coffee_sort.price * slot.count})
    return tiles


def _order_context(request, uid, seconds_left):
    member = Member.objects.filter(uid=uid).first()
    coffee_sorts = list(CoffeeSort.offered())
    shown = request.session.get("shown_order") or [
        request.session.get("order_coffee_id"), request.session.get("order_auto_checkout", True),
        request.session.get("order_slot")]
    shown_coffee, shown_auto = shown[0], shown[1]
    shown_slot = shown[2] if len(shown) > 2 else None
    slots = my_coffees.slots_for(member)
    sort, slot = _chosen(request, member, (shown_coffee, shown_slot)) if member is not None else (None, None)
    selected_id = (sort.pk if sort is not None and slot is None else None) if member is not None else None
    if selected_id is None and slot is None:
        selected_id = coffee_sorts[0].pk if coffee_sorts else None
    tiles = _slot_tiles(slots)
    hide = bool(slots) and member.hide_standard_sorts
    # "My coffees" take the first row: the sorts below get two rows then - or a page of their own,
    # behind "Other sorts", for whoever hid them.
    layout = SORT_PAGES_ORDER if hide or not slots else SORT_PAGES_ORDER_UNDER_SLOTS
    limit = SORTS_WITHOUT_SCROLL if hide or not slots else SORTS_WITHOUT_SCROLL_UNDER_SLOTS
    sort_pages, sort_page = _sort_pages(request, coffee_sorts, member, selected_id, layout, limit)
    if hide:
        for page in sort_pages:
            page["index"] += 1
        sort_pages[0]["back"] = True
        if sort_pages[0]["heading"] is None:
            sort_pages[0]["heading"] = _("Other sorts")
        sort_pages.insert(0, {"index": 0, "heading": _("My coffees"), "slots": tiles, "sorts": [],
                              "next": _("Other sorts"), "back": False})
        sort_page = 0 if slot is not None or selected_id is None else sort_page + 1
    # Nudges members who still owe from before this month rolled over - same "ab dem
    # Vormonat" balance the dashboard pillory uses, so it can't say two different things.
    month_reminder_balance = None
    alltime_badge = None
    month_badge = None
    if member is not None:
        prior_balance = services.balance_before_current_month(member)
        if prior_balance < 0:
            month_reminder_balance = -prior_balance
        # Shares the ranking screen's cached rows - the order screen is polled as well. A place
        # on the podium is the ranking too, shown to whoever stands next to the machine.
        if "alltime" in _ranking_views(request):
            alltime_badge = _rank_badge(uid, _ranking_rows(request, "alltime"))
            month_badge = _rank_badge(uid, _ranking_rows(request, "month"))
    return {
        "state": "order",
        "member": member,
        "sort_pages": sort_pages,
        "sort_page": sort_page,
        "slot_tiles": [] if hide else tiles,
        "selected_coffee_id": selected_id,
        "selected_slot": f"slot:{slot.position}" if slot is not None else None,
        "auto_checkout": shown_auto,
        "month_reminder_balance": month_reminder_balance,
        "alltime_badge": alltime_badge,
        "month_badge": month_badge,
        "seconds_left": seconds_left,
    }


def _settings_context(request, uid, seconds_left):
    member = Member.objects.filter(uid=uid).first()
    sorts = list(CoffeeSort.offered())
    pending_favourite = request.session.get("pending_favourite")
    if pending_favourite:
        favourite = int(pending_favourite)
    elif member is not None and member.favourite_coffee_id:
        favourite = member.favourite_coffee_id
    else:  # a new card starts on the first sort, as the old dropdown did
        favourite = sorts[0].pk if sorts else None
    sort_pages, sort_page = _sort_pages(request, sorts, member, favourite, SORT_PAGES_SETTINGS)
    pending = request.session.get("pending_nickname")
    if pending is not None:
        nickname = pending
    elif member is not None and member.name_problem is not None:
        # A start from the old name, so a bare "Anna" only needs her initial (coffee/names.py).
        nickname = names.suggestion(member.nickname)
    else:
        nickname = member.nickname if member else ""
    return {
        "state": "settings",
        "uid": uid,
        "is_new_user": request.session.get("is_new_user", False),
        # Came to order or to book a purchase, but the name has to be sorted out first.
        "name_required": bool(request.session.get("then_mode")) and member is not None,
        "then_mode": request.session.get("then_mode"),
        "name_error": names.message(request.session.get("name_error")),
        "nickname": nickname,
        "favourite_coffee_id": favourite,
        "sort_pages": sort_pages,
        "sort_page": sort_page,
        "seconds_left": seconds_left,
    }


def _slots_context(request, uid):
    """The editor of "my coffees" (coffee/slots.py): three slots side by side, each a sort and what its
    machine product lets them set. Built once - its buttons only change text and hidden fields
    (static/kiosk/slots_editor.js), so the panel stays the same bytes until Save."""
    member = Member.objects.filter(uid=uid).first()
    sorts = list(CoffeeSort.offered())
    by_position = {slot.position: slot for slot in my_coffees.slots_for(member)}
    cards = []
    for position in MemberSlot.POSITIONS:
        slot = by_position.get(position)
        made = my_coffees.effective(slot) if slot is not None else {}
        cards.append({"position": position, "sort_id": slot.coffee_sort_id if slot else "",
                      "sort_name": slot.coffee_sort.name if slot else "", "count": slot.count if slot else 1,
                      "strength": made.get("strength"), "water_ml": made.get("water_ml"),
                      "temperature": made.get("temperature")})
    return {
        "state": "slots",
        "member": member,
        "cards": cards,
        "max_count": MemberSlot.MAX_COUNT,
        "recipes": {str(sort.pk): {"name": sort.name, "recipe": my_coffees.recipe(sort)} for sort in sorts},
        "sort_ids": [""] + [str(sort.pk) for sort in sorts],
        "hide_standard_sorts": member.hide_standard_sorts if member else False,
        "favourite_slot": member.favourite_slot if member else None,
        "notice": request.session.get("slots_notice"),
    }


@require_POST
def slots_open(request):
    if _in_session(request, MODE_SETTINGS) and Member.objects.filter(uid=request.session["uid"]).exists():
        request.session["settings_view"] = "slots"
        request.session.pop("slots_notice", None)
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
        request.session.pop("shown_deadline", None)
    return _render_panel(request)


@require_POST
def slots_close(request):
    if _in_session(request, MODE_SETTINGS):
        request.session.pop("settings_view", None)
        request.session.pop("slots_notice", None)
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
        request.session.pop("shown_deadline", None)
    return _render_panel(request)


@require_POST
def slots_save(request):
    """Save on the editor: each slot within its sort's recipe (my_coffees.clean), the preselected
    one, and whether the sorts hide behind them on the order screen."""
    if _in_session(request, MODE_SETTINGS) and request.session.get("settings_view") == "slots":
        member = Member.objects.filter(uid=request.session["uid"]).first()
        if member is not None:
            saved = my_coffees.save_form(member, request.POST)
            who = audit.member_actor(member)
            audit.log(request, "member.slots_update", target=who, actor=who, details="; ".join(
                f"{p}: {my_coffees.describe(slot)}" for p, slot in saved.items()) or "none")
            request.session["slots_notice"] = True
        request.session["deadline"] = time.time() + AUTO_LOGOUT_SECONDS
        request.session.pop("shown_deadline", None)
    return _render_panel(request)


def _payment_method_qr_row(balance, member=None):
    """QR codes so a member can scan-to-pay what they owe via an admin-configured
    method (PayPal, a PayPal pool, a GiroCode for their banking app, ...) straight from the
    kiosk screen - only methods with something to scan and only while something is actually
    owed (see PaymentMethod.qr_payload)."""
    owed = -balance if balance < 0 else Decimal("0.00")
    if owed <= 0:
        return []
    entries = []
    for method in PaymentMethod.objects.filter(is_active=True):
        payload = method.qr_payload(owed, member)
        if payload:
            entries.append({"name": method.name, "qr_svg": _qr_svg(payload)})
    return entries


def _info_pay_context(request, uid, seconds_left):
    member = Member.objects.filter(uid=uid).first()
    balance = services.account_balance(member) if member else Decimal("0.00")
    pay_default = -balance if balance < 0 else Decimal("0.00")
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
        "payment_method_qr_row": _payment_method_qr_row(balance, member),
        "seconds_left": seconds_left,
    }


# Item tiles per page on the purchase screen - two rows of four at 1024x600, above the quantity
# and price fields. More come in pages like the sorts, never a scrolling list.
ITEMS_PER_PAGE = 8


def _item_pages(entries):
    """`entries` in pages of tiles for the purchase and stock-take screens - one page while they
    fit, else pages with a back and a next tile (sort_pages.js turns them)."""
    if len(entries) <= ITEMS_PER_PAGE:
        return [{"index": 0, "entries": entries}]
    per_page = ITEMS_PER_PAGE - 2
    chunks = [entries[i:i + per_page] for i in range(0, len(entries), per_page)]
    return [{"index": index, "entries": chunk, "back": index > 0, "next": index < len(chunks) - 1}
            for index, chunk in enumerate(chunks)]


def _purchase_context(request, uid, seconds_left):
    member = Member.objects.filter(uid=uid).first()
    items = list(InventoryItem.objects.filter(is_active=True))
    may_count = bool(member and member.may_take_stock)
    notice = request.session.get("inventory_notice")
    return {
        "state": "purchase",
        "member": member,
        "balance": services.account_balance(member) if member else Decimal("0.00"),
        "item_pages": _item_pages(items),
        "may_count": may_count,
        "stock_pages": _item_pages(services.stock_levels(items)) if may_count else [],
        "inventory_page": request.session.get("inventory_page", "purchase") if may_count else "purchase",
        "notice": {**notice, "quantity": Decimal(notice["quantity"]),
                   "price": Decimal(notice.get("price") or "0")} if notice else None,
        "inventory_error": _translated(request.session.get("inventory_error")),
        "seconds_left": seconds_left,
    }


def _ranking_context(request):
    views = _ranking_views(request)
    view = request.session.get("ranking_view")
    if view not in views:
        view = views[0]
    rows = _ranking_rows(request, view)
    context = {"ranking_view": view, "rows": rows}
    if view == "runaways":
        context["banner"] = "Ran off"
        context["ticker_seconds"] = 8 + 4 * len(rows)
    elif view == "pillory":
        context["banner"] = "Shame" if rows else "No shame"
        # The ticker under the walk of shame scrolls through every debtor; a longer list
        # scrolls longer rather than faster, so each name stays readable.
        context["ticker_seconds"] = 8 + 4 * len(rows)
    else:
        first, second, third = (rows + [None, None, None])[:3]
        # All time is decided: left to right as the podium stands. The month is still running -
        # its GIF is the vault, and the names are the standings so far, in order. A place
        # nobody holds yet stays empty either way.
        if view == "alltime":
            context["podium"] = [(2, second), (1, first), (3, third)]
        else:
            context["podium"] = [(1, first), (2, second), (3, third)]
        # The banner is retro, and retro is English - only the month's name is translated.
        context["banner"] = "All time" if view == "alltime" else date_format(timezone.localtime(), "F")
    return context


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
    if view == "runaways":  # this month's: whoever ran off in March has a clean slate in April
        return machine.runaway_board(since=services.current_month_start())
    if view == "pillory":
        return [
            {"name": e["member"].display_name(), "balance": e["balance"]}
            for e in services.deposit_pillory()
        ]
    members = services.ranking_current_month(limit=3) if view == "month" else services.ranking_all_time(limit=3)
    return [{"uid": m.uid, "name": m.display_name(), "cups": m.cups} for m in members]
