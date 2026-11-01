"""Query logic ported from the legacy DatabaseConnector, using the Django ORM instead of
string-built SQL."""
import calendar
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, DecimalField, F, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, ExtractHour, ExtractIsoWeekDay, TruncMonth, TruncQuarter
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dates import WEEKDAYS_ABBR

from .models import (
    PURCHASE_METHOD_NAME,
    AuditLogEntry,
    CoffeeSort,
    InventoryItem,
    Member,
    Order,
    Payment,
    Purchase,
    StockCount,
    payment_method_label,
)


def current_month_start() -> datetime:
    now = timezone.localtime()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def today_start() -> datetime:
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def last_active_at(member: Member):
    """Most recent Order or Payment timestamp for a member, or None if they have
    neither - used by the member-merge flow (dashboard.views.member_merge_confirm) to
    decide whose nickname survives the merge: whichever card was used more recently."""
    last_order = Order.objects.filter(member=member).order_by("-created_at").values_list("created_at", flat=True).first()
    last_payment = Payment.objects.filter(member=member).order_by("-created_at").values_list("created_at", flat=True).first()
    candidates = [t for t in (last_order, last_payment) if t is not None]
    return max(candidates) if candidates else None


def current_week_start() -> datetime:
    """Monday 00:00 of the current (ISO) week."""
    today = today_start()
    return today - timedelta(days=today.isoweekday() - 1)


def total_cups(member: Member) -> int:
    total = Order.objects.filter(member=member).aggregate(total=Sum("machine_strokes"))["total"]
    return int(total or 0)


def month_cups(member: Member) -> int:
    total = Order.objects.filter(
        member=member, created_at__gte=current_month_start()
    ).aggregate(total=Sum("machine_strokes"))["total"]
    return int(total or 0)


def today_cups(member: Member) -> int:
    """Cups (machine strokes) this member has ordered today - used to pick which "thank
    you" tier/gif to show right after an order (see kiosk._thank_you_tier)."""
    total = Order.objects.filter(
        member=member, created_at__gte=today_start()
    ).aggregate(total=Sum("machine_strokes"))["total"]
    return int(total or 0)


def week_cups(member: Member) -> int:
    """Cups (machine strokes) this member has ordered since Monday - the kiosk's "thank
    you" screen overrides its usual daily tier with a special gif above 100."""
    total = Order.objects.filter(
        member=member, created_at__gte=current_week_start()
    ).aggregate(total=Sum("machine_strokes"))["total"]
    return int(total or 0)


def total_payments(member: Member) -> Decimal:
    total = Payment.objects.filter(member=member).aggregate(total=Sum("value"))["total"]
    return total or Decimal("0.00")


def total_deposit(member: Member, before: datetime | None = None) -> Decimal:
    qs = Order.objects.filter(member=member)
    if before is not None:
        qs = qs.filter(created_at__lt=before)
    total = qs.aggregate(total=Sum("value"))["total"]
    return total or Decimal("0.00")


def account_balance(member: Member, round_value: bool = True) -> Decimal:
    balance = total_payments(member) - total_deposit(member)
    return round(balance, 2) if round_value else balance


def months_ago(months: int) -> datetime:
    """Now, `months` calendar months back - the 31st becomes the last day of a shorter month."""
    now = timezone.now()
    year, month = divmod(now.year * 12 + now.month - 1 - months, 12)
    return now.replace(year=year, month=month + 1, day=min(now.day, calendar.monthrange(year, month + 1)[1]))


def _member_total(model):
    """A member's sum of `model.value` as an annotation, 0 without rows."""
    return Coalesce(Subquery(model.objects.filter(member=OuterRef("pk")).values("member")
                             .annotate(total=Sum("value")).values("total"), output_field=DecimalField()),
                    Value(Decimal("0")), output_field=DecimalField())


