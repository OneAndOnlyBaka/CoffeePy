from django import forms
from django.utils.translation import gettext_lazy, pgettext_lazy

from coffee.models import CoffeeSort, Member, PaymentMethod


class CoffeeSortForm(forms.ModelForm):
    class Meta:
        model = CoffeeSort
        fields = ["name", "price", "machine_strokes"]
        # Spelled out (as Django would derive them from the field names) so they can be
        # translated without touching the models.
        labels = {
            "name": gettext_lazy("Name"),
            "price": gettext_lazy("Price"),
            "machine_strokes": gettext_lazy("Machine strokes"),
        }


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "provider", "identifier", "link_template", "is_active", "order"]
        labels = {
            "name": gettext_lazy("Name"),
            "provider": gettext_lazy("Provider"),
            "identifier": gettext_lazy("Identifier"),
            "link_template": gettext_lazy("Link template"),
            "is_active": gettext_lazy("Is active"),
            # Context: the kiosk's "Order" (to order a coffee) is a different word in German.
            "order": pgettext_lazy("position in a list", "Order"),
        }
        help_texts = {
            "identifier": gettext_lazy(
                "PayPal: your paypal.me username (e.g. \"coffeeclub\"). "
                "PayPal Pool: the pool's URL, or just the ID at the end of it. "
                "WERO: the phone number registered with WERO - WERO has no public link "
                "format, so this is shown as a plain instruction instead of a QR link."
            ),
            "link_template": gettext_lazy(
                'Custom provider only. Optional "{amount}" placeholder for what is owed '
                '(e.g. https://example.com/pay/{amount}EUR); leave the placeholder out for '
                "a static link, or the whole field blank for no link at all."
            ),
            "order": gettext_lazy(
                "Display order among payment methods (lowest number first), e.g. in the "
                '"Paid via" dropdown and the row of pay buttons/QR codes on the kiosk and '
                "phone. Methods with the same number are sorted alphabetically by name "
                "instead. Leave at 0 if you don't care about the order."
            ),
        }


class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = ["uid", "nickname", "favourite_coffee"]
        labels = {
            "uid": gettext_lazy("Uid"),
            "nickname": gettext_lazy("Nickname"),
            "favourite_coffee": gettext_lazy("Favourite coffee"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # uid is the primary key - editing it would mean creating a different row
            # (order/payment history is tied to the old one), so only allow setting it
            # once, at creation.
            self.fields["uid"].disabled = True
