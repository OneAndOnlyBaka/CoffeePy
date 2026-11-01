from decimal import Decimal

from django import template
from django.utils.formats import number_format

from coffee.models import payment_method_label

register = template.Library()

# {{ payment.method_name|method_label }}: what a payment method is called on screen.
register.filter("method_label", payment_method_label)


@register.filter
def quantity(value):
    """A stock or purchase quantity without trailing zeros, in the language's own format:
    "1,5" kg, not "1.500"."""
    value = Decimal(value).normalize()
    if value == value.to_integral_value():
        value = value.quantize(Decimal(1))
    return number_format(value)


@register.simple_tag
def inventory_on() -> bool:
    """Whether members book purchases and take stock themselves (dashboard → System) - for the
    portal's tab bar, which every portal page draws."""
    from dashboard.models import InventoryPolicy  # noqa: PLC0415 - dashboard imports coffee

    return InventoryPolicy.current().members_book
