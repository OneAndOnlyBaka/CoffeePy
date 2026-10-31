import csv
import json
import os
import tempfile
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from coffee import backups, clock, names, purge, rtc, services
from coffee.models import (
    AltUidCapture,
    AuditLogEntry,
    CoffeeSort,
    Member,
    MemberCard,
    NfcReaderStatus,
    Order,
    Payment,
    PaymentMethod,
)
from coffee.network import public_host

from . import audit, import_jobs, night, system_clock, system_stats, tls
from .decorators import superuser_required
from .models import AccountSecurity, DeletionHold, KioskBoards, MemberRetention, NamePolicy, NightPolicy, PasswordPolicy
from .forms import (
    ROLE_ADMIN,
    AdminSetPasswordForm,
    CoffeeSortForm,
    DashboardUserForm,
    MemberForm,
    MemberRetentionForm,
    OrderForm,
    OwnPasswordChangeForm,
    NamePolicyForm,
    KioskBoardsForm,
    NightPolicyForm,
    PasswordPolicyForm,
    PaymentForm,
    PaymentMethodForm,
    UserCreateForm,
)

@login_required
def index(request):
    reader_status = NfcReaderStatus.objects.filter(pk=1).first()
    return render(
        request,
        "dashboard/index.html",
        {"reader_status": reader_status, "public_host": public_host(), "overview": services.dashboard_overview(),
         "backup_health": backups.health(), "rtc_health": rtc.health(), "clock_corrected": clock.corrected(),
         "due_members": services.departed_members_due(MemberRetention.current().departed_months)},
    )


@login_required
def statistics(request):
    ctx = {
        "monthly_totals": json.dumps(services.monthly_totals()),
        "coffee_distribution": json.dumps(services.coffee_distribution()),
        "price_trend": json.dumps(services.quarterly_price_trend()),
        "payment_methods": json.dumps(services.payment_method_totals()),
        "hourly_distribution": json.dumps(services.hourly_distribution()),
        "weekday_distribution": json.dumps(services.weekday_distribution()),
    }
    return render(request, "dashboard/statistics.html", ctx)


@login_required
def pillory(request):
    return render(request, "dashboard/pillory.html", {"entries": services.deposit_pillory(limit=1000)})


@login_required
def coffeesort_list(request):
    return render(request, "dashboard/coffeesorts.html", {"sorts": CoffeeSort.objects.all()})


@login_required
def coffeesort_add(request):
    if request.method == "POST":
        form = CoffeeSortForm(request.POST)
        if form.is_valid():
            sort = form.save()
            audit.log(request, "coffee_sort.create", target=sort.name)
            return redirect("dashboard:coffeesorts")
    else:
        form = CoffeeSortForm()
    return render(request, "dashboard/coffeesort_form.html", {"form": form, "instance": None})


@login_required
def coffeesort_edit(request, pk):
    instance = get_object_or_404(CoffeeSort, pk=pk)
    if request.method == "POST":
        form = CoffeeSortForm(request.POST, instance=instance)
        if form.is_valid():
            sort = form.save()
            audit.log(request, "coffee_sort.update", target=sort.name)
            return redirect("dashboard:coffeesorts")
    else:
        form = CoffeeSortForm(instance=instance)
    return render(request, "dashboard/coffeesort_form.html", {"form": form, "instance": instance})


@login_required
@require_POST
def coffeesort_delete(request, pk):
    sort = get_object_or_404(CoffeeSort, pk=pk)
    name = sort.name
    sort.delete()
    audit.log(request, "coffee_sort.delete", target=name)
    return redirect("dashboard:coffeesorts")


@login_required
def paymentmethod_list(request):
    return render(request, "dashboard/paymentmethods.html", {"methods": PaymentMethod.objects.all()})


@login_required
def paymentmethod_add(request):
    if request.method == "POST":
        form = PaymentMethodForm(request.POST)
        if form.is_valid():
            method = form.save()
            audit.log(request, "payment_method.create", target=method.name)
            return redirect("dashboard:paymentmethods")
    else:
        form = PaymentMethodForm()
    return render(request, "dashboard/paymentmethod_form.html", {"form": form, "instance": None})


@login_required
def paymentmethod_edit(request, pk):
    instance = get_object_or_404(PaymentMethod, pk=pk)
    if request.method == "POST":
        form = PaymentMethodForm(request.POST, instance=instance)
        if form.is_valid():
            method = form.save()
            audit.log(request, "payment_method.update", target=method.name)
            return redirect("dashboard:paymentmethods")
    else:
        form = PaymentMethodForm(instance=instance)
    return render(request, "dashboard/paymentmethod_form.html", {"form": form, "instance": instance})


