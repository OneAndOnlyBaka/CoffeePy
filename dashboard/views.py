import csv
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import connection, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_GET, require_POST

from coffee import purge, services
from coffee.models import (
    AltUidCapture,
    AuditLogEntry,
    CoffeeSort,
    Member,
    MemberCard,
    MemberDevice,
    NfcReaderStatus,
    Order,
    Payment,
    PaymentMethod,
)
from coffee.network import public_host

from . import audit, import_jobs, system_clock, system_stats
from .forms import CoffeeSortForm, MemberForm, PaymentMethodForm

superuser_required = user_passes_test(lambda u: u.is_superuser)


@login_required
def index(request):
    reader_status = NfcReaderStatus.objects.filter(pk=1).first()
    return render(
        request,
        "dashboard/index.html",
        {"reader_status": reader_status, "public_host": public_host()},
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
    return render(
        request,
        "dashboard/members.html",
        {
            "members": Member.objects.select_related("favourite_coffee")
            .prefetch_related("alt_cards")
            .order_by("nickname", "uid")
        },
    )


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
        return HttpResponse(
            '<p class="msg error">Timed out - no card was tapped within '
            f'{ALT_UID_CAPTURE_SECONDS} seconds.</p>'
            f'<p><a href="{reverse("dashboard:member_alt_uid_start", args=[uid])}">Try again</a></p>'
        )

    seconds_left = int((capture.expires_at - timezone.now()).total_seconds())
    error_html = f'<p class="msg error">{escape(capture.last_error)}</p>' if capture.last_error else ""
    return HttpResponse(f"{error_html}<p>Waiting for a card to be tapped on the kiosk reader… ({seconds_left}s left)</p>")


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
            error = "Pick both accounts."
        elif keep_uid == remove_uid:
            error = "Pick two different accounts."
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
    for order in Order.objects.select_related("member", "coffee_sort").order_by("created_at"):
        writer.writerow([
            order.member_id,
            order.member.nickname,
            order.coffee_sort.name if order.coffee_sort else "",
            order.value,
            order.machine_strokes,
            order.created_at.isoformat(),
            order.info,
        ])
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


@login_required
@superuser_required
def system_dashboard(request):
    return render(
        request,
        "dashboard/system_dashboard.html",
        {"stats": system_stats.gather(), "public_host": public_host()},
    )


@login_required
@superuser_required
def system_time(request):
    error = None
    if request.method == "POST":
        dt_str = request.POST.get("datetime")
        try:
            new_dt = datetime.fromisoformat(dt_str)
            system_clock.set_system_datetime(new_dt)
        except Exception as exc:  # noqa: BLE001 - surfaced to the admin, not swallowed
            error = str(exc)
        else:
            audit.log(request, "system_time.set", details=new_dt.isoformat())
            return redirect("dashboard:system_time")
    return render(request, "dashboard/system_time.html", {"now": timezone.localtime(), "error": error})


@login_required
@superuser_required
def backup(request):
    backup_path = os.environ.get("BACKUP_PATH")
    message = None
    if request.method == "POST":
        try:
            call_command("backup_db", once=True)
            message = "Backup completed."
            audit.log(request, "backup.trigger")
        except Exception as exc:  # noqa: BLE001
            message = f"Backup failed: {exc}"

    files = []
    if backup_path and Path(backup_path).is_dir():
        files = sorted(
            (p for p in Path(backup_path).glob("coffee_backup_*.sql")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    return render(
        request,
        "dashboard/backup.html",
        {"message": message, "files": files, "backup_path": backup_path},
    )


@login_required
@superuser_required
def backup_download(request, filename):
    backup_path = os.environ.get("BACKUP_PATH")
    if not backup_path:
        raise Http404
    target = (Path(backup_path) / filename).resolve()
    if target.parent != Path(backup_path).resolve() or not target.is_file():
        raise Http404
    return HttpResponse(
        target.read_bytes(),
        content_type="application/sql",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


@login_required
@superuser_required
def update_upload(request):
    message = None
    if request.method == "POST":
        upload = request.FILES.get("bundle")
        if not upload or not upload.name.endswith((".tar", ".tar.gz")):
            message = "Please upload a .tar or .tar.gz image bundle."
        else:
            settings.UPDATE_INCOMING_DIR.mkdir(parents=True, exist_ok=True)
            tmp_name = settings.UPDATE_INCOMING_DIR / f".uploading-{uuid.uuid4().hex}"
            with open(tmp_name, "wb") as f:
                for chunk in upload.chunks():
                    f.write(chunk)
            final_name = settings.UPDATE_INCOMING_DIR / upload.name
            os.replace(tmp_name, final_name)  # atomic rename so the watcher never sees a partial file
            message = f"Uploaded {upload.name}. The update watcher will pick it up shortly."
            audit.log(request, "update.upload", target=upload.name)
    return render(request, "dashboard/update.html", {"message": message})


@login_required
@superuser_required
def import_legacy_db(request):
    """GET renders the upload page; POST (sent by its JavaScript) stores the upload and
    kicks off the import in the background, so the page can show a spinner and progress
    instead of a browser that hangs for minutes on a big database."""
    if request.method == "POST":
        upload = request.FILES.get("dump")
        if not upload:
            return JsonResponse({"error": "Please choose a file to upload."}, status=400)
        if import_jobs.is_running():
            return JsonResponse({"error": "An import is already running."}, status=409)

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
        return JsonResponse(import_jobs.read_status())
    return render(request, "dashboard/import.html", {"status": import_jobs.read_status()})


@login_required
@superuser_required
@require_GET
def import_status(request):
    return JsonResponse(import_jobs.read_status())


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
            error = f"Type {RESET_CONFIRMATION} to confirm - nothing was deleted."
        elif import_jobs.is_running():
            error = "An import is running - wait for it to finish."
        else:
            summary = purge.describe(purge.purge(purge.FULL_SCOPE))
            # Logged after the wipe on purpose: the audit log is part of what gets
            # deleted, so this entry is what's left to say the box was reset and by whom.
            audit.log(request, "database.reset", details=summary)
            message = f"Database reset - deleted {summary}."
    return render(
        request,
        "dashboard/reset.html",
        {
            "message": message,
            "error": error,
            "confirmation": RESET_CONFIRMATION,
            "counts": [
                ("Members", Member.objects.count()),
                ("Orders", Order.objects.count()),
                ("Payments", Payment.objects.count()),
                ("Coffee sorts", CoffeeSort.objects.count()),
                ("Payment methods", PaymentMethod.objects.count()),
                ("Audit log entries", AuditLogEntry.objects.count()),
            ],
        },
    )


@login_required
@superuser_required
def audit_log(request):
    entries = AuditLogEntry.objects.all()[:500]
    return render(request, "dashboard/audit_log.html", {"entries": entries})


@login_required
@superuser_required
@require_GET
def update_status(request):
    status_dir = settings.UPDATE_STATUS_DIR
    entries = []
    if status_dir.is_dir():
        for p in sorted(status_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            entries.append({"name": p.name, "text": p.read_text(errors="replace")})
    return JsonResponse({"entries": entries})


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
