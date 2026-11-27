"""The coffee machine's picture (coffee/machine_image.py): the drawing of its JURA product line, or the photo an
admin uploads on dashboard -> Coffee machine, served to every screen."""
import importlib.util
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from coffee import machine_image
from coffee.models import AuditLogEntry
from dashboard.models import CoffeeMachine

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64


class FamilyTests(TestCase):
    def test_names_as_py_jura_gives_them(self):
        for name, family in [("E6 (EB)", "e"), ("JURA E6 (EB)", "e"), ("E60 RFID Aroma G3", "e"), ("E800", "e"),
                             ("ENA 8 60Hz", "ena"), ("JURA WE8", "we"), ("WE50 JDE", "we"), ("W8", "w"),
                             ("GIGA 6", "giga"), ("GIGA X8c Professional", "giga_x"), ("GIGA W10", "giga_x"),
                             ("X10c", "x"), ("S808", "s"), ("J10 twin", "j"), ("Z10", "z"), ("D601", "d"),
                             ("C9", "c"), ("", "generic"), ("Simulator", "generic")]:
            self.assertEqual(machine_image.family(name), family, name)

    def test_every_family_is_drawn(self):
        static = Path(settings.BASE_DIR) / "static" / "machines"
        drawn = {path.stem for path in static.glob("*.svg")}
        self.assertEqual({key for key, _pattern in machine_image.FAMILIES} | {"generic"}, drawn)
        script = Path(settings.BASE_DIR) / "scripts" / "make_machine_images.py"
        spec = importlib.util.spec_from_file_location("make_machine_images", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(set(module.FAMILIES), drawn)
        for key, params in module.FAMILIES.items():  # the committed files are what the script draws
            self.assertEqual((static / f"{key}.svg").read_text(), module.draw(**params), key)

    def test_sniff(self):
        self.assertEqual(machine_image.sniff(PNG), "image/png")
        self.assertEqual(machine_image.sniff(JPEG), "image/jpeg")
        self.assertEqual(machine_image.sniff(b"RIFF\0\0\0\0WEBPVP8 "), "image/webp")
        self.assertIsNone(machine_image.sniff(b"<svg onload=alert(1)>"))


class PhotoTests(TestCase):
    def setUp(self):
        settings_ = CoffeeMachine.current()
        settings_.driver, settings_.address, settings_.name = "jura_ble", "F4:57:52:42:0C:04", "JURA Z10"
        settings_.save()
        User.objects.create_superuser(username="root", password="pw")
        self.client = Client()
        self.client.login(username="root", password="pw")
        self.url = reverse("dashboard:device")

    def upload(self, data, name="m.png"):
        return self.client.post(self.url, {"form": "photo", "photo": SimpleUploadedFile(name, data)}, follow=True)

    def test_the_drawing_until_a_photo_comes(self):
        self.assertEqual(machine_image.url(), "/static/machines/z.svg")
        self.assertContains(self.client.get(self.url), 'class="machine-image" src="/static/machines/z.svg"')

    def test_upload_serve_and_remove(self):
        self.assertContains(self.upload(PNG), "the photo now shows")
        settings_ = CoffeeMachine.current()
        url = machine_image.url()
        self.assertEqual(url, f"/machine-photo/{machine_image.digest(PNG)}.png")
        self.assertEqual(AuditLogEntry.objects.get(action="coffee_machine.photo").outcome, "success")
        response = Client().get(url)  # no login: the kiosk and the phones show it too
        self.assertEqual((response.status_code, response["Content-Type"], response.content), (200, "image/png", PNG))
        self.assertIn("immutable", response["Cache-Control"])
        self.assertEqual(Client().get(url.replace(".png", ".jpg")).status_code, 404)
        self.assertEqual(Client().get("/machine-photo/0000000000000000.png").status_code, 404)
        self.assertContains(self.client.get(self.url), f'src="{url}"')
        self.assertTrue(settings_.photo_digest)
        self.client.post(self.url, {"form": "photo_remove"})
        self.assertEqual(machine_image.url(), "/static/machines/z.svg")
        self.assertEqual(Client().get(url).status_code, 404)
        self.assertTrue(AuditLogEntry.objects.filter(action="coffee_machine.photo_remove").exists())

    def test_only_photos_and_not_too_big(self):
        self.assertContains(self.upload(b"<svg/>", "m.svg"), "That is no photo")
        self.assertContains(self.upload(JPEG + b"\0" * machine_image.MAX_BYTES, "m.jpg"), "That is no photo")
        self.assertEqual(CoffeeMachine.current().photo_digest, "")

    def test_only_admins_upload(self):
        User.objects.create_user(username="m", password="pw")
        self.client.login(username="m", password="pw")
        self.client.post(self.url, {"form": "photo", "photo": SimpleUploadedFile("m.png", PNG)})
        self.assertEqual(CoffeeMachine.current().photo_digest, "")