def departed_members_due(months: int) -> list[Member]:
    """Departed members whose departure is `months` months past and whose balance is settled -
    suggested for deletion on the Members page and as a task sign on the kiosk. Only suggested:
    the period is measured with the device's clock, which can be wrong (a flat RTC battery), and
    an open balance, owed either way, is the admin's call; so is keeping one with a reason
    (dashboard.models.DeletionHold). One query - the kiosk asks on every
    poll of its wait screen. The balance is worked out here, not compared in SQL: SQLite sums
    decimals as floats."""
    if not months:
        return []
    candidates = (Member.objects.filter(departed_at__lt=months_ago(months), deletion_hold__isnull=True)
                  .annotate(paid=_member_total(Payment), drunk=_member_total(Order)).order_by("departed_at"))
    return [member for member in candidates if round(Decimal(member.paid) - Decimal(member.drunk), 2) == 0]


def member_data_response(member: Member, own: bool = False):
    """member_data as a JSON download - the same file from the dashboard and the portal."""
    response = HttpResponse(json.dumps(member_data(member, own), indent=1, ensure_ascii=False),
                            content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="coffeepy-member-{member.uid}-{timezone.localdate().isoformat()}.json"')
    return response


def member_data(member: Member, own: bool = False) -> dict:
    """Everything the device stores about one member, for their right of access (Art. 15 GDPR):
    the dashboard's per-member export, and with `own` the member's own from the portal. Audit
    entries are found by "(uid N)" - how dashboard.audit.member_actor names a member, as actor or
    as target. `own` leaves out other people's data in them: an admin's user name and address
    become the role ("dashboard", "system")."""
    def when(value):
        return value.isoformat() if value else None

    tag = f"(uid {member.uid})"

    def other(entry):
        return own and tag not in entry.actor
    return {
        "exported_at": timezone.now().isoformat(),
        "member": {
            "uid": member.uid,
            "name": member.nickname,
            "favourite_coffee": member.favourite_coffee.name if member.favourite_coffee else None,
            "departed_at": when(member.departed_at),
            "balance": str(account_balance(member)),
            "cups_total": total_cups(member),
        },
        "cards": [{"uid": member.uid, "added_at": None}] + [
            {"uid": card.uid, "added_at": when(card.created_at)} for card in member.alt_cards.order_by("created_at")],
        "phones": [{"label": device.label, "linked_at": when(device.created_at), "last_seen_at": when(device.last_seen_at)}
                   for device in member.devices.order_by("created_at")],
        "orders": [{"at": when(order.created_at), "coffee_sort": order.coffee_sort.name if order.coffee_sort else None,
                    "cups": order.machine_strokes, "value": str(order.value), "info": order.info}
                   for order in member.orders.select_related("coffee_sort").order_by("created_at")],
        "payments": [{"at": when(payment.created_at), "value": str(payment.value), "method": payment.method_name,
                      "info": payment.info} for payment in member.payments.order_by("created_at")],
        "purchases": [{"at": when(purchase.created_at), "item": purchase.item.name, "quantity": str(purchase.quantity),
                       "price": str(purchase.price), "info": purchase.info}
                      for purchase in member.purchases.select_related("item").order_by("created_at")],
        "audit_log": [{"at": when(entry.created_at), "action": entry.action,
                       "actor": entry.source if other(entry) else entry.actor,
                       "target": entry.target, "details": entry.details, "source": entry.source,
                       "address": None if other(entry) else entry.ip, "outcome": entry.outcome}
                      for entry in AuditLogEntry.objects.filter(Q(actor__contains=tag) | Q(target__contains=tag))
                      .order_by("created_at")],
    }


def ranking_all_time(limit: int = 7):
    """Members ranked by total machine strokes (all time), highest first. Departed
    members are excluded - they no longer drink here."""
    return (
        Member.objects.filter(departed_at__isnull=True)
        .annotate(cups=Sum("orders__machine_strokes"))
        .filter(cups__isnull=False)
        .order_by("-cups")[:limit]
    )


def ranking_current_month(limit: int = 7):
    """Members ranked by machine strokes ordered this month, highest first. Departed
    members are excluded - they no longer drink here."""
    return (
        Member.objects.filter(departed_at__isnull=True, orders__created_at__gte=current_month_start())
        .annotate(cups=Sum("orders__machine_strokes"))
        .filter(cups__isnull=False)
        .order_by("-cups")[:limit]
    )


def ranking_fingerprint() -> str:
    """Changes whenever what the ranking/pillory screens show could have: a new order or
    payment, a member added, renamed, marked departed or merged away, the month rolling
    over, or anything changed in the dashboard - a corrected or deleted booking leaves the
    max ids where they were, but every dashboard change writes an audit row. Index lookups
    and one small table instead of the full-history aggregates those screens run - on a Pi
    3 with five years of orders, ~10ms against 100-200ms."""
    parts = (
        Order.objects.aggregate(m=Max("id"))["m"],
        Payment.objects.aggregate(m=Max("id"))["m"],
        AuditLogEntry.objects.aggregate(m=Max("id"))["m"],
        list(Member.objects.order_by("uid").values_list("uid", "nickname", "departed_at")),
        current_month_start(),
    )
    return hashlib.sha1(repr(parts).encode()).hexdigest()


def balance_before_current_month(member: Member) -> Decimal:
    """Payments to date minus orders strictly before this month started - i.e. whether a
    member was paid up before the month rolled over ("ab dem Vormonat"). Shared by
    deposit_pillory and the kiosk's month-change payment reminder so the two can't drift
    apart on what "still owing" means."""
    payments = total_payments(member)
    deposit = total_deposit(member, before=current_month_start())
    return round(payments - deposit, 2)


def deposit_pillory(limit: int = 7):
    """Members with a negative balance considering all payments but only orders strictly
    before the start of the current month, most negative first - mirrors
    DatabaseConnector.GetPillorySortedByDecreasing. Departed members are excluded - "Mark
    as departed" is exactly how an admin writes off/acknowledges that debt instead of
    keeping to chase it.

    Aggregates payments/deposits per member in two grouped queries instead of looping
    per-member (the kiosk's idle ranking screen calls this on every ~500ms poll while
    showing the pillory view, so an N+1 pattern here means N+1 queries every poll)."""
    members = list(Member.objects.filter(departed_at__isnull=True))

    payments_by_member = dict(
        Payment.objects.filter(member__departed_at__isnull=True)
        .values("member_id")
        .annotate(total=Sum("value"))
        .values_list("member_id", "total")
    )
    deposits_by_member = dict(
        Order.objects.filter(member__departed_at__isnull=True, created_at__lt=current_month_start())
        .values("member_id")
        .annotate(total=Sum("value"))
        .values_list("member_id", "total")
    )

    entries = []
    for member in members:
        payments = payments_by_member.get(member.uid, Decimal("0.00"))
        deposits = deposits_by_member.get(member.uid, Decimal("0.00"))
        balance = round(payments - deposits, 2)
        if balance < 0:
            entries.append(
                {
                    "member": member,
                    "payments": round(payments, 2),
                    "deposits": round(deposits, 2),
                    "balance": balance,
                }
            )
    entries.sort(key=lambda e: e["balance"])
    return entries[:limit]


def dashboard_overview() -> dict:
    """The tiles at the top of the dashboard's overview: cups today and this month (machine
    strokes, like everywhere), active members, and what the pillory adds up to."""
    def cups(since):
        return Order.objects.filter(created_at__gte=since).aggregate(n=Sum("machine_strokes"))["n"] or 0

    debtors = deposit_pillory(limit=None)
    return {
        "cups_today": cups(today_start()),
        "cups_month": cups(current_month_start()),
        "active_members": Member.objects.filter(departed_at__isnull=True).count(),
        "debtors": len(debtors),
        "open_amount": -sum((e["balance"] for e in debtors), Decimal("0")),
    }


def orders_in_span(days: int | None = None):
    qs = Order.objects.select_related("member", "coffee_sort")
    if days is not None:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return qs


def payments_in_span(days: int | None = None):
    qs = Payment.objects.select_related("member")
    if days is not None:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return qs


def _step_back_one_month(dt: datetime) -> datetime:
    prev_month = dt.month - 1 or 12
    prev_year = dt.year - 1 if dt.month == 1 else dt.year
    return dt.replace(year=prev_year, month=prev_month, day=1)


def monthly_totals(months: int = 12):
    """Total order value, money paid and purchases per month, oldest first. A purchase credited to
    a member is a payment in kind (PURCHASE_METHOD_NAME) - left out of "payments", so that stays the
    money that came in; "purchases" counts every purchase, the coffee box's too."""
    month_starts = []
    cursor = current_month_start()
    for _ in range(months):
        month_starts.append(cursor)
        cursor = _step_back_one_month(cursor)
    month_starts.reverse()
    since = month_starts[0]

    order_rows = {
        row["month"].strftime("%Y-%m"): row["total"]
        for row in Order.objects.filter(created_at__gte=since)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Sum("value"))
    }
    def per_month(rows, field):
        return {
            row["month"].strftime("%Y-%m"): row["total"]
            for row in rows.filter(created_at__gte=since)
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(total=Sum(field))
        }
    payment_rows = per_month(Payment.objects.exclude(method_name=PURCHASE_METHOD_NAME), "value")
    purchase_rows = per_month(Purchase.objects.all(), "price")

    labels = [d.strftime("%Y-%m") for d in month_starts]
    return {
        "labels": labels,
        "orders": [float(order_rows.get(label, 0)) for label in labels],
        "payments": [float(payment_rows.get(label, 0)) for label in labels],
        "purchases": [float(purchase_rows.get(label, 0)) for label in labels],
    }


