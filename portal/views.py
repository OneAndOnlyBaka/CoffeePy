import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.sessions.models import Session
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from coffee import machine, privacy, services, turns
from coffee import slots as my_coffees
from coffee.models import (
    AltUidCapture, AuditLogEntry, CASH_METHOD_NAME, CoffeeSort, InventoryItem, MachineDispense, MachineTurn,
    MemberDevice, MemberLinkToken, MemberSlot, Payment, PaymentMethod,
)

from dashboard import audit
from dashboard.models import InventoryPolicy

from .decorators import ROLLING_SESSION_SECONDS, portal_login_required, portal_privacy_required

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
        "recent_payments": member.payments.select_related("purchase__item")[:10],
        "privacy_confirmed": privacy.confirmed(member),
        "machine_on": machine.status()["machine"].configured,
    }
    return render(request, "portal/index.html", ctx)


@portal_login_required
def privacy_notice(request):
    """The privacy notice on the phone (coffee/privacy.py) - for a phone linked before there was one;
    a phone linked since was linked by a member who had confirmed it at the kiosk."""
    member = request.member
    target = request.GET.get("next", "")
    if not target.startswith("/portal/") or target.startswith("//"):  # only back into the portal
        target = reverse("portal:index")
    if request.method == "POST":
        if not privacy.confirmed(member):
            privacy.confirm(member)
            who = audit.member_actor(member)
            audit.log(request, "member.privacy_confirm", target=who, actor=who, details=f"notice {privacy.VERSION}")
        return redirect(target)
    return render(request, "portal/privacy.html", {
        "member": member, "confirmed": privacy.confirmed(member), "next": target, **privacy.notice_context()})


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


def _payment_methods_context(balance: Decimal, member=None):
    """Cash is always offered and isn't a DB row; configured methods only show a link (or
    for a GiroCode the transfer to copy - the phone can't scan its own screen) once there's
    actually something owed (a positive balance means the member is in credit, and pool/PayPal
    links can't sensibly represent "pay me nothing")."""
    owed = -balance if balance < 0 else Decimal("0.00")
    methods = []
    for method in PaymentMethod.objects.filter(is_active=True):
        link = method.build_link(owed) if owed > 0 else ""
        transfer = method.transfer_details(owed, member) if owed > 0 and not link else []
        methods.append({"method": method, "link": link, "transfer": transfer})
    return {"payment_methods": methods, "owed": owed}


@portal_login_required
@portal_privacy_required
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
    ctx.update(_payment_methods_context(balance, member))
    return render(request, "portal/pay.html", ctx)


@portal_login_required
@portal_privacy_required
def purchase(request):
    """Booking a purchase from the phone - and, for the members the admin picked
    (Member.may_take_stock), the stock-take. A <select> is fine here: only the kiosk's
    browser can't open one."""
    if not InventoryPolicy.current().members_book:
        raise Http404
    member = request.member
    items = InventoryItem.objects.filter(is_active=True)
    error = None
    if request.method == "POST" and request.POST.get("form") == "stock" and member.may_take_stock:
        counted = []
        for item in items:
            raw = request.POST.get(f"count-{item.pk}", "").strip()
            if not raw:
                continue
            quantity = services.parse_quantity(raw)
            if quantity is None:
                error = _("%(item)s: enter a number of at least 0.") % {"item": item.name}
                break
            counted.append((item, quantity))
        if error is None and counted:
            for item, quantity in counted:
                services.record_stock_count(item, quantity, member.display_name())
            who = audit.member_actor(member)
            audit.log(request, "stock.count", actor=who, details=", ".join(
                f"{item.name} {quantity.normalize():f}" for item, quantity in counted))
            return redirect(reverse("portal:purchase") + "?counted=1")
    elif request.method == "POST":
        item_id = request.POST.get("item", "")
        item = items.filter(pk=item_id).first() if item_id.isdigit() else None
        quantity = services.parse_quantity(request.POST.get("quantity", ""))
        price = services.parse_quantity(request.POST.get("price", ""))
        if item is None or not quantity or not price:
            error = _("Pick an item and enter a quantity and a price above 0.")
        else:
            booked = services.book_purchase(member, item, quantity, price.quantize(Decimal("0.01")))
            who = audit.member_actor(member)
            audit.log(request, "purchase.create", target=who, actor=who, details=services.describe_purchase(booked))
            return redirect(reverse("portal:purchase") + "?booked=1")
    return render(request, "portal/purchase.html", {
        "member": member,
        "balance": services.account_balance(member),
        "items": items,
        "error": error,
        "booked": request.GET.get("booked") == "1",
        "counted": request.GET.get("counted") == "1",
        "purchases": member.purchases.select_related("item")[:10],
        "levels": services.stock_levels(items) if member.may_take_stock else [],
    })


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
@portal_privacy_required
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


@portal_login_required
@portal_privacy_required
def coffees(request):
    """"My coffees" on the phone (coffee/slots.py) - the same three slots as the kiosk's editor, with a
    form a phone handles better. Values outside what the machine allows are put within it on saving."""
    member = request.member
    saved = False
    if request.method == "POST":
        found = my_coffees.save_form(member, request.POST)
        who = audit.member_actor(member)
        audit.log(request, "member.slots_update", target=who, actor=who, details="; ".join(
            f"{p}: {my_coffees.describe(slot)}" for p, slot in found.items()) or "none")
        saved = True
    by_position = {slot.position: slot for slot in my_coffees.slots_for(member)}
    cards = []
    for position in MemberSlot.POSITIONS:
        slot = by_position.get(position)
        made = my_coffees.effective(slot) if slot is not None else {}
        recipe = my_coffees.recipe(slot.coffee_sort) if slot is not None else {}
        cards.append({"position": position, "slot": slot, "made": made, "recipe": recipe})
    return render(request, "portal/coffees.html", {
        "member": member, "cards": cards, "sorts": CoffeeSort.offered(), "max_count": MemberSlot.MAX_COUNT,
        "counts": range(1, MemberSlot.MAX_COUNT + 1), "saved": saved,
    })