@login_required
@require_POST
def paymentmethod_delete(request, pk):
    method = get_object_or_404(PaymentMethod, pk=pk)
    name = method.name
    method.delete()
    audit.log(request, "payment_method.delete", target=name)
    return redirect("dashboard:paymentmethods")


@login_required
def member_list(request):
    # Annotated rather than member.devices.count in the template: that was two
    # queries per member row. distinct: the join with orders would multiply the devices.
    # last_order - when someone stopped coming is what tells a nameless card that left the
    # company from one still in use.
    members = list(Member.objects.select_related("favourite_coffee").prefetch_related("alt_cards")
                   .annotate(device_count=Count("devices", distinct=True), last_order=Max("orders__created_at"))
                   .order_by("nickname", "uid"))
    blocked = NamePolicy.current().blocked_list()
    for member in members:
        # Who the kiosk will ask for a proper name at their next coffee (coffee/names.py).
        # A name that only lacks its form ("Anna B") is put right silently, so it is no issue.
        member.name_issue = None if member.is_departed else names.message(
            names.problem(names.normalize(member.nickname), member=member, extra_blocked=blocked))
    return render(request, "dashboard/members.html",
                  {"members": members, "name_policy_form": NamePolicyForm(instance=NamePolicy.current()),
                   "retention_form": MemberRetentionForm(instance=MemberRetention.current()),
                   "due_members": services.departed_members_due(MemberRetention.current().departed_months),
                   "holds": DeletionHold.objects.select_related("member").order_by("member__nickname")})


@login_required
@superuser_required
@require_POST
def member_name_rules(request):
    form = NamePolicyForm(request.POST, instance=NamePolicy.current())
    if form.is_valid():
        policy = form.save()
        audit.log(request, "name_policy.update", details=", ".join(policy.blocked_list())[:500])
    return redirect("dashboard:members")


@login_required
@superuser_required
@require_POST
def member_retention(request):
    form = MemberRetentionForm(request.POST, instance=MemberRetention.current())
    if form.is_valid():
        retention = form.save()
        audit.log(request, "member_retention.update", details=f"{retention.departed_months} month(s)")
    else:
        messages.error(request, " ".join(form.errors.get("departed_months", [])))
    return redirect("dashboard:members")


@login_required
@require_POST
def member_keep(request, uid):
    """Keeps a departed member suggested for deletion - with the reason, which is required: the
    next admin has to know why this one stays."""
    member = get_object_or_404(Member, pk=uid, departed_at__isnull=False)
    reason = request.POST.get("reason", "").strip()[:200]
    if not reason:
        messages.error(request, _("Say why the member is kept."))
        return redirect(reverse("dashboard:members") + "#due")
    DeletionHold.objects.update_or_create(member=member, defaults={"reason": reason,
                                                                   "held_by": request.user.get_username()})
    audit.log(request, "member.keep", target=audit.member_actor(member), details=reason)
    return redirect(reverse("dashboard:members") + "#due")


@login_required
@require_POST
def member_keep_undo(request, uid):
    member = get_object_or_404(Member, pk=uid)
    DeletionHold.objects.filter(member=member).delete()
    audit.log(request, "member.keep_undo", target=audit.member_actor(member))
    return redirect(reverse("dashboard:members") + "#due")


@login_required
def member_export(request, uid):
    """One member's data as JSON - their right of access (Art. 15 GDPR) without picking their
    rows out of the full CSV exports."""
    member = get_object_or_404(Member, pk=uid)
    response = services.member_data_response(member)
    audit.log(request, "member.export", target=audit.member_actor(member))
    return response


@login_required
def member_add(request):
    if request.method == "POST":
        form = MemberForm(request.POST)
        if form.is_valid():
            member = form.save()
            audit.log(request, "member.create", target=f"{member.display_name()} (uid {member.uid})")
            return redirect("dashboard:members")
    else:
        form = MemberForm()
    return render(request, "dashboard/member_form.html", {"form": form, "instance": None})


@login_required
def member_edit(request, uid):
    instance = get_object_or_404(Member, pk=uid)
    if request.method == "POST":
        form = MemberForm(request.POST, instance=instance)
        if form.is_valid():
            member = form.save()
            audit.log(request, "member.update", target=f"{member.display_name()} (uid {member.uid})")
            return redirect("dashboard:members")
    else:
        form = MemberForm(instance=instance)
    return render(request, "dashboard/member_form.html", {"form": form, "instance": instance})


