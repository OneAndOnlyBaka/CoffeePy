from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy, pgettext_lazy

from .models import POLICY_LIMITS, PasswordPolicy
from coffee.models import CASH_METHOD_NAME, BackupSettings, CoffeeSort, Member, Order, Payment, PaymentMethod, payment_method_label


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
                "PayPal: your paypal.me username (e.g. \"coffeefund\"). "
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


ROLE_ADMIN, ROLE_MANAGER = "admin", "manager"
ROLE_CHOICES = [
    # An admin is a Django superuser: everything, including the System pages and this one.
    (ROLE_ADMIN, gettext_lazy("Admin - everything, including system and users")),
    (ROLE_MANAGER, gettext_lazy("Manager - members, bookings, coffee sorts, payment methods")),
]


class DashboardUserForm(forms.ModelForm):
    """Name, role and whether the account may log in. The password is set separately
    (UserCreateForm adds it for a new account, SetPasswordForm changes it)."""

    role = forms.ChoiceField(label=gettext_lazy("Role"), choices=ROLE_CHOICES, widget=forms.RadioSelect)

    class Meta:
        model = get_user_model()
        fields = ["username", "is_active"]
        labels = {
            "username": gettext_lazy("Username"),
            "is_active": gettext_lazy("May log in"),
        }
        help_texts = {
            "username": "",
            "is_active": gettext_lazy("Untick to lock the account without deleting it."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].initial = ROLE_ADMIN if self.instance.pk and self.instance.is_superuser else ROLE_MANAGER

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_superuser = user.is_staff = self.cleaned_data["role"] == ROLE_ADMIN
        if commit:
            user.save()
        return user


class UserCreateForm(DashboardUserForm):
    password1 = forms.CharField(label=gettext_lazy("Password"), strip=False, widget=forms.PasswordInput,
                                help_text=password_validation.password_validators_help_text_html)
    password2 = forms.CharField(label=gettext_lazy("Password again"), strip=False, widget=forms.PasswordInput)
    must_change_password = forms.BooleanField(
        label=gettext_lazy("Must set a new password at the first login"), required=False, initial=True,
        help_text=gettext_lazy("Recommended: then only the new user knows their password."))

    class Meta(DashboardUserForm.Meta):
        fields = ["username"]

    def clean_password2(self):
        first, second = self.cleaned_data.get("password1"), self.cleaned_data.get("password2")
        if first and second and first != second:
            raise forms.ValidationError(gettext_lazy("The two passwords don't match."))
        password_validation.validate_password(second, self.instance)
        return second

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password2"])
        if commit:
            user.save()
        return user


def _payment_method_choices():
    """Cash, every configured method, and - when editing - whatever the payment was
    recorded with, even if that method has since been renamed or removed."""
    names = [CASH_METHOD_NAME] + list(PaymentMethod.objects.values_list("name", flat=True))
    return [(name, payment_method_label(name)) for name in dict.fromkeys(names)]


class CommaDecimalField(forms.DecimalField):
    """An amount typed either way, "1,50" or "1.50" - German fingers type the comma, and a
    number input would refuse it (the kiosk's pay view does the same)."""

    widget = forms.TextInput(attrs={"inputmode": "decimal"})

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
        return super().to_python(value)


class LocalDateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


class PaymentForm(forms.ModelForm):
    """A payment entered or corrected by hand - e.g. cash handed over in person."""

    method_name = forms.ChoiceField(label=gettext_lazy("Paid via"))

    class Meta:
        model = Payment
        fields = ["member", "value", "method_name", "created_at", "info"]
        labels = {
            "member": gettext_lazy("Member"),
            "value": gettext_lazy("Amount (€)"),
            "created_at": gettext_lazy("Date and time"),
            "info": gettext_lazy("Note"),
        }
        widgets = {"created_at": LocalDateTimeInput()}
        field_classes = {"value": CommaDecimalField}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = _payment_method_choices()
        current = self.instance.method_name if self.instance.pk else None
        if current and current not in dict(choices):
            choices.append((current, payment_method_label(current)))
        self.fields["method_name"].choices = choices
        self.fields["member"].queryset = Member.objects.order_by("nickname", "uid")
        self.fields["created_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        if not self.instance.pk:
            self.fields["created_at"].initial = timezone.localtime().replace(second=0, microsecond=0)

    def clean_value(self):
        value = self.cleaned_data["value"]
        if value == 0:
            raise forms.ValidationError(gettext_lazy("Enter an amount other than 0."))
        return value


class OrderForm(forms.ModelForm):
    """Correcting a booked coffee - the wrong sort, a price, the number of cups."""

    class Meta:
        model = Order
        fields = ["coffee_sort", "value", "machine_strokes", "created_at"]
        labels = {
            "coffee_sort": gettext_lazy("Coffee sort"),
            "value": gettext_lazy("Price (€)"),
            "machine_strokes": gettext_lazy("Cups"),
            "created_at": gettext_lazy("Date and time"),
        }
        widgets = {"created_at": LocalDateTimeInput()}
        field_classes = {"value": CommaDecimalField}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["created_at"].input_formats = ["%Y-%m-%dT%H:%M"]


class AdminSetPasswordForm(SetPasswordForm):
    """An admin setting someone else's password - by default they have to replace it at
    their next login, so it stays theirs alone."""

    must_change_password = forms.BooleanField(
        label=gettext_lazy("Must set a new password at the next login"), required=False, initial=True)


class OwnPasswordChangeForm(PasswordChangeForm):
    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("new_password2")
        if password and self.user.check_password(password):
            self.add_error("new_password2", gettext_lazy("The new password must differ from the current one."))
        return cleaned


class PasswordPolicyForm(forms.ModelForm):
    """The login rules, each kept within POLICY_LIMITS."""

    class Meta:
        model = PasswordPolicy
        fields = list(POLICY_LIMITS)
        labels = {
            "min_length": gettext_lazy("Minimum length"),
            "min_kinds": gettext_lazy("Character kinds needed"),
            "passphrase_length": gettext_lazy("Passphrase: minimum length"),
            "passphrase_kinds": gettext_lazy("Passphrase: character kinds needed"),
            "lock_after_failures": gettext_lazy("Lock after this many failed logins"),
            "lock_minutes": gettext_lazy("Lock for (minutes)"),
            "idle_logout_minutes": gettext_lazy("Log out after inactivity (minutes)"),
        }
        help_texts = {
            "min_kinds": gettext_lazy("Of the four: lower case, upper case, digits, other characters."),
            "passphrase_length": gettext_lazy("A long password may make do with fewer kinds. 0 turns this off."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, (low, high) in POLICY_LIMITS.items():
            field = self.fields[name]
            field.min_value, field.max_value = low, high
            field.validators += [MinValueValidator(low), MaxValueValidator(high)]
            field.widget.attrs.update(min=low, max=high)
            extra = gettext_lazy("Allowed: %(low)s to %(high)s.") % {"low": low, "high": high}
            field.help_text = f"{field.help_text} {extra}".strip()

    def clean(self):
        cleaned = super().clean()
        passphrase, minimum = cleaned.get("passphrase_length"), cleaned.get("min_length")
        if passphrase and minimum and passphrase <= minimum:
            self.add_error("passphrase_length", gettext_lazy("Longer than the minimum length - or 0."))
        return cleaned


BACKUP_LIMITS = {
    "interval_minutes": (15, 1440),
    "keep_recent": (1, 500),
    "keep_daily": (0, 90),
    "keep_weekly": (0, 52),
    "keep_monthly": (0, 60),
}


class BackupSettingsForm(forms.ModelForm):
    class Meta:
        model = BackupSettings
        fields = list(BACKUP_LIMITS)
        labels = {
            "interval_minutes": gettext_lazy("Back up every (minutes)"),
            "keep_recent": gettext_lazy("Keep the newest"),
            "keep_daily": gettext_lazy("Then one a day, for (days)"),
            "keep_weekly": gettext_lazy("Then one a week, for (weeks)"),
            "keep_monthly": gettext_lazy("Then one a month, for (months)"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, (low, high) in BACKUP_LIMITS.items():
            field = self.fields[name]
            field.validators += [MinValueValidator(low), MaxValueValidator(high)]
            field.widget.attrs.update(min=low, max=high)
            field.help_text = gettext_lazy("Allowed: %(low)s to %(high)s.") % {"low": low, "high": high}


RESTORE_PART_CHOICES = [
    ("database", gettext_lazy("Data: members, bookings, coffee sorts, dashboard logins")),
    ("certificates", gettext_lazy("Certificates and the device CA - phones keep trusting the device")),
    ("identity", gettext_lazy("Device identity - its backup stick becomes this device's")),
    ("settings", gettext_lazy("Settings: language, address, time zone, log retention")),
]


class RestoreForm(forms.Form):
    """What to bring back from a backup, and the confirmation. The parts beyond the data are
    for a replacement device; on the same device the data alone is the usual choice."""

    parts = forms.MultipleChoiceField(label=gettext_lazy("Restore"), choices=RESTORE_PART_CHOICES,
                                      widget=forms.CheckboxSelectMultiple, initial=["database"])
    passphrase = forms.CharField(
        label=gettext_lazy("Passphrase"), required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
        help_text=gettext_lazy("Only for an encrypted backup made with a different passphrase than this device's."))
    confirm = forms.CharField(label=gettext_lazy("Type DELETE to confirm"), widget=forms.TextInput(
        attrs={"autocomplete": "off"}))

    def __init__(self, *args, device_backup=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not device_backup:  # an older database dump has nothing else in it
            self.fields["parts"].choices = RESTORE_PART_CHOICES[:1]

    def clean_confirm(self):
        if self.cleaned_data["confirm"].strip() != "DELETE":
            raise forms.ValidationError(gettext_lazy("Type DELETE to confirm - nothing was changed."))
        return "DELETE"
