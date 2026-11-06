"""The coffee machine page: which machine (or none), finding and connecting it, what it reports.
Admins only. The talking to the machine is the bridge's (coffee-machine/), the app's side of it
coffee/machine.py; the host's Bluetooth is switched by install/storage_helper.py."""
import json
import re
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from coffee import backups, machine
from coffee.models import CoffeeSort, MachineDispense, MachineIncident, MachineProduct, MachineSupply

from . import audit
from .decorators import superuser_required
from .forms import CoffeeMachineForm
from .models import CoffeeMachine

ADDRESS = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def _bluetooth_status():
    """What the host's storage helper last saw of its Bluetooth - None while it doesn't run."""
    status = backups.storage_status()
    bluetooth = status.get("bluetooth") or {}
    return bluetooth if status.get("helper_running") and "active" in bluetooth else None


def _switch_bluetooth(request, settings_):
    backups.storage_request("bluetooth", enabled=settings_.driver == CoffeeMachine.DRIVER_JURA_BLE,
                            onboard=settings_.adapter == CoffeeMachine.ADAPTER_ONBOARD,
                            requested_by=request.user.get_username())


@login_required
@superuser_required
def device(request):
    settings_ = CoffeeMachine.current()
    form = CoffeeMachineForm(instance=settings_)
    if request.method == "POST":
        action = request.POST.get("form")
        if action == "settings":
            before = (settings_.driver, settings_.adapter)
            form = CoffeeMachineForm(request.POST, instance=settings_)
            if form.is_valid():
                settings_ = form.save()
                if not settings_.enabled and settings_.address:
                    settings_.address, settings_.name = "", ""
                    settings_.save(update_fields=["address", "name"])
                if (settings_.driver, settings_.adapter) != before:
                    _switch_bluetooth(request, settings_)
                if not settings_.enabled:
                    offered = machine.unsync_products()
                    if offered:
                        audit.log(request, "coffee_sort.sync", details=f"offered: {', '.join(offered)}"[:500])
                sent = machine.send_config()
                audit.log(request, "coffee_machine.update", details=(
                    f"{settings_.driver}, {settings_.adapter}, keys {'locked' if settings_.block_manual else 'free'}"))
                messages.success(request, _("Saved.") if sent else
                                 _("Saved - the machine connection takes it over within a minute."))
                return redirect("dashboard:device")
        elif action == "connect":
            address = request.POST.get("address", "").strip().upper()
            if not settings_.enabled or not ADDRESS.match(address):
                messages.error(request, _("That is not a machine that can be connected."))
                return redirect("dashboard:device")
            settings_.address, settings_.name = address, request.POST.get("name", "").strip()[:100]
            settings_.save(update_fields=["address", "name"])
            machine.send_config()
            audit.log(request, "coffee_machine.connect", target=address, details=settings_.name)
            messages.success(request, _("Connecting - this takes up to a minute."))
            return redirect("dashboard:device")
        elif action == "forget":
            address = settings_.address
            settings_.address, settings_.name = "", ""
            settings_.save(update_fields=["address", "name"])
            machine.send_config()
            audit.log(request, "coffee_machine.forget", target=address)
            messages.success(request, _("The machine is disconnected."))
            return redirect("dashboard:device")
        elif action == "products":
            changed = _save_products(request)
            if changed:
                audit.log(request, "machine_product.update", details="; ".join(changed)[:500])
            sync = machine.sync_products({"products": [
                {"code": p.code, "name": p.name, "strokes": p.strokes, "brewable": p.brewable, "water_ml": p.water_ml}
                for p in MachineProduct.objects.filter(present=True)]})
            if sync:
                audit.log(request, "coffee_sort.sync", details="; ".join(f"{k}: {', '.join(v)}" for k, v in sync.items())[:500])
            messages.success(request, _("Saved."))
            return redirect("dashboard:device")
        elif action == "adopt":
            product = get_object_or_404(MachineProduct, code=request.POST.get("code"), coffee_sort__isnull=True)
            sort = machine.adopt_product(product)
            machine.sync_products({"products": [{"code": p.code, "name": p.name, "strokes": p.strokes}
                                                for p in MachineProduct.objects.filter(present=True)]})
            audit.log(request, "coffee_sort.create", target=sort.name, details=f"from the machine's {product.name}")
            messages.success(request, _("Created - check its price."))
            return redirect("dashboard:coffeesort_edit", pk=sort.pk)
        elif action == "supplies":
            changed = _save_supplies(request)
            if changed:
                audit.log(request, "machine_supply.update", details="; ".join(changed)[:500])
            messages.success(request, _("Saved."))
            return redirect("dashboard:device")
        elif action == "refill":
            kind = request.POST.get("kind")
            if kind in dict(MachineSupply.KINDS):
                machine.refill(kind, actor=request.user.get_username())
            return redirect("dashboard:device")
        elif action == "reboot":
            backups.storage_request("reboot", requested_by=request.user.get_username())
            audit.log(request, "system.reboot")
            messages.success(request, _("The device restarts - the dashboard is back in about two minutes."))
            return redirect("dashboard:device")
    status = machine.status()
    state = status["state"]
    products = list(MachineProduct.objects.select_related("coffee_sort").order_by("-present", "name"))
    firmware = " · ".join(filter(None, (state.get("firmware"), f"BlueFrog {state['bluefrog']}" if state.get("bluefrog") else "")))
    return render(request, "dashboard/device.html", {
        "machine_name": settings_.name or state.get("model") or settings_.address, "firmware": firmware,
        "machine_error": state.get("error", ""),
        "form": form, "settings": settings_, "status": status, "bluetooth": _bluetooth_status(),
        "counter_rows": machine.counter_rows(status), "maintenance": status["counters"].get("maintenance") or {},
        "products": products, "sorts": CoffeeSort.objects.order_by("name"),
        "supplies": list(machine.supplies().values()) if products else [],
        **_statistics(request),
    })