# --- ordering from the phone: the line for the machine (coffee/turns.py) --------------------------

# "Booked" stays on the page this long after the member's brew ended.
TURN_RESULT_SECONDS = 120


def _choices(member):
    """What the phone orders: the member's slots and the sorts the machine makes - one outlet, so only
    what goes through the line. A sort without the machine is booked at the kiosk, as ever."""
    slots = [s for s in my_coffees.slots_for(member) if machine.product_for(s.coffee_sort) is not None]
    sorts = [s for s in CoffeeSort.offered() if machine.product_for(s) is not None]
    return slots, sorts


def _turn_context(member):
    line = turns.advance()
    turn = next((t for t in line if t.member_id == member.pk), None)
    ctx = {"member": member, "turn": turn, "available": machine.brewing_available(),
           "holder": line[0] if line and line[0].state != MachineTurn.WAITING else None,
           "waiting": [t for t in line if t.state == MachineTurn.WAITING]}
    if turn is not None:
        ctx["ahead"] = turns.ahead(turn, line)
        product = machine.product_for(turn.coffee_sort)
        water = (turn.options or {}).get("water_ml")
        state = machine.status()
        if turn.state == MachineTurn.CALLED:
            ctx["seconds_left"] = max(0, int((turns.deadline(turn) - timezone.now()).total_seconds()))
            ctx["blocking"] = [machine.alert_label(key) for key in state["blocking"]]
            ctx["tasks"] = [t for t in machine.tasks_before(product, water, turn.count)
                            if t not in ctx["blocking"]] if product else []
        elif turn.state == MachineTurn.BREWING:
            progress = machine.chain_progress(turn.dispense) if turn.dispense_id else None
            ctx["cup"] = progress["cup"] if progress else 1
            ctx["tasks"] = machine.tasks_after(product, water, turn.count) if product else []
    else:
        since = timezone.now() - timedelta(seconds=TURN_RESULT_SECONDS)
        ctx["last"] = MachineTurn.objects.filter(member=member, finished_at__gte=since).exclude(
            state=MachineTurn.CANCELLED).order_by("-finished_at").first()
        if ctx["last"] is not None and ctx["last"].dispense_id:
            ctx["last_made"] = machine.chain_progress(ctx["last"].dispense)["done"]
    return ctx


@portal_login_required
@portal_privacy_required
def order(request):
    """A coffee from the phone: in line with the kiosk's members, Start once it is the member's turn and
    the cup is under the outlet. Booked cup by cup as the machine makes them, like at the kiosk."""
    member = request.member
    action = request.POST.get("action") if request.method == "POST" else None
    who = audit.member_actor(member)
    turn = turns.current_for(member)
    if action == "join" and machine.brewing_available():
        slots, sorts = _choices(member)
        picked = request.POST.get("choice", "")
        slot = next((s for s in slots if picked == f"slot:{s.position}"), None)
        sort = slot.coffee_sort if slot is not None else next((s for s in sorts if picked == str(s.pk)), None)
        if sort is not None:
            turn = turns.join(member, sort, slot, MachineTurn.SOURCE_PHONE)
            audit.log(request, "machine.queue_join", target=who, actor=who,
                      details=f"No. {turn.number}: {sort.name}{f' x{turn.count}' if turn.count > 1 else ''} (phone)")
    elif action == "start" and turn is not None:
        dispense = turns.start(turn, MachineDispense.SOURCE_PHONE)
        if dispense is not None:
            audit.log(request, "machine.brew", target=who, actor=who, details=f"{dispense.product_name} for "
                      f"{turn.coffee_sort.name}{f' ({turn.count} cups)' if turn.count > 1 else ''}, from the phone")
    elif action == "leave" and turn is not None and turns.cancel(turn):
        audit.log(request, "machine.queue_leave", target=who, actor=who, details=f"No. {turn.number} (phone)")
    if action:
        return redirect("portal:order")
    slots, sorts = _choices(member)
    choices = [{"value": f"slot:{s.position}", "name": s.coffee_sort.name, "count": s.count, "slot": s.position,
                "price": s.coffee_sort.price * s.count, "options": my_coffees.options_text(my_coffees.options(s))} for s in slots]
    choices += [{"value": str(s.pk), "name": s.name, "count": 1, "price": s.price} for s in sorts]
    ctx = _turn_context(member)
    ctx.update({"choices": choices, "preselected": _preselected(member, slots, sorts)})
    return render(request, "portal/order.html", ctx)


def _preselected(member, slots, sorts):
    """As on the kiosk: the slot they preselect, else the favourite sort, else the first."""
    if member.favourite_slot in {s.position for s in slots}:
        return f"slot:{member.favourite_slot}"
    if member.favourite_coffee_id in {s.pk for s in sorts}:
        return str(member.favourite_coffee_id)
    return f"slot:{slots[0].position}" if slots else (str(sorts[0].pk) if sorts else "")


@portal_login_required
@portal_privacy_required
def order_status(request):
    """The page's turn card, polled every 2 s."""
    return render(request, "portal/_turn.html", _turn_context(request.member))