def payment_method_totals():
    """Total paid and payment count per method_name, all time, highest total first -
    answers "how do members actually pay" for the dashboard statistics page."""
    rows = (
        Payment.objects.values("method_name")
        .annotate(total=Sum("value"), count=Count("id"))
        .order_by("-total")
    )
    return {
        "labels": [payment_method_label(r["method_name"]) for r in rows],
        "totals": [float(r["total"]) for r in rows],
        "counts": [r["count"] for r in rows],
    }


@transaction.atomic
def _credit(purchase: Purchase) -> Payment:
    return Payment.objects.create(member=purchase.member, value=purchase.price, method_name=PURCHASE_METHOD_NAME,
                                  created_at=purchase.created_at, info=purchase.item.name)


@transaction.atomic
def book_purchase(member: Member | None, item: InventoryItem, quantity: Decimal, price: Decimal, info: str = "",
                  created_at: datetime | None = None) -> Purchase:
    """A purchase and the credit for its full price, together or not at all. Without a member it
    was paid from the coffee box, and nobody is credited."""
    purchase = Purchase(member=member, item=item, quantity=quantity, price=price,
                        created_at=created_at or timezone.now(), info=info)
    if member is not None:
        purchase.payment = _credit(purchase)
    purchase.save()
    return purchase