def _save_products(request) -> list[str]:
    """The products table: which sort each is sold as, free or not, its strokes and - for a product
    the machine has no recipe for - its water."""
    changed = []
    sorts = {str(s.pk): s for s in CoffeeSort.objects.all()}
    taken = set()
    for product in MachineProduct.objects.select_related("coffee_sort"):
        prefix = f"p{product.code}-"
        if f"{prefix}present" not in request.POST:
            continue  # not on the form (it was not listed when the page was drawn)
        sort = sorts.get(request.POST.get(f"{prefix}sort", ""))
        if sort is not None and sort.pk in taken:
            sort = None  # one sort, one product
        free = request.POST.get(f"{prefix}free") == "on"
        if free:
            sort = None  # a free product isn't sold
        strokes = max(1, min(9, _int(request.POST.get(f"{prefix}strokes"), product.strokes)))
        water = _int(request.POST.get(f"{prefix}water"), None)
        water = max(5, min(500, water)) if water else None
        new = (sort.pk if sort else None, free, strokes, water)
        if new != (product.coffee_sort_id, product.free, product.strokes, product.water_ml):
            product.coffee_sort, product.free, product.strokes = sort, free, strokes
            if not product.brewable:
                product.water_ml = water
            product.save()
            changed.append(f"{product.name}: {sort.name if sort else ('free' if free else '-')}, {strokes}")
        if sort is not None:
            taken.add(sort.pk)
    return changed


def _save_supplies(request) -> list[str]:
    changed = []
    for supply in machine.supplies().values():
        capacity = _int(request.POST.get(f"{supply.kind}-capacity"), supply.capacity)
        per_portion = _int(request.POST.get(f"{supply.kind}-per_portion"), int(supply.per_portion))
        capacity = max(1, min(100000, capacity))
        if (capacity, per_portion) != (supply.capacity, int(supply.per_portion)):
            supply.capacity, supply.per_portion = capacity, max(0, min(100, per_portion))
            supply.save(update_fields=["capacity", "per_portion"])
            changed.append(f"{supply.kind} {capacity}")
    return changed


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _statistics(request) -> dict:
    """The page's numbers: products made per day by where they were asked for (the last 30 days),
    the dispenses themselves, and the incidents."""
    since = timezone.now() - timedelta(days=30)
    days = (MachineDispense.objects.filter(created_at__gte=since, state=MachineDispense.DONE)
            .annotate(day=TruncDate("created_at")).values("day", "source").annotate(n=Count("id")).order_by("day"))
    labels = sorted({row["day"].isoformat() for row in days})
    series = {source: [0] * len(labels) for source, _ in MachineDispense.SOURCES}
    for row in days:
        series[row["source"]][labels.index(row["day"].isoformat())] = row["n"]
    totals = MachineDispense.objects.aggregate(
        done=Count("id", filter=Q(state=MachineDispense.DONE)),
        booked=Count("id", filter=Q(state=MachineDispense.DONE, order__isnull=False)),
        manual=Count("id", filter=Q(state=MachineDispense.DONE, source=MachineDispense.SOURCE_MANUAL)),
        free=Count("id", filter=Q(state=MachineDispense.DONE, source=MachineDispense.SOURCE_FREE)),
        failed=Count("id", filter=Q(state=MachineDispense.FAILED)),
    )
    page = Paginator(MachineDispense.objects.select_related("member", "order"), 50).get_page(request.GET.get("page"))
    return {
        "chart": json.dumps({"labels": labels, "series": {k: v for k, v in series.items() if any(v)},
                             "names": {key: str(label) for key, label in MachineDispense.SOURCES}}),
        "totals": totals,
        "dispenses": page,
        "incidents": MachineIncident.objects.select_related("member", "hero")[:30],
    }


@login_required
@superuser_required
@require_POST
def device_scan(request):
    """Starts a search for machines; the page polls device_scan_status with the id."""
    if not CoffeeMachine.current().enabled:
        return JsonResponse({"error": "disabled"}, status=400)
    return JsonResponse({"id": machine.request_scan()})


@login_required
@superuser_required
@require_GET
def device_scan_status(request):
    return JsonResponse(machine.scan_result(request.GET.get("id", "")))
