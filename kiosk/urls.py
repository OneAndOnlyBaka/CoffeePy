from django.urls import path

from . import views

app_name = "kiosk"

urlpatterns = [
    path("", views.index, name="index"),
    path("panel/", views.panel, name="panel"),
    path("language/", views.language, name="language"),
    path("nav/<str:mode>/", views.nav, name="nav"),
    path("ranking/next/", views.ranking_next, name="ranking_next"),
    path("order/select/", views.order_select, name="order_select"),
    path("order/confirm/", views.order_confirm, name="order_confirm"),
    path("order/cancel/", views.order_cancel, name="order_cancel"),
    path("settings/save/", views.settings_save, name="settings_save"),
    path("settings/touch/", views.settings_touch, name="settings_touch"),
    path("pay/", views.pay, name="pay"),
    path("keyboard/", views.keyboard_edit, name="keyboard"),
    path("doom/", views.doom, name="doom"),
    path("qr-popup/", views.qr_popup, name="qr_popup"),
    path("link-phone-popup/", views.link_phone_popup, name="link_phone_popup"),
]
