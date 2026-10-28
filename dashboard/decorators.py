from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from . import audit


def superuser_required(view):
    """Admin-only pages. A manager who gets here anyway (an old link, a guessed URL) lands
    on the overview - and the attempt is logged."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            audit.log(request, "access.denied", target=request.path, outcome="failure")
            messages.error(request, _("Only admins may open that page."))
            return redirect("dashboard:index")
        return view(request, *args, **kwargs)
    return wrapped