@login_required
@require_POST
def member_delete(request, uid):
    member = get_object_or_404(Member, pk=uid)
    target = f"{member.display_name()} (uid {member.uid})"
    member.delete()
    audit.log(request, "member.delete", target=target)
    return redirect("dashboard:members")


@login_required
@require_POST
def member_revoke_devices(request, uid):
    member = get_object_or_404(Member, pk=uid)
    session_keys = list(member.devices.values_list("session_key", flat=True))
    Session.objects.filter(pk__in=session_keys).delete()
    count = member.devices.count()
    member.devices.all().delete()
    audit.log(request, "member.revoke_devices", target=member.display_name(), details=f"{count} device(s)")
    return redirect("dashboard:members")


@login_required
@require_POST
def member_mark_departed(request, uid):
    """For someone who left without settling up - acknowledges/writes off the debt
    (excluded from the pillory and rankings from then on, see coffee.services) and
    deactivates their card(s) at the kiosk/portal, without deleting their history."""
    member = get_object_or_404(Member, pk=uid)
    member.departed_at = timezone.now()
    member.save(update_fields=["departed_at"])
    audit.log(request, "member.mark_departed", target=member.display_name())
    return redirect("dashboard:members")


@login_required
@require_POST
def member_reactivate(request, uid):
    member = get_object_or_404(Member, pk=uid)
    member.departed_at = None
    member.save(update_fields=["departed_at"])
    DeletionHold.objects.filter(member=member).delete()  # it was about a departure that is over
    audit.log(request, "member.reactivate", target=member.display_name())
    return redirect("dashboard:members")


ALT_UID_CAPTURE_SECONDS = 60


@login_required
def member_alt_uid_start(request, uid):
    """A card's numeric UID isn't something a human can type from memory, so instead of
    a form field: arm the kiosk to link whatever card gets tapped next to this member
    (see AltUidCapture and kiosk.views._handle_new_scan, which actually fulfills it - the
    kiosk and dashboard are separate browser sessions, so this is tracked in the DB, not
    request.session). Only one capture is meant to be armed at a time (single physical
    reader), so arming a new one clears any previous one first."""
    member = get_object_or_404(Member, pk=uid)
    AltUidCapture.objects.all().delete()
    AltUidCapture.objects.create(member=member, expires_at=timezone.now() + timedelta(seconds=ALT_UID_CAPTURE_SECONDS))
    return render(request, "dashboard/member_alt_uid.html", {"member": member, "seconds": ALT_UID_CAPTURE_SECONDS})


@login_required
@require_GET
def member_alt_uid_poll(request, uid):
    capture = AltUidCapture.objects.filter(member_id=uid).first()
    if capture is None:
        # The kiosk deletes the row the moment it successfully applies a card - so its
        # absence here means success, not merely "nothing happened yet".
        response = HttpResponse("")
        response["HX-Redirect"] = reverse("dashboard:members")
        return response

    if capture.is_expired():
        capture.delete()
        timed_out = _("Timed out - no card was tapped within %(seconds)s seconds.") % {
            "seconds": ALT_UID_CAPTURE_SECONDS
        }
        return HttpResponse(
            f'<p class="msg error">{escape(timed_out)}</p>'
            f'<p><a href="{reverse("dashboard:member_alt_uid_start", args=[uid])}">{escape(_("Try again"))}</a></p>'
        )

    seconds_left = int((capture.expires_at - timezone.now()).total_seconds())
    # Stored in English by the kiosk (kiosk.views.CARD_CONFLICT_ERROR), translated for whoever looks.
    error_html = f'<p class="msg error">{escape(_(capture.last_error))}</p>' if capture.last_error else ""
    waiting = _("Waiting for a card to be tapped on the kiosk reader… (%(seconds)ss left)") % {"seconds": seconds_left}
    return HttpResponse(f"{error_html}<p>{escape(waiting)}</p>")