@transaction.atomic
def update_purchase(purchase: Purchase) -> Purchase:
    """Saves an edited purchase and moves its credit along - member, price and time; from a member
    to the coffee box the credit goes, the other way round one is made."""
    old_payment = purchase.payment
    if purchase.member is None:
        purchase.payment = None
    elif old_payment is None:
        purchase.payment = _credit(purchase)
    else:
        Payment.objects.filter(pk=old_payment.pk).update(
            member=purchase.member, value=purchase.price, created_at=purchase.created_at, info=purchase.item.name)
    purchase.save()
    if old_payment is not None and purchase.payment is None:
        old_payment.delete()
    return purchase


def delete_purchase(purchase: Purchase) -> None:
    """Deleting the credit takes the purchase with it (CASCADE)."""
    if purchase.payment_id:
        purchase.payment.delete()
    else:
        purchase.delete()


def parse_quantity(value) -> Decimal | None:
    """A counted or bought quantity typed either way, "1,5" or "1.5" (the German keyboard has
    the comma); None for anything that isn't a finite number of at least 0."""
    try:
        quantity = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not quantity.is_finite() or quantity < 0 or quantity >= Decimal("10000000"):
        return None
    return quantity.quantize(Decimal("0.001"))


def describe_purchase(purchase: Purchase) -> str:
    """"#12: 1 kg Coffee beans, 14.99 EUR" - the audit log's details."""
    quantity = " ".join(filter(None, (f"{purchase.quantity.normalize():f}", purchase.item.unit)))
    paid = "" if purchase.member_id else " (coffee box)"
    return f"#{purchase.pk}: {quantity} {purchase.item.name}, {purchase.price} EUR{paid}"


