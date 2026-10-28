from django import template

from coffee.models import payment_method_label

register = template.Library()

# {{ payment.method_name|method_label }}: what a payment method is called on screen.
register.filter("method_label", payment_method_label)