@login_required
def member_merge(request):
    """For when a member ended up with two accounts instead of one - e.g. their old card
    broke and a fresh tap started a brand new account rather than using "Set alt card"
    up front. Picks which account to keep; the other's orders/payments move over and it
    gets deleted, so this has its own confirmation step rather than the plain JS confirm()
    used for a single delete."""
    error = None
    if request.method == "POST":
        keep_uid = request.POST.get("keep")
        remove_uid = request.POST.get("remove")
        if not keep_uid or not remove_uid:
            error = _("Pick both accounts.")
        elif keep_uid == remove_uid:
            error = _("Pick two different accounts.")
        else:
            return redirect("dashboard:member_merge_confirm", keep_uid=keep_uid, remove_uid=remove_uid)
    return render(
        request,
        "dashboard/member_merge.html",
        {"members": Member.objects.order_by("nickname", "uid"), "error": error},
    )


def _nickname_switch_plan(keep, remove):
    """Whichever of the two accounts was more recently active (its last order or
    payment) keeps its nickname after the merge - an admin picking "keep"/"remove" in
    the UI shouldn't accidentally revert someone to an old/stale display name. Returns
    the nickname to use, and whether that's actually a change from `keep`'s current one."""
    keep_last_active = services.last_active_at(keep)
    remove_last_active = services.last_active_at(remove)
    remove_is_more_recent = remove_last_active is not None and (
        keep_last_active is None or remove_last_active > keep_last_active
    )
    if remove_is_more_recent and remove.nickname and remove.nickname != keep.nickname:
        return remove.nickname, True
    return keep.nickname, False


@login_required
def member_merge_confirm(request, keep_uid, remove_uid):
    keep = get_object_or_404(Member, pk=keep_uid)
    remove = get_object_or_404(Member, pk=remove_uid)
    final_nickname, nickname_switched = _nickname_switch_plan(keep, remove)

    if request.method == "POST":
        with transaction.atomic():
            remove_orders = remove.orders.update(member=keep)
            remove_payments = remove.payments.update(member=keep)
            # remove's own alt cards (if it had any from an earlier merge/"Set alt
            # card") move over too - MemberCard.member has on_delete=CASCADE, so this
            # has to happen before remove.delete() or they'd be deleted along with it.
            remove_cards = remove.alt_cards.update(member=keep)
            old_uid = remove.uid
            remove.delete()
            MemberCard.objects.create(member=keep, uid=old_uid)
            if nickname_switched:
                keep.nickname = final_nickname
                keep.save(update_fields=["nickname"])
        audit.log(
            request,
            "member.merge",
            target=f"{keep.display_name()} (uid {keep.uid})",
            details=(
                f"merged in uid {old_uid}, moved {remove_orders} order(s) + {remove_payments} payment(s)"
                + (f" + {remove_cards} alt card(s)" if remove_cards else "")
                + (f", nickname switched to '{final_nickname}' (more recently active card)" if nickname_switched else "")
            ),
        )
        return render(
            request,
            "dashboard/member_merge_done.html",
            {
                "keep": keep,
                "old_uid": old_uid,
                "moved_orders": remove_orders,
                "moved_payments": remove_payments,
                "moved_cards": remove_cards,
                "nickname_switched": nickname_switched,
            },
        )

    return render(
        request,
        "dashboard/member_merge_confirm.html",
        {
            "keep": keep,
            "remove": remove,
            "keep_orders": keep.orders.count(),
            "keep_payments": keep.payments.count(),
            "remove_orders": remove.orders.count(),
            "remove_payments": remove.payments.count(),
            "remove_cards": remove.alt_cards.count(),
            "final_nickname": final_nickname,
            "nickname_switched": nickname_switched,
        },
    )