def record_stock_count(item: InventoryItem, quantity: Decimal, counted_by: str) -> StockCount:
    return StockCount.objects.create(item=item, quantity=quantity, counted_by=counted_by)


def stock_levels(items=None) -> list[dict]:
    """Per item: the newest count, what was bought since, and so what should be on the shelf.
    Without a count the stock is everything ever bought. Two queries per item - the list is
    a handful of things, not the order history."""
    items = InventoryItem.objects.filter(is_active=True) if items is None else items
    levels = []
    for item in items:
        count = item.counts.order_by("-created_at", "-id").first()
        bought = item.purchases.all()
        if count is not None:
            bought = bought.filter(created_at__gt=count.created_at)
        # Rounded back: SQLite sums decimals as floats.
        bought_since = Decimal(bought.aggregate(total=Sum("quantity"))["total"] or 0).quantize(Decimal("0.001"))
        levels.append({
            "item": item,
            "count": count,
            "bought_since": bought_since,
            "stock": (count.quantity if count else Decimal("0")) + bought_since,
        })
    return levels


def purchase_totals(months: int = 12) -> dict:
    """What was spent per month and item over the last `months` months - the dashboard's spending
    overview: the months that had purchases, newest first, each with one total per item (in the
    order of "items"), and each item's sum over all of them."""
    since = current_month_start()
    for _ in range(months - 1):
        since = _step_back_one_month(since)
    rows = (Purchase.objects.filter(created_at__gte=since)
            .annotate(month=TruncMonth("created_at"))
            .values("item__name", "month")
            .annotate(total=Sum("price")))
    names = sorted({row["item__name"] for row in rows})
    by_month: dict[str, list] = {}
    for row in rows:
        totals = by_month.setdefault(row["month"].strftime("%Y-%m"), [Decimal("0")] * len(names))
        # Rounded back: SQLite sums decimals as floats.
        totals[names.index(row["item__name"])] += Decimal(row["total"]).quantize(Decimal("0.01"))
    month_rows = [{"label": label, "totals": totals, "sum": sum(totals, Decimal("0"))}
                  for label, totals in sorted(by_month.items(), reverse=True)]
    items = [{"name": name, "sum": sum((row["totals"][i] for row in month_rows), Decimal("0"))}
             for i, name in enumerate(names)]
    return {"months": month_rows, "items": items, "sum": sum((item["sum"] for item in items), Decimal("0"))}


COST_MIN_MONTHS = 3   # months with purchases before a price is suggested at all
COST_MIN_CUPS = 100


def _cups(start, end=None) -> int:
    orders = Order.objects.filter(created_at__gte=start)
    if end is not None:
        orders = orders.filter(created_at__lte=end)
    return orders.aggregate(cups=Sum("machine_strokes"))["cups"] or 0


def _money(value) -> Decimal:
    # Rounded back: SQLite sums decimals as floats.
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _cents(euros: Decimal) -> Decimal:
    return (euros * 100).quantize(Decimal("0.1"))


