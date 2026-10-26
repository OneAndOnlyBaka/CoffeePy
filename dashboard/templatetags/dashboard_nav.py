"""The dashboard's navigation, in one place: the sidebar (tablet/PC), the bottom tabs and
the "More" sheet (phone) are all drawn from DASHBOARD_NAV, so a new page is added once."""
from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()

# (heading, superuser only, items); an item is (url name, label, icon, url names it is
# also active for). Admin-only groups are the pages behind @superuser_required.
DASHBOARD_NAV = (
    (None, False, (
        ("index", _("Overview"), "home", ()),
        ("statistics", _("Statistics"), "chart", ()),
        ("pillory", _("Pillory"), "bell", ()),
    )),
    (_("Manage"), False, (
        ("members", _("Members"), "users", ("member_",)),
        ("bookings", _("Bookings"), "list", ("booking", "order_", "payment_")),
        ("coffeesorts", _("Coffee sorts"), "cup", ("coffeesort_",)),
        ("paymentmethods", _("Payment methods"), "card", ("paymentmethod_",)),
    )),
    (_("System"), True, (
        ("users", _("Dashboard users"), "user-shield", ("user_",)),
        ("password_policy", _("Login & passwords"), "key", ()),
        ("system_dashboard", _("System"), "server", ()),
        ("backup", _("Backup"), "database", ("backup_",)),
        ("update", _("Update"), "upload", ("update_",)),
        ("import_legacy_db", _("Import legacy DB"), "download", ("import_",)),
        ("system_time", _("System time"), "clock", ()),
        ("certificate", _("Certificate"), "lock", ()),
        ("audit_log", _("Audit log"), "clipboard", ()),
        ("reset_database", _("Reset database"), "trash", ()),
    )),
)

# The phone's bottom tabs: the everyday pages, everything else is under "More".
TABS = ("index", "members", "bookings", "statistics")


@register.simple_tag(takes_context=True)
def dashboard_nav(context):
    """The groups this user may see, each item with its `active` flag set for the page."""
    request = context["request"]
    current = request.resolver_match.url_name if request.resolver_match else ""
    groups = []
    for heading, superuser_only, items in DASHBOARD_NAV:
        if superuser_only and not request.user.is_superuser:
            continue
        groups.append({
            "heading": heading,
            "items": [
                {"url_name": name, "label": label, "icon": f"icons/{icon}.svg",
                 "active": current == name or any(current.startswith(p) for p in also)}
                for name, label, icon, also in items
            ],
        })
    return groups


@register.simple_tag(takes_context=True)
def dashboard_tabs(context):
    groups = dashboard_nav(context)
    items = {item["url_name"]: item for group in groups for item in group["items"]}
    tabs = [items[name] for name in TABS]
    # "More" lights up for whatever page isn't one of the tabs.
    more_active = not any(tab["active"] for tab in tabs)
    return {"tabs": tabs, "more_active": more_active}
