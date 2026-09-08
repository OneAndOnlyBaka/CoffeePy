from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="kiosk:index", permanent=False)),
    path("admin/", admin.site.urls),
    path("kiosk/", include("kiosk.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("portal/", include("portal.urls")),
    path("health/", include("dashboard.health_urls")),
]