def cost_per_cup(months: int = 12) -> dict:
    """What a cup cost over the last `months` months (docs/CALCULATIONS.md, "Price suggestion").
    Each item is reckoned over its own stretch and divided by the cups of that stretch: with two
    stock-takes in the period, what was used between the first and the last (counted + bought
    - counted again), valued at the period's average price per unit; otherwise simply what was
    bought, over the whole period. Cups are strokes, as everywhere."""
    start = months_ago(months)
    now = timezone.now()
    items, per_cup, all_counted = [], Decimal("0"), True
    for item in InventoryItem.objects.all():
        bought = item.purchases.filter(created_at__gte=start)
        totals = bought.aggregate(quantity=Sum("quantity"), price=Sum("price"))
        spent = _money(totals["price"])
        counts = list(item.counts.filter(created_at__gte=start).order_by("created_at", "id"))
        if len(counts) >= 2:
            first, last = counts[0], counts[-1]
            between = item.purchases.filter(created_at__gt=first.created_at, created_at__lte=last.created_at)
            added = Decimal(between.aggregate(total=Sum("quantity"))["total"] or 0).quantize(Decimal("0.001"))
            used = max(Decimal("0"), first.quantity + added - last.quantity)
            if not totals["quantity"]:  # nothing bought in the period: the newest price there is
                totals = item.purchases.aggregate(quantity=Sum("quantity"), price=Sum("price"))
            unit_price = Decimal(totals["price"] or 0) / Decimal(totals["quantity"] or 1)
            cost, cups, counted = _money(used * unit_price), _cups(first.created_at, last.created_at), True
        else:
            used, cost, cups, counted = None, spent, _cups(start), False
        if not cost and not spent:
            continue
        all_counted &= counted
        item_per_cup = cost / cups if cups else Decimal("0")
        per_cup += item_per_cup
        items.append({"item": item, "spent": spent, "used": used, "cost": cost, "cups": cups,
                      "counted": counted, "per_cup": item_per_cup, "per_cup_cents": _cents(item_per_cup)})

    revenue = _money(Order.objects.filter(created_at__gte=start).aggregate(total=Sum("value"))["total"])
    spent = sum((row["spent"] for row in items), Decimal("0"))
    # What members marked as departed in the period still owed - written off, paid by everyone else.
    departed = Member.objects.filter(departed_at__gte=start).annotate(paid=_member_total(Payment),
                                                                      drunk=_member_total(Order))
    written_off = sum((max(Decimal("0"), _money(m.drunk) - _money(m.paid)) for m in departed), Decimal("0"))
    purchase_months = (Purchase.objects.filter(created_at__gte=start).annotate(month=TruncMonth("created_at"))
                       .values("month").distinct().count())
    cups = _cups(start)
    return {
        "months": months,
        "start": start,
        "end": now,
        "items": items,
        "per_cup": per_cup,
        "per_cup_cents": _cents(per_cup),
        "counted": bool(items) and all_counted,
        "cups": cups,
        "revenue": revenue,
        "spent": spent,
        "balance": revenue - spent,
        "written_off": written_off,
        "written_off_percent": (written_off * 100 / revenue).quantize(Decimal("0.1")) if revenue else None,
        "enough_data": purchase_months >= min(COST_MIN_MONTHS, months) and cups >= COST_MIN_CUPS and per_cup > 0,
    }


def price_suggestions(per_cup: Decimal, margin_percent: int) -> list[dict]:
    """Per coffee sort: strokes x cost per cup x (1 + margin), rounded up to 5 cents - a
    suggestion next to the current price; changing it stays the admin's decision."""
    step = Decimal("0.05")
    rows = []
    for sort in CoffeeSort.objects.order_by("name"):
        raw = sort.machine_strokes * per_cup * (100 + margin_percent) / 100
        suggestion = (raw / step).to_integral_value(rounding="ROUND_CEILING") * step
        rows.append({
            "sort": sort,
            "suggestion": suggestion,
            "difference_percent": ((suggestion - sort.price) * 100 / sort.price).quantize(Decimal("1"))
            if sort.price else None,
        })
    return rows

def preferred_sorts(member: Member | None, limit: int) -> list[int]:
    """The sorts a member most likely wants, best first: their favourite, then what they order
    most, then what everyone orders most - the kiosk's "For you" when there are more sorts than
    fit on its screen (kiosk.views). Counts orders, not cups: how often a sort is chosen, like
    the statistics page's sort distribution."""
    ids = []

    def add(pk):
        if pk and pk not in ids:
            ids.append(pk)

    if member is not None:
        add(member.favourite_coffee_id)
        mine = Order.objects.filter(member=member, coffee_sort__isnull=False)
        for row in mine.values("coffee_sort").annotate(n=Count("id")).order_by("-n", "coffee_sort__name")[:limit]:
            add(row["coffee_sort"])
    if len(ids) < limit:
        everyone = Order.objects.filter(coffee_sort__isnull=False)
        for row in everyone.values("coffee_sort").annotate(n=Count("id")).order_by("-n", "coffee_sort__name")[:limit * 2]:
            add(row["coffee_sort"])
    return ids[:limit]


