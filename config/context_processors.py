from django.conf import settings


def version(request):
    """The running version for every page footer - dashboard navigation, phone portal and the
    kiosk's admin QR codes - so whoever reports a problem can say which build it was."""
    return {"coffeepy_version": settings.COFFEEPY_VERSION}
