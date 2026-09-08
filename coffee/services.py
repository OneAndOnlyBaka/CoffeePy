"""Query logic ported from the legacy DatabaseConnector, using the Django ORM instead of
string-built SQL."""
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncMonth, TruncQuarter
from django.utils import timezone

from .models import CoffeeSort, Member, Order, Payment


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


def cups_by_member(members, since: datetime | None = None) -> dict:
    """Machine-strokes total per member in one grouped query, keyed by uid - for
    rendering a list of members (e.g. the kiosk ranking screen) without falling back to
    total_cups()/month_cups() in a per-member loop. Those two stay as they are for the
    single-member call sites (order/pay screens) where a loop doesn't apply."""
    qs = Order.objects.filter(member__in=members)
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return dict(qs.values("member_id").annotate(total=Sum("machine_strokes")).values_list("member_id", "total"))


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


def ranking_all_time(limit: int = 7):
    """Members ranked by total machine strokes (all time), highest first. Departed
    members are excluded - they're no longer part of the club."""
    return (
        Member.objects.filter(departed_at__isnull=True)
        .annotate(cups=Sum("orders__machine_strokes"))
        .filter(cups__isnull=False)
        .order_by("-cups")[:limit]
    )


def ranking_current_month(limit: int = 7):
    """Members ranked by machine strokes ordered this month, highest first. Departed
    members are excluded - they're no longer part of the club."""
    return (
        Member.objects.filter(departed_at__isnull=True, orders__created_at__gte=current_month_start())
        .annotate(cups=Sum("orders__machine_strokes"))
        .filter(cups__isnull=False)
        .order_by("-cups")[:limit]
    )


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
    """Total order value and total payment value per month, oldest first."""
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
    payment_rows = {
        row["month"].strftime("%Y-%m"): row["total"]
        for row in Payment.objects.filter(created_at__gte=since)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Sum("value"))
    }

    labels = [d.strftime("%Y-%m") for d in month_starts]
    return {
        "labels": labels,
        "orders": [float(order_rows.get(label, 0)) for label in labels],
        "payments": [float(payment_rows.get(label, 0)) for label in labels],
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
        "labels": [r["method_name"] or "Cash" for r in rows],
        "totals": [float(r["total"]) for r in rows],
        "counts": [r["count"] for r in rows],
    }


def coffee_distribution():
    """Number of orders per coffee sort, all time."""
    rows = (
        Order.objects.filter(coffee_sort__isnull=False)
        .values("coffee_sort__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return {"labels": [r["coffee_sort__name"] for r in rows], "counts": [r["count"] for r in rows]}


WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


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
        "labels": WEEKDAY_LABELS,
        # ExtractIsoWeekDay is 1 (Monday) to 7 (Sunday), matching WEEKDAY_LABELS' order.
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
        "labels": WEEKDAY_LABELS,
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
