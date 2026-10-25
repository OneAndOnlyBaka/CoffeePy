"""Which of the two languages (settings.LANGUAGES) a request is answered in.

Django's own LocaleMiddleware would fall back to the browser's Accept-Language - a phone set
to English would then ignore the default chosen when the device was set up. Here it is only
ever the device's own choice (the language cookie, set by django.views.i18n.set_language
from the kiosk's language button or the switch in dashboard and portal) or that default.
"""
from django.conf import settings
from django.utils import translation


def device_language(request) -> str:
    chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    return chosen if chosen in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE


class DeviceLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = device_language(request)
        translation.activate(language)
        request.LANGUAGE_CODE = language
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", language)
        return response
