"""The inventory pages: what is on the shelf and the stock-take, the purchases members booked
(and corrections to them), what was spent per month, and the item list. Only while the
inventory is switched on (InventoryPolicy, System page); admins and managers alike."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from coffee import services
from coffee.models import InventoryItem, Member, Purchase, StockCount

from . import audit
from .forms import InventoryItemForm, PriceAdjustmentForm, PurchaseForm
from .models import InventoryPolicy

PURCHASES_PER_PAGE = 50
BOX = "box"  # the purchases filter's value for "paid from the coffee box"


def inventory_required(view):
    """Switched off, the pages don't exist - an old bookmark gets a 404, not a half feature."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not InventoryPolicy.current().enabled:
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


@login_required
@inventory_required
def inventory(request):
    if request.method == "POST":
        counted = []
        for item in InventoryItem.objects.filter(is_active=True):
            raw = request.POST.get(f"count-{item.pk}", "").strip()
            if not raw:
                continue
            quantity = services.parse_quantity(raw)
            if quantity is None:
                messages.error(request, _("%(item)s: enter a number of at least 0.") % {"item": item.name})
                return redirect("dashboard:inventory")
            services.record_stock_count(item, quantity, request.user.get_username())
            counted.append(f"{item.name} {quantity.normalize():f}")
        if counted:
            audit.log(request, "stock.count", details=", ".join(counted))
            messages.success(request, _("Stock saved."))
        return redirect("dashboard:inventory")
    return render(request, "dashboard/inventory.html", {
        "levels": services.stock_levels(),
        "counts": StockCount.objects.select_related("item")[:20],
    })


@login_required
@inventory_required
def purchases(request):
    rows = Purchase.objects.select_related("member", "item")
    member, box = None, request.GET.get("member") == BOX
    if request.GET.get("member", "").isdigit():
        member = Member.objects.filter(pk=int(request.GET["member"])).first()
    if member:
        rows = rows.filter(member=member)
    elif box:
        rows = rows.filter(member__isnull=True)
    page = Paginator(rows, PURCHASES_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "dashboard/inventory_purchases.html", {
        "page": page,
        "member": member,
        "box": box,
        "box_purchases": Purchase.objects.filter(member__isnull=True).exists(),
        "members": Member.objects.filter(purchases__isnull=False).distinct().order_by("nickname", "uid"),
    })


def _purchases_url(member_uid=None):
    url = reverse("dashboard:inventory_purchases")
    return f"{url}?member={member_uid}" if member_uid else url


def _payer(purchase):
    """The audit target: who was credited, or the coffee box."""
    return purchase.member.display_name() if purchase.member else "coffee box"


@login_required
@inventory_required
def purchase_add(request):
    initial = {}
    if request.GET.get("member", "").isdigit():
        initial["member"] = request.GET["member"]
    form = PurchaseForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        purchase = services.book_purchase(data["member"], data["item"], data["quantity"], data["price"],
                                          info=data["info"], created_at=data["created_at"])
        audit.log(request, "purchase.create", target=_payer(purchase),
                  details=services.describe_purchase(purchase))
        messages.success(request, _("Purchase entered."))
        return redirect(_purchases_url(purchase.member_id))
    return render(request, "dashboard/inventory_purchase_form.html", {"form": form, "instance": None})


# Not switched off with the rest: a purchase booked earlier is still a credit in someone's balance,
# and Bookings sends its corrections here.
@login_required
def purchase_edit(request, pk):
    purchase = get_object_or_404(Purchase.objects.select_related("member", "item"), pk=pk)
    form = PurchaseForm(request.POST or None, instance=purchase)
    if request.method == "POST" and form.is_valid():
        purchase = services.update_purchase(form.save(commit=False))
        audit.log(request, "purchase.update", target=_payer(purchase),
                  details=services.describe_purchase(purchase))
        messages.success(request, _("Purchase saved."))
        return redirect(_purchases_url(purchase.member_id))
    return render(request, "dashboard/inventory_purchase_form.html", {"form": form, "instance": purchase})


@login_required
@require_POST
def purchase_delete(request, pk):
    purchase = get_object_or_404(Purchase.objects.select_related("member", "item"), pk=pk)
    member_uid, target, details = purchase.member_id, _payer(purchase), services.describe_purchase(purchase)
    services.delete_purchase(purchase)
    audit.log(request, "purchase.delete", target=target, details=details)
    messages.success(request, _("Purchase deleted."))
    return redirect(_purchases_url(member_uid))


@login_required
@inventory_required
def spending(request):
    return render(request, "dashboard/inventory_spending.html", {"totals": services.purchase_totals()})


COST_PERIODS = (3, 6, 12)


@login_required
@inventory_required
def costs(request):
    """What a cup cost and what the sorts would have to cost - a suggestion; the price itself is
    still set under Coffee sorts, by someone who can explain it to the members."""
    policy = InventoryPolicy.current()
    form = PriceAdjustmentForm(request.POST or None, instance=policy)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log(request, "inventory_policy.update", details=f"price adjustment {policy.price_adjustment_percent:+d}%")
        messages.success(request, _("Price adjustment saved."))
        return redirect(f"{reverse('dashboard:inventory_costs')}?months={request.GET.get('months', '')}")
    months = int(request.GET["months"]) if request.GET.get("months") in {str(m) for m in COST_PERIODS} else 12
    data = services.cost_per_cup(months)
    return render(request, "dashboard/inventory_costs.html", {
        "data": data,
        "form": form,
        "periods": COST_PERIODS,
        "adjustment": policy.price_adjustment_percent,
        "suggestions": services.price_suggestions(data["per_cup"], policy.price_adjustment_percent)
        if data["enough_data"] else [],
        "min_months": services.COST_MIN_MONTHS,
        "min_cups": services.COST_MIN_CUPS,
    })


@login_required
@inventory_required
def item_list(request):
    return render(request, "dashboard/inventory_items.html", {"items": InventoryItem.objects.all()})


@login_required
@inventory_required
def item_add(request):
    form = InventoryItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        audit.log(request, "inventory_item.create", target=item.name)
        return redirect("dashboard:inventory_items")
    return render(request, "dashboard/inventory_item_form.html", {"form": form, "instance": None})


@login_required
@inventory_required
def item_edit(request, pk):
    instance = get_object_or_404(InventoryItem, pk=pk)
    form = InventoryItemForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        audit.log(request, "inventory_item.update", target=item.name)
        return redirect("dashboard:inventory_items")
    return render(request, "dashboard/inventory_item_form.html", {"form": form, "instance": instance})


@login_required
@inventory_required
@require_POST
def item_delete(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    name = item.name
    try:
        item.delete()
    except ProtectedError:
        # Its purchases are credits in members' balances - the item stays, only unbookable.
        messages.error(request, _("%(item)s has purchases - switch off \"Can be booked\" instead.") % {"item": name})
        audit.log(request, "inventory_item.delete", target=name, outcome="failure", details="has purchases")
        return redirect("dashboard:inventory_items")
    audit.log(request, "inventory_item.delete", target=name)
    return redirect("dashboard:inventory_items")
