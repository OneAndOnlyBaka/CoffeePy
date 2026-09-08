from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("login/", auth_views.LoginView.as_view(template_name="dashboard/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("statistics/", views.statistics, name="statistics"),
    path("pillory/", views.pillory, name="pillory"),
    path("coffee-sorts/", views.coffeesort_list, name="coffeesorts"),
    path("coffee-sorts/add/", views.coffeesort_add, name="coffeesort_add"),
    path("coffee-sorts/<int:pk>/edit/", views.coffeesort_edit, name="coffeesort_edit"),
    path("coffee-sorts/<int:pk>/delete/", views.coffeesort_delete, name="coffeesort_delete"),
    path("payment-methods/", views.paymentmethod_list, name="paymentmethods"),
    path("payment-methods/add/", views.paymentmethod_add, name="paymentmethod_add"),
    path("payment-methods/<int:pk>/edit/", views.paymentmethod_edit, name="paymentmethod_edit"),
    path("payment-methods/<int:pk>/delete/", views.paymentmethod_delete, name="paymentmethod_delete"),
    path("members/", views.member_list, name="members"),
    path("members/add/", views.member_add, name="member_add"),
    path("members/merge/", views.member_merge, name="member_merge"),
    path("members/merge/<int:keep_uid>/<int:remove_uid>/", views.member_merge_confirm, name="member_merge_confirm"),
    path("members/<int:uid>/edit/", views.member_edit, name="member_edit"),
    path("members/<int:uid>/delete/", views.member_delete, name="member_delete"),
    path("members/<int:uid>/alt-uid/", views.member_alt_uid_start, name="member_alt_uid_start"),
    path("members/<int:uid>/alt-uid/poll/", views.member_alt_uid_poll, name="member_alt_uid_poll"),
    path("members/<int:uid>/revoke-devices/", views.member_revoke_devices, name="member_revoke_devices"),
    path("members/<int:uid>/mark-departed/", views.member_mark_departed, name="member_mark_departed"),
    path("members/<int:uid>/reactivate/", views.member_reactivate, name="member_reactivate"),
    path("export/orders.csv", views.export_orders, name="export_orders"),
    path("export/payments.csv", views.export_payments, name="export_payments"),
    path("system/", views.system_dashboard, name="system_dashboard"),
    path("system-time/", views.system_time, name="system_time"),
    path("backup/", views.backup, name="backup"),
    path("backup/download/<str:filename>/", views.backup_download, name="backup_download"),
    path("update/", views.update_upload, name="update"),
    path("update/status/", views.update_status, name="update_status"),
    path("import/", views.import_legacy_db, name="import_legacy_db"),
    path("import/status/", views.import_status, name="import_status"),
    path("reset/", views.reset_database, name="reset_database"),
    path("audit-log/", views.audit_log, name="audit_log"),
]
