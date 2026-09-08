from django.contrib import admin

from .models import CoffeeSort, Member, NfcScan, Order, Payment


@admin.register(CoffeeSort)
class CoffeeSortAdmin(admin.ModelAdmin):
    list_display = ["name", "price", "machine_strokes"]
    search_fields = ["name"]


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ["uid", "nickname", "alt_card_uids", "favourite_coffee"]
    search_fields = ["uid", "alt_cards__uid", "nickname"]
    list_select_related = ["favourite_coffee"]

    @admin.display(description="Alt cards")
    def alt_card_uids(self, obj):
        return ", ".join(str(uid) for uid in obj.alt_cards.values_list("uid", flat=True)) or "-"


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["member", "coffee_sort", "value", "machine_strokes", "created_at"]
    list_filter = ["coffee_sort"]
    date_hierarchy = "created_at"
    autocomplete_fields = ["member"]
    search_fields = ["member__uid", "member__nickname"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["member", "value", "created_at"]
    date_hierarchy = "created_at"
    autocomplete_fields = ["member"]
    search_fields = ["member__uid", "member__nickname"]


@admin.register(NfcScan)
class NfcScanAdmin(admin.ModelAdmin):
    list_display = ["created_at", "event", "uid", "card_type"]
    readonly_fields = [f.name for f in NfcScan._meta.fields]

    def has_add_permission(self, request):
        return False