def coffee_distribution():
    """Number of orders per coffee sort, all time."""
    rows = (
        Order.objects.filter(coffee_sort__isnull=False)
        .values("coffee_sort__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return {"labels": [r["coffee_sort__name"] for r in rows], "counts": [r["count"] for r in rows]}


def _weekday_labels() -> list[str]:
    """Monday first, in the active language. Django's own abbreviations, so they come
    already translated; resolved to plain strings here because callers json.dumps them."""
    return [str(WEEKDAYS_ABBR[day]) for day in range(7)]


def hourly_distribution():
    """Cups per hour of the day, all time - when coffee actually gets drawn.

    Counts machine strokes, not orders, so a jug counts for the two cups it is. The hour
    is the local one (TIME_ZONE), not UTC, which is the only reading that means anything
    here: "most coffee is pulled at 09:00" is a statement about the morning, and the
    database stores everything in UTC."""
    rows = (
        Order.objects.annotate(hour=ExtractHour("created_at"))
        .values("hour")
        .annotate(cups=Sum("machine_strokes"))
    )
    by_hour = {row["hour"]: int(row["cups"] or 0) for row in rows}
    return {
        "labels": [f"{hour:02d}" for hour in range(24)],
        "cups": [by_hour.get(hour, 0) for hour in range(24)],
    }


def weekday_distribution():
    """Cups per weekday, Monday first - same counting as hourly_distribution()."""
    rows = (
        Order.objects.annotate(weekday=ExtractIsoWeekDay("created_at"))
        .values("weekday")
        .annotate(cups=Sum("machine_strokes"))
    )
    by_weekday = {row["weekday"]: int(row["cups"] or 0) for row in rows}
    return {
        "labels": _weekday_labels(),
        # ExtractIsoWeekDay is 1 (Monday) to 7 (Sunday), matching the order of _weekday_labels().
        "cups": [by_weekday.get(day, 0) for day in range(1, 8)],
    }


def _split_cups(annotation, member) -> tuple[dict, dict]:
    """Cups grouped by `annotation`, once for `member` and once for everyone else, in a
    single query. The two halves are what every "you vs the others" chart in the member
    portal is built from."""
    rows = (
        Order.objects.annotate(key=annotation)
        .values("key")
        .annotate(
            mine=Sum("machine_strokes", filter=Q(member=member)),
            others=Sum("machine_strokes", filter=~Q(member=member)),
        )
    )
    mine, others = {}, {}
    for row in rows:
        mine[row["key"]] = int(row["mine"] or 0)
        others[row["key"]] = int(row["others"] or 0)
    return mine, others


def _as_shares(counts: list) -> list:
    """Absolute cups -> percent of that person's own total. Comparing one member against
    two dozen others only says something once both sides are normalised: in absolute cups
    the single member loses every bar, whatever their habits."""
    total = sum(counts)
    return [round(count * 100 / total, 1) if total else 0.0 for count in counts]


def member_vs_others_hourly(member) -> dict:
    """When this member drinks their coffee compared to everyone else, in percent."""
    mine, others = _split_cups(ExtractHour("created_at"), member)
    hours = range(24)
    return {
        "labels": [f"{hour:02d}" for hour in hours],
        "you": _as_shares([mine.get(hour, 0) for hour in hours]),
        "others": _as_shares([others.get(hour, 0) for hour in hours]),
    }


def member_vs_others_weekday(member) -> dict:
    mine, others = _split_cups(ExtractIsoWeekDay("created_at"), member)
    days = range(1, 8)
    return {
        "labels": _weekday_labels(),
        "you": _as_shares([mine.get(day, 0) for day in days]),
        "others": _as_shares([others.get(day, 0) for day in days]),
    }


def member_vs_others_sorts(member) -> dict:
    """Which coffees this member picks compared to everyone else, in percent. Orders
    imported from the legacy database without a recognisable sort are left out, same as
    the dashboard's own distribution chart."""
    mine, others = _split_cups(F("coffee_sort__name"), member)
    # Most-drunk first, ties broken by name: the set union alone iterates in whatever
    # order the process' string hashing produces, which would reshuffle the chart's
    # categories between two page loads of the same data.
    names = sorted(
        (name for name in mine.keys() | others.keys() if name),
        key=lambda name: (-(mine.get(name, 0) + others.get(name, 0)), name),
    )
    return {
        "labels": names,
        "you": _as_shares([mine.get(name, 0) for name in names]),
        "others": _as_shares([others.get(name, 0) for name in names]),
    }


def member_vs_others_monthly(member, months: int = 12) -> dict:
    """Cups per month: this member against what the average *active* member drank that
    month. Dividing by everyone on file instead would drag the comparison down with every
    member who has long stopped coming."""
    month_starts = []
    cursor = current_month_start()
    for _ in range(months):
        month_starts.append(cursor)
        cursor = _step_back_one_month(cursor)
    month_starts.reverse()

    mine = {
        row["month"].strftime("%Y-%m"): int(row["cups"] or 0)
        for row in Order.objects.filter(member=member, created_at__gte=month_starts[0])
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(cups=Sum("machine_strokes"))
    }
    others = {
        row["month"].strftime("%Y-%m"): (int(row["cups"] or 0), row["members"] or 0)
        for row in Order.objects.filter(created_at__gte=month_starts[0])
        .exclude(member=member)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(cups=Sum("machine_strokes"), members=Count("member", distinct=True))
    }

    labels = [month.strftime("%Y-%m") for month in month_starts]
    return {
        "labels": labels,
        "you": [mine.get(label, 0) for label in labels],
        "others": [
            round(others[label][0] / others[label][1], 1) if others.get(label, (0, 0))[1] else 0
            for label in labels
        ],
    }


def member_comparison_summary(member) -> dict:
    """Headline numbers for the portal's stats page: how this member's all-time total
    compares to the average active member, and where they rank."""
    mine = total_cups(member)
    rows = (
        Order.objects.exclude(member=member)
        .values("member_id")
        .annotate(cups=Sum("machine_strokes"))
    )
    other_totals = [int(row["cups"] or 0) for row in rows]
    average = round(sum(other_totals) / len(other_totals), 1) if other_totals else 0.0
    return {
        "your_cups": mine,
        "others_average": average,
        "rank": sum(1 for total in other_totals if total > mine) + 1,
        "members": len(other_totals) + 1,
        # None rather than 0 when there's nobody to compare against yet - "0% above
        # average" would read as a real result.
        "percent_of_average": round((mine - average) * 100 / average, 1) if average else None,
    }


def quarterly_price_trend(quarters: int = 12):
    """Average order price per coffee sort, for each of the last `quarters` quarters."""
    now = timezone.localtime()
    quarter_starts = []
    q_index = (now.month - 1) // 3
    cursor_year, cursor_q = now.year, q_index
    for _ in range(quarters):
        month = cursor_q * 3 + 1
        quarter_starts.append(now.replace(year=cursor_year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0))
        cursor_q -= 1
        if cursor_q < 0:
            cursor_q = 3
            cursor_year -= 1
    quarter_starts.reverse()

    labels = [f"{q.year}-Q{(q.month - 1) // 3 + 1}" for q in quarter_starts]
    sort_names = [sort.name for sort in CoffeeSort.objects.all()]

    # One grouped query instead of one per (sort, quarter) pair - that used to be
    # sorts * quarters round-trips (~100 with the defaults) for a single page render.
    rows = (
        Order.objects.filter(coffee_sort__isnull=False, created_at__gte=quarter_starts[0])
        .annotate(quarter=TruncQuarter("created_at"))
        .values("quarter", "coffee_sort__name")
        .annotate(total=Sum("value"), n=Count("id"))
    )
    averages = {}
    for row in rows:
        q = row["quarter"]
        label = f"{q.year}-Q{(q.month - 1) // 3 + 1}"
        averages[(label, row["coffee_sort__name"])] = float(row["total"]) / row["n"] if row["n"] else None

    series = {name: [averages.get((label, name)) for label in labels] for name in sort_names}
    return {"labels": labels, "series": series}
