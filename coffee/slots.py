"""A member's "my coffees": up to three slots, each a coffee sort the way they like it - strength,
water and temperature within what the machine's product allows (MachineProduct.recipe), and how
many of it in a row into one big cup or a jug (coffee-machine/README.md, several cups).

The price is the sort's, whatever the strength or the water, times the cups; each cup is booked
once the machine has made it (coffee.machine.book). Without a machine - or for a sort the machine
doesn't make - a slot is only the sort and the number of cups, booked straight away."""
from django.utils.translation import gettext as _

from coffee.models import CoffeeSort, MachineProduct, Member, MemberSlot

TEMPERATURES = {0: "low", 1: "normal", 2: "high"}


def _product(sort: CoffeeSort | None) -> MachineProduct | None:
    return MachineProduct.objects.filter(coffee_sort=sort, present=True).first() if sort is not None else None


def recipe(sort: CoffeeSort | None) -> dict:
    """What a slot of this sort can set - {} when nothing (no machine product, or one like milk foam)."""
    product = _product(sort)
    return dict(product.recipe or {}) if product is not None else {}


def _clamp(value, limits: dict, step: int = 1):
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    value = max(limits["min"], min(limits["max"], value))
    if step > 1:
        value = limits["min"] + round((value - limits["min"]) / step) * step
        value = min(limits["max"], value)
    return None if value == limits.get("default") else value


def clean(sort: CoffeeSort, strength=None, water_ml=None, temperature=None, count=1) -> dict:
    """The slot's fields within the sort's recipe: out of range is put to the nearest allowed value,
    the machine's default stored as None (so a slot follows a default the machine changes), and what
    the product can't set dropped."""
    limits = recipe(sort)
    fields = {"strength": None, "water_ml": None, "temperature": None}
    if "strength" in limits:
        fields["strength"] = _clamp(strength, limits["strength"])
    if "water" in limits:
        fields["water_ml"] = _clamp(water_ml, limits["water"], int(limits["water"].get("step") or 1))
    if "temperature" in limits and temperature is not None:
        try:
            value = int(temperature)
        except (TypeError, ValueError):
            value = None
        options = limits["temperature"].get("options") or []
        if value in options and value != limits["temperature"].get("default"):
            fields["temperature"] = value
    try:
        fields["count"] = max(1, min(MemberSlot.MAX_COUNT, int(count)))
    except (TypeError, ValueError):
        fields["count"] = 1
    return fields


def save(member: Member, position: int, sort: CoffeeSort | None, **values) -> MemberSlot | None:
    """Sets (or, without a sort, clears) one slot."""
    if position not in MemberSlot.POSITIONS:
        raise ValueError(position)
    if sort is None:
        MemberSlot.objects.filter(member=member, position=position).delete()
        if member.favourite_slot == position:
            member.favourite_slot = None
            member.save(update_fields=["favourite_slot"])
        return None
    slot, _created = MemberSlot.objects.update_or_create(
        member=member, position=position, defaults={"coffee_sort": sort, **clean(sort, **values)})
    return slot


def slots_for(member: Member | None) -> list[MemberSlot]:
    """The member's slots whose sort is offered at the kiosk."""
    if member is None:
        return []
    return list(MemberSlot.objects.filter(member=member, coffee_sort__is_obsolete=False)
                .select_related("coffee_sort").order_by("position"))


def options(slot: MemberSlot) -> dict:
    """The brew options for the machine - only what differs from its default."""
    out = {}
    for key in ("strength", "water_ml", "temperature"):
        if getattr(slot, key) is not None:
            out[key] = getattr(slot, key)
    return out


def effective(slot: MemberSlot) -> dict:
    """What the slot makes, defaults filled in - for the labels and the editors."""
    limits = recipe(slot.coffee_sort)
    return {
        "strength": slot.strength if slot.strength is not None else limits.get("strength", {}).get("default"),
        "water_ml": slot.water_ml if slot.water_ml is not None else limits.get("water", {}).get("default"),
        "temperature": slot.temperature if slot.temperature is not None else limits.get("temperature", {}).get("default"),
    }


def describe(slot: MemberSlot) -> str:
    """For the audit log and the order's info - English, like everything stored."""
    made = effective(slot)
    parts = [slot.coffee_sort.name]
    if made["strength"] is not None:
        parts.append(f"strength {made['strength']}")
    if made["water_ml"] is not None:
        parts.append(f"{made['water_ml']} ml")
    if made["temperature"] is not None:
        parts.append(TEMPERATURES.get(made["temperature"], str(made["temperature"])))
    if slot.count > 1:
        parts.append(f"{slot.count}x")
    return ", ".join(parts)


def save_form(member: Member, data) -> dict:
    """The editor's fields - the kiosk's and the phone's are the same: `s<n>-sort`, `-strength`, `-water`,
    `-temperature`, `-count` per slot, `favourite` (the slot preselected) and `hide_standard_sorts`.
    Returns {position: slot} of what was saved."""
    offered = {str(sort.pk): sort for sort in CoffeeSort.offered()}
    for position in MemberSlot.POSITIONS:
        def field(name, position=position):
            return data.get(f"s{position}-{name}") or None
        save(member, position, offered.get(str(data.get(f"s{position}-sort") or "")),
             strength=field("strength"), water_ml=field("water"), temperature=field("temperature"),
             count=field("count") or 1)
    saved = {slot.position: slot for slot in slots_for(member)}
    try:
        favourite = int(data.get("favourite") or 0)
    except ValueError:
        favourite = 0
    member.favourite_slot = favourite if favourite in saved else None
    member.hide_standard_sorts = data.get("hide_standard_sorts") == "on"
    member.save(update_fields=["favourite_slot", "hide_standard_sorts"])
    return saved


def options_text(values: dict) -> str:
    """"strength 7 · 200 ml · extra hot" - what differs from the machine's default (a slot stores only
    that), in the active language. The kiosk's order screen and brew, the phone's order page."""
    parts = []
    if values.get("strength") is not None:
        parts.append(_("strength %(level)s") % {"level": values["strength"]})
    if values.get("water_ml") is not None:
        parts.append(_("%(ml)s ml") % {"ml": values["water_ml"]})
    if values.get("temperature") is not None:
        parts.append(_("extra hot") if values["temperature"] >= 2 else _("less hot"))
    return " · ".join(parts)
