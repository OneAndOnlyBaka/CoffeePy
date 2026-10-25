from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from dashboard import views as dashboard_views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="kiosk:index", permanent=False)),
    path("admin/", admin.site.urls),
    path("kiosk/", include("kiosk.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("portal/", include("portal.urls")),
    path("health/", include("dashboard.health_urls")),
    path("certificate/", dashboard_views.certificate_download, name="certificate_download"),
    # set_language: POST language + next, sets the device's language cookie (config/language.py).
    path("i18n/", include("django.conf.urls.i18n")),
]