@login_required
def export_orders(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="orders.csv"'
    writer = csv.writer(response)
    writer.writerow(["uid", "nickname", "coffee_sort", "value", "machine_strokes", "created_at", "info"])
    # Plain tuples instead of model instances: with a few years of history that is tens of
    # thousands of orders, and building an Order + Member + CoffeeSort for each took ~12s
    # on a Pi 3.
    rows = (
        Order.objects.order_by("created_at")
        .values_list("member_id", "member__nickname", "coffee_sort__name", "value", "machine_strokes",
                     "created_at", "info")
        .iterator(chunk_size=2000)
    )
    for member_id, nickname, sort_name, value, strokes, created_at, info in rows:
        writer.writerow([member_id, nickname, sort_name or "", value, strokes,
                         created_at.isoformat(), info])
    return response


@login_required
def export_payments(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="payments.csv"'
    writer = csv.writer(response)
    writer.writerow(["uid", "nickname", "value", "created_at", "info"])
    for payment in Payment.objects.select_related("member").order_by("created_at"):
        writer.writerow([
            payment.member_id,
            payment.member.nickname,
            payment.value,
            payment.created_at.isoformat(),
            payment.info,
        ])
    return response


def _ssh_status():
    """{"active", "error"} as the host's storage helper last saw it - None while it doesn't run."""
    status = backups.storage_status()
    return status.get("ssh") if status.get("helper_running") and "active" in status.get("ssh", {}) else None


@login_required
@superuser_required
def system_dashboard(request):
    policy, boards = NightPolicy.current(), KioskBoards.current()
    form = NightPolicyForm(instance=policy)
    boards_form = KioskBoardsForm(instance=boards)
    if request.method == "POST" and request.POST.get("form") == "ssh":
        # The host's storage helper switches it (install/storage_helper.py), within seconds.
        enabled = request.POST.get("enabled") == "1"
        backups.storage_request("ssh", enabled=enabled, requested_by=request.user.get_username())
        audit.log(request, "ssh.enable" if enabled else "ssh.disable")
        messages.success(request, _("SSH is being switched on - reload in a few seconds.") if enabled
                         else _("SSH is being switched off - reload in a few seconds."))
        return redirect("dashboard:system_dashboard")
    if request.method == "POST" and request.POST.get("form") == "boards":
        boards_form = KioskBoardsForm(request.POST, instance=boards)
        if boards_form.is_valid():
            boards = boards_form.save()
            audit.log(request, "kiosk_boards.update", details=", ".join(
                f"{name} {'on' if getattr(boards, name) else 'off'}" for name in ("ranking", "pillory")))
            messages.success(request, _("Saved - the kiosk follows with its next poll."))
            return redirect("dashboard:system_dashboard")
    elif request.method == "POST":
        form = NightPolicyForm(request.POST, instance=policy)
        if form.is_valid():
            policy = form.save()
            night.write_file(policy)
            audit.log(request, "night.update", details=night.describe(policy))
            messages.success(request, _("Saved - the kiosk follows within a minute."))
            return redirect("dashboard:system_dashboard")
    return render(request, "dashboard/system_dashboard.html", {
        "stats": system_stats.gather(), "public_host": public_host(), "night_form": form,
        "boards_form": boards_form, "ssh": _ssh_status(),
        # Without PUBLIC_HOST the address is detected inside the container - usually the
        # container's own, which no phone reaches (coffee/network.py).
        "public_host_guessed": not settings.PUBLIC_HOST,
    })


@login_required
@superuser_required
def system_time(request):
    error = None
    if request.method == "POST" and request.POST.get("confirm"):
        # The clock check (coffee/clock.py) put the clock forward, and it is right as it is.
        clock.confirmed()
        audit.log(request, "system_time.confirmed", details=timezone.localtime().isoformat(timespec="seconds"))
        return redirect("dashboard:system_time")
    if request.method == "POST":
        dt_str = request.POST.get("datetime")
        try:
            new_dt = datetime.fromisoformat(dt_str)
            system_clock.set_system_datetime(new_dt)
        except Exception as exc:  # noqa: BLE001 - surfaced to the admin, not swallowed
            error = str(exc)
        else:
            clock.confirmed()
            audit.log(request, "system_time.set", details=new_dt.isoformat())
            return redirect("dashboard:system_time")
    return render(request, "dashboard/system_time.html",
                  {"now": timezone.localtime(), "error": error, "clock_corrected": clock.corrected()})


@login_required
@superuser_required
def certificate(request):
    """What port 443 presents, and the upload that replaces it with the admin's own
    certificate (or goes back to the device's self-signed one) - see dashboard/tls.py."""
    error = None
    warnings = []
    if request.method == "POST":
        if request.POST.get("action") == "revert":
            tls.remove_custom()
            audit.log(request, "certificate.revert")
            return redirect("dashboard:certificate")
        cert, key = request.FILES.get("certificate"), request.FILES.get("key")
        if not cert or not key:
            error = _("Please choose both the certificate and its private key.")
        else:
            try:
                warnings = tls.install_custom(cert.read(), key.read())
            except tls.TLSError as exc:
                error = str(exc)
            else:
                audit.log(request, "certificate.upload", target=cert.name)
    return render(request, "dashboard/certificate.html", {
        "active": tls.active_certificate(),
        "error": error,
        "warnings": warnings,
        "uploaded": request.method == "POST" and not error,
    })


@require_GET
def certificate_download(request):
    """No login: a phone needs this before it can open the login page without a warning."""
    found = tls.download()
    if found is None:
        raise Http404("No certificate has been created yet.")
    data, filename = found
    response = HttpResponse(data, content_type="application/x-x509-ca-cert")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@superuser_required
def import_legacy_db(request):
    """GET renders the upload page; POST (sent by its JavaScript) stores the upload and
    kicks off the import in the background, so the page can show a spinner and progress
    instead of a browser that hangs for minutes on a big database."""
    if request.method == "POST":
        upload = request.FILES.get("dump")
        if not upload:
            return JsonResponse({"error": _("Please choose a file to upload.")}, status=400)
        if import_jobs.is_running():
            return JsonResponse({"error": _("An import is already running.")}, status=409)

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            for chunk in upload.chunks():
                tmp.write(chunk)
        finally:
            tmp.close()
        import_jobs.start_import(
            tmp.name,
            upload.name,
            request.user.get_username(),
            purge=request.POST.get("purge") == "1",
        )
        return JsonResponse(import_jobs.localized(import_jobs.read_status()))
    return render(request, "dashboard/import.html", {"status": import_jobs.localized(import_jobs.read_status())})


@login_required
@superuser_required
@require_GET
def import_status(request):
    return JsonResponse(import_jobs.localized(import_jobs.read_status()))


RESET_CONFIRMATION = "DELETE"


@login_required
@superuser_required
def reset_database(request):
    """Wipes all coffee data - members, orders, payments, sorts, payment methods, NFC
    scans and the audit log. Dashboard logins are deliberately left alone; this is meant
    to hand a box over empty, not to lock its admin out of it."""
    message = None
    error = None
    if request.method == "POST":
        if request.POST.get("confirm", "").strip() != RESET_CONFIRMATION:
            error = _("Type %(word)s to confirm - nothing was deleted.") % {"word": RESET_CONFIRMATION}
        elif import_jobs.is_running():
            error = _("An import is running - wait for it to finish.")
        else:
            summary = purge.describe(purge.purge(purge.FULL_SCOPE))
            # Logged after the wipe on purpose: the audit log is part of what gets
            # deleted, so this entry is what's left to say the box was reset and by whom.
            audit.log(request, "database.reset", details=summary)
            message = _("Database reset - deleted %(summary)s.") % {"summary": summary}
    return render(
        request,
        "dashboard/reset.html",
        {
            "message": message,
            "error": error,
            "confirmation": RESET_CONFIRMATION,
            "counts": [
                (_("Members"), Member.objects.count()),
                (_("Orders"), Order.objects.count()),
                (_("Payments"), Payment.objects.count()),
                (_("Coffee sorts"), CoffeeSort.objects.count()),
                (_("Payment methods"), PaymentMethod.objects.count()),
                (_("Audit log entries"), AuditLogEntry.objects.count()),
            ],
        },
    )


AUDIT_PER_PAGE = 100


def _audit_entries(request):
    """The audit log filtered as the page's form says: free text over who/what/target/
    details/address, the source, the outcome, and a date range."""
    entries = AuditLogEntry.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        for word in q.split():
            entries = entries.filter(Q(action__icontains=word) | Q(actor__icontains=word) | Q(target__icontains=word)
                                     | Q(details__icontains=word) | Q(ip__icontains=word))
    if request.GET.get("source") in dict(AuditLogEntry.SOURCE_CHOICES):
        entries = entries.filter(source=request.GET["source"])
    if request.GET.get("outcome") in (AuditLogEntry.OUTCOME_SUCCESS, AuditLogEntry.OUTCOME_FAILURE):
        entries = entries.filter(outcome=request.GET["outcome"])
    for param, lookup in (("from", "created_at__date__gte"), ("to", "created_at__date__lte")):
        try:
            entries = entries.filter(**{lookup: datetime.strptime(request.GET.get(param, ""), "%Y-%m-%d").date()})
        except ValueError:
            pass
    return entries.order_by("-created_at", "-id")


@login_required
@superuser_required
def audit_log(request):
    audit.ingest_host_events()  # what happened on the host since the backup service last collected it
    entries = _audit_entries(request)
    if request.GET.get("format") == "csv":
        audit.log(request, "audit.export", details=request.GET.urlencode()[:500])
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
        writer = csv.writer(response)
        writer.writerow(["time", "source", "actor", "action", "target", "details", "ip", "outcome"])
        for e in entries.iterator():
            writer.writerow([timezone.localtime(e.created_at).isoformat(), e.source, e.actor, e.action, e.target,
                             e.details, e.ip or "", e.outcome])
        return response
    page = Paginator(entries, AUDIT_PER_PAGE).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "dashboard/audit_log.html", {
        "page": page, "query": query.urlencode(), "sources": AuditLogEntry.SOURCE_CHOICES,
        "retention_days": os.environ.get("AUDIT_RETENTION_DAYS", "90"),
    })


@require_GET
def health(request):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    status = "ok" if db_ok else "error"
    return JsonResponse({"status": status, "time": timezone.now().isoformat()}, status=200 if db_ok else 503)


@login_required
def account(request):
    """Everyone's own page: change the password. The session survives the change
    (update_session_auth_hash) - otherwise it would log the user straight out."""
    form = OwnPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        AccountSecurity.objects.filter(user=user).update(must_change_password=False)
        audit.log(request, "account.password_change", target=user.get_username())
        messages.success(request, _("Password changed."))
        return redirect("dashboard:account")
    return render(request, "dashboard/account.html", {"form": form})


# --- dashboard users ----------------------------------------------------------------------

def _lockout_error(request, user, *, admin, active, deleting=False):
    """Why a change to `user` must not happen, or None: nobody takes their own admin rights
    away, locks or deletes themselves. That alone keeps the device from ending up without an
    admin who can log in - whoever edits users here is one - which would otherwise take a
    shell on the Pi to undo."""
    if user.pk == request.user.pk and (deleting or not admin or not active):
        return _("You can't remove your own admin rights, lock or delete your own account.")
    return None


@login_required
@superuser_required
def users(request):
    return render(request, "dashboard/users.html", {
        "users": get_user_model().objects.order_by("-is_superuser", "username"),
    })


@login_required
@superuser_required
def user_add(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        AccountSecurity.objects.update_or_create(
            user=user, defaults={"must_change_password": form.cleaned_data["must_change_password"]})
        audit.log(request, "user.create", target=user.get_username(),
                  details=form.cleaned_data["role"] + (", must change password" if form.cleaned_data["must_change_password"] else ""))
        messages.success(request, _("User %(name)s created.") % {"name": user.get_username()})
        return redirect("dashboard:users")
    return render(request, "dashboard/user_form.html", {"form": form, "instance": None})


@login_required
@superuser_required
def user_edit(request, pk):
    instance = get_object_or_404(get_user_model(), pk=pk)
    form = DashboardUserForm(request.POST or None, instance=instance)
    error = None
    if request.method == "POST" and form.is_valid():
        error = _lockout_error(request, instance, admin=form.cleaned_data["role"] == ROLE_ADMIN,
                               active=form.cleaned_data["is_active"])
        if error is None:
            user = form.save()
            audit.log(request, "user.update", target=user.get_username(),
                      details=f"{form.cleaned_data['role']}, {'active' if user.is_active else 'locked'}")
            messages.success(request, _("Saved."))
            return redirect("dashboard:users")
        instance.refresh_from_db()
    return render(request, "dashboard/user_form.html", {"form": form, "instance": instance, "error": error})


@login_required
@superuser_required
def user_password(request, pk):
    instance = get_object_or_404(get_user_model(), pk=pk)
    if instance.pk == request.user.pk:
        return redirect("dashboard:account")  # your own: with the current one, on your account page
    form = AdminSetPasswordForm(instance, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        must_change = form.cleaned_data["must_change_password"]
        AccountSecurity.objects.update_or_create(user=instance, defaults={"must_change_password": must_change})
        audit.log(request, "user.set_password", target=instance.get_username(),
                  details="must change at next login" if must_change else "")
        messages.success(request, _("Password for %(name)s changed.") % {"name": instance.get_username()})
        return redirect("dashboard:users")
    return render(request, "dashboard/user_password.html", {"form": form, "instance": instance})


@login_required
@superuser_required
@require_POST
def user_delete(request, pk):
    instance = get_object_or_404(get_user_model(), pk=pk)
    error = _lockout_error(request, instance, admin=False, active=False, deleting=True)
    if error:
        messages.error(request, error)
    else:
        name = instance.get_username()
        instance.delete()
        audit.log(request, "user.delete", target=name)
        messages.success(request, _("User %(name)s deleted.") % {"name": name})
    return redirect("dashboard:users")


# --- bookings: orders and payments, one by one --------------------------------------------

BOOKINGS_PER_PAGE = 50


def _bookings_url(kind, member_uid=None):
    url = f"{reverse('dashboard:bookings')}?kind={kind}"
    return f"{url}&member={member_uid}" if member_uid else url


@login_required
def bookings(request):
    """Every order or every payment, newest first, optionally for one member - the place
    to correct a mis-booked coffee or a mistyped payment, and to enter cash handed over in
    person. Replaces what the Django admin was used for."""
    kind = "payments" if request.GET.get("kind") == "payments" else "orders"
    member = None
    if request.GET.get("member", "").isdigit():
        member = Member.objects.filter(pk=int(request.GET["member"])).first()
    if kind == "orders":
        rows = Order.objects.select_related("member", "coffee_sort")
    else:
        rows = Payment.objects.select_related("member")
    if member:
        rows = rows.filter(member=member)
    page = Paginator(rows.order_by("-created_at", "-id"), BOOKINGS_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "dashboard/bookings.html", {
        "kind": kind,
        "member": member,
        "members": Member.objects.order_by("nickname", "uid"),
        "page": page,
        "balance": services.account_balance(member) if member else None,
    })


@login_required
def order_edit(request, pk):
    order = get_object_or_404(Order.objects.select_related("member"), pk=pk)
    form = OrderForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        order = form.save()
        audit.log(request, "order.update", target=order.member.display_name(),
                  details=f"#{order.pk}: {order.value} EUR, {order.machine_strokes} cup(s)")
        messages.success(request, _("Booking saved."))
        return redirect(_bookings_url("orders", order.member_id))
    return render(request, "dashboard/booking_form.html", {"form": form, "instance": order, "kind": "orders"})


@login_required
@require_POST
def order_delete(request, pk):
    order = get_object_or_404(Order.objects.select_related("member"), pk=pk)
    member = order.member
    details = f"#{order.pk}: {order.value} EUR, {order.created_at:%Y-%m-%d %H:%M}"
    order.delete()
    audit.log(request, "order.delete", target=member.display_name(), details=details)
    messages.success(request, _("Booking deleted."))
    return redirect(_bookings_url("orders", member.pk))


@login_required
def payment_add(request):
    initial = {}
    if request.GET.get("member", "").isdigit():
        initial["member"] = request.GET["member"]
    form = PaymentForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        payment = form.save()
        audit.log(request, "payment.create", target=payment.member.display_name(),
                  details=f"#{payment.pk}: {payment.value} EUR ({payment.method_name})")
        messages.success(request, _("Payment entered."))
        return redirect(_bookings_url("payments", payment.member_id))
    return render(request, "dashboard/booking_form.html", {"form": form, "instance": None, "kind": "payments"})


@login_required
def payment_edit(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("member"), pk=pk)
    form = PaymentForm(request.POST or None, instance=payment)
    if request.method == "POST" and form.is_valid():
        payment = form.save()
        audit.log(request, "payment.update", target=payment.member.display_name(),
                  details=f"#{payment.pk}: {payment.value} EUR ({payment.method_name})")
        messages.success(request, _("Payment saved."))
        return redirect(_bookings_url("payments", payment.member_id))
    return render(request, "dashboard/booking_form.html", {"form": form, "instance": payment, "kind": "payments"})


@login_required
@require_POST
def payment_delete(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("member"), pk=pk)
    member = payment.member
    details = f"#{payment.pk}: {payment.value} EUR ({payment.method_name}), {payment.created_at:%Y-%m-%d %H:%M}"
    payment.delete()
    audit.log(request, "payment.delete", target=member.display_name(), details=details)
    messages.success(request, _("Payment deleted."))
    return redirect(_bookings_url("payments", member.pk))


@login_required
@superuser_required
def password_policy(request):
    """The login rules. Stricter ones apply to every password set from now on - and to an
    existing one at its owner's next login, which then has to replace it (dashboard/security.py)."""
    policy = PasswordPolicy.current()
    before = {name: getattr(policy, name) for name in PasswordPolicyForm.Meta.fields}
    form = PasswordPolicyForm(request.POST or None, instance=policy)
    if request.method == "POST" and form.is_valid():
        policy = form.save()
        changes = ", ".join(f"{name} {before[name]} -> {getattr(policy, name)}"
                            for name in before if before[name] != getattr(policy, name))
        if changes:
            audit.log(request, "security.policy_update", details=changes)
        messages.success(request, _("Saved. Anyone whose password doesn't meet the new rules has to set a new one "
                                     "at their next login."))
        return redirect("dashboard:password_policy")
    return render(request, "dashboard/password_policy.html", {"form": form})
