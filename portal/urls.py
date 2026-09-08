from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("link/<str:token>/", views.link, name="link"),
    path("link-invalid/", views.link_invalid, name="link_invalid"),
    path("", views.index, name="index"),
    path("pay/", views.pay, name="pay"),
    path("stats/", views.stats, name="stats"),
    path("devices/", views.devices, name="devices"),
    path("devices/<int:device_id>/revoke/", views.revoke_device, name="revoke_device"),
    path("cards/", views.cards, name="cards"),
    path("cards/add/", views.card_add_start, name="card_add_start"),
    path("cards/add/poll/", views.card_add_poll, name="card_add_poll"),
    path("logout/", views.logout_view, name="logout"),
]
