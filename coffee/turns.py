"""The line for the coffee machine: one outlet, one cup under it at a time - and members who order from
the kiosk and from their phones at once. Everyone who wants a brew gets a MachineTurn; whoever is first
while the machine is free is called and has TURN_SECONDS to press Start (at the kiosk after a card tap,
or on the phone), else the next one is. Nothing is booked for a turn itself: the brew it starts is
booked cup by cup like any other (coffee.machine.book).

`advance` moves the line on. It runs on every kiosk poll, every phone poll and in the listener's
housekeeping - whichever comes first; it is idempotent and takes the active rows under a lock, as the
web workers run it side by side. With no active turn it is one query: the kiosk polls every 500 ms."""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from coffee import machine
from coffee.models import MachineDispense, MachineTurn

TURN_SECONDS = 60
# Finished turns are only there for the day's numbers and "it was booked" on the phone.
KEEP_SECONDS = 24 * 3600


def active():
    return MachineTurn.objects.filter(state__in=MachineTurn.ACTIVE).select_related("member", "coffee_sort")


def deadline(turn: MachineTurn):
    return turn.called_at + timezone.timedelta(seconds=TURN_SECONDS) if turn.called_at else None


def machine_busy(now=None) -> bool:
    """A brew asked for or running - from a turn, a free product, Home Assistant or the machine's keys."""
    now = now or timezone.now()
    return MachineDispense.objects.filter(
        state__in=(MachineDispense.REQUESTED, MachineDispense.STARTED),
        created_at__gte=now - timezone.timedelta(seconds=machine.STARTED_TIMEOUT)).exists()


def advance(now=None) -> list[MachineTurn]:
    """Ends what is over, calls the next one when the machine is free. Returns the active turns, the
    one called or brewing first."""
    now = now or timezone.now()
    if not MachineTurn.objects.filter(state__in=MachineTurn.ACTIVE).exists():
        return []
    with transaction.atomic():
        turns = list(MachineTurn.objects.select_for_update().filter(state__in=MachineTurn.ACTIVE)
                     .order_by("created_at", "id"))
        for turn in turns:
            if turn.state == MachineTurn.BREWING:
                progress = machine.chain_progress(turn.dispense) if turn.dispense_id else None
                if progress is None or progress["over"]:
                    failed = progress and progress["failed"]
                    _finish(turn, MachineTurn.FAILED if failed and not progress["done"] else MachineTurn.DONE, now,
                            failed.reason if failed else "")
            elif turn.state == MachineTurn.CALLED and now >= deadline(turn):
                _finish(turn, MachineTurn.EXPIRED, now)
        turns = [t for t in turns if t.state in MachineTurn.ACTIVE]
        holder = next((t for t in turns if t.state != MachineTurn.WAITING), None)
        if holder is None and turns and machine.brewing_available() and not machine_busy(now):
            holder = turns[0]
            holder.state, holder.called_at = MachineTurn.CALLED, now
            holder.save(update_fields=["state", "called_at"])
    return sorted(turns, key=lambda t: t.state == MachineTurn.WAITING)


def _finish(turn, state, now, reason=""):
    turn.state, turn.finished_at, turn.reason = state, now, reason[:200]
    turn.save(update_fields=["state", "finished_at", "reason"])


def current_for(member) -> MachineTurn | None:
    """The member's turn that hasn't ended - one at a time."""
    return active().filter(member=member).first() if member is not None else None


def join(member, sort, slot, source, now=None) -> MachineTurn:
    """In line with this choice - or the member's turn that waits already, with the new choice (a turn
    that runs stays as it is: a second coffee comes after it)."""
    from coffee import slots as my_coffees  # noqa: PLC0415 - slots imports machine

    now = now or timezone.now()
    options = my_coffees.options(slot) if slot is not None else {}
    count = slot.count if slot is not None else 1
    with transaction.atomic():
        turn = (MachineTurn.objects.select_for_update()
                .filter(member=member, state__in=(MachineTurn.WAITING, MachineTurn.CALLED)).first())
        if turn is not None:
            turn.coffee_sort, turn.options, turn.count = sort, options, count
            turn.slot_position = slot.position if slot is not None else None
            turn.save(update_fields=["coffee_sort", "options", "count", "slot_position"])
        else:
            today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
            last = MachineTurn.objects.filter(created_at__gte=today).aggregate(n=Max("number"))["n"] or 0
            turn = MachineTurn.objects.create(
                number=last + 1, member=member, coffee_sort=sort, options=options, count=count, source=source,
                slot_position=slot.position if slot is not None else None, created_at=now)
    advance(now)
    turn.refresh_from_db()
    return turn


def claim(turn: MachineTurn, now=None):
    """The member who is called showed up at the kiosk (a card tap): their minute starts afresh, so a tap
    in its last seconds still leaves time to put the cup under."""
    now = now or timezone.now()
    MachineTurn.objects.filter(pk=turn.pk, state=MachineTurn.CALLED).update(called_at=now)
    turn.called_at = now


def start(turn: MachineTurn, source) -> MachineDispense | None:
    """Start pressed: the brew of the turn's choice, if it is still this turn's. None when not (the
    minute ran out, or the machine can't make it any more)."""
    with transaction.atomic():
        turn = MachineTurn.objects.select_for_update().filter(pk=turn.pk, state=MachineTurn.CALLED).first()
        if turn is None or timezone.now() >= deadline(turn):  # ran out before a poll noticed
            return None
        product = machine.product_for(turn.coffee_sort)
        if product is None or not machine.brewing_available():
            return None
        dispense = machine.request_brew(product, turn.member, turn.coffee_sort, source,
                                        options=turn.options, count=turn.count)
        turn.state, turn.dispense = MachineTurn.BREWING, dispense
        turn.save(update_fields=["state", "dispense"])
    return dispense


def cancel(turn: MachineTurn, now=None) -> bool:
    """Out of line (only while waiting or called - a brew that runs runs to its end)."""
    now = now or timezone.now()
    left = MachineTurn.objects.filter(pk=turn.pk, state__in=(MachineTurn.WAITING, MachineTurn.CALLED)).update(
        state=MachineTurn.CANCELLED, finished_at=now)
    if left:
        advance(now)
    return bool(left)


def ahead(turn: MachineTurn, turns=None) -> list[MachineTurn]:
    """Who comes before this turn: the one at the machine, then those waiting before it."""
    turns = turns if turns is not None else advance()
    return [t for t in turns if t.pk != turn.pk and (t.state != MachineTurn.WAITING or
                                                     (t.created_at, t.pk) < (turn.created_at, turn.pk))]


def housekeeping(now=None):
    now = now or timezone.now()
    advance(now)
    MachineTurn.objects.exclude(state__in=MachineTurn.ACTIVE).filter(
        created_at__lt=now - timezone.timedelta(seconds=KEEP_SECONDS)).delete()
