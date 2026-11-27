from functools import wraps

from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from coffee import privacy
from coffee.models import Member, MemberDevice

# ~6 months - a portal session rolls forward by this much on every authenticated
# request (see the decorator below), so it only actually expires after 6 months of the
# member simply not opening the page at all, not from a fixed link expiry.
ROLLING_SESSION_SECONDS = 60 * 60 * 24 * 183


def portal_login_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        uid = request.session.get("portal_member_uid")
        member = Member.objects.filter(pk=uid).first() if uid is not None else None
        if member is None or member.is_departed:
            request.session.flush()
            return redirect("portal:link_invalid")

        request.member = member
        request.session.set_expiry(ROLLING_SESSION_SECONDS)
        MemberDevice.objects.filter(session_key=request.session.session_key).update(
            last_seen_at=timezone.now()
        )
        return view(request, *args, **kwargs)

    return wrapper


def portal_privacy_required(view):
    """Inside portal_login_required: whatever books something - a payment, a purchase, a new card - only
    once the member has confirmed the privacy notice (coffee/privacy.py); before, the notice."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not privacy.confirmed(request.member):
            return redirect(reverse("portal:privacy") + "?next=" + request.path)
        return view(request, *args, **kwargs)

    return wrapper
