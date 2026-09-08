from django.test import Client, TestCase
from django.urls import reverse


class DoomViewTestCase(TestCase):
    def test_renders_without_needing_a_session(self):
        response = Client().get(reverse("kiosk:doom"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Exit to Kiosk")
        self.assertContains(response, 'id="dos"')
