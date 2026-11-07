from django.urls import path

from . import views

app_name = "kiosk"

urlpatterns = [
    path("", views.index, name="index"),
    path("panel/", views.panel, name="panel"),
    path("language/", views.language, name="language"),
    path("nav/<str:mode>/", views.nav, name="nav"),
    path("ranking/<str:view>/", views.ranking_view, name="ranking_view"),
    path("order/select/", views.order_select, name="order_select"),
    path("order/confirm/", views.order_confirm, name="order_confirm"),
    path("order/cancel/", views.order_cancel, name="order_cancel"),
    path("machine/start/", views.machine_start, name="machine_start"),
    path("machine/cancel/", views.machine_cancel, name="machine_cancel"),
    path("machine/free/", views.machine_free, name="machine_free"),
    path("settings/save/", views.settings_save, name="settings_save"),
    path("settings/touch/", views.settings_touch, name="settings_touch"),
    path("privacy/confirm/", views.privacy_confirm, name="privacy_confirm"),
    path("privacy/decline/", views.privacy_decline, name="privacy_decline"),
    path("pay/", views.pay, name="pay"),
    path("purchase/", views.purchase_book, name="purchase_book"),
    path("stock/", views.stock_count, name="stock_count"),
    path("qr-popup/", views.qr_popup, name="qr_popup"),
    path("link-phone-popup/", views.link_phone_popup, name="link_phone_popup"),
    path("machine-popup/", views.machine_popup, name="machine_popup"),
    path("machine/refill/", views.machine_refill, name="machine_refill"),
]
