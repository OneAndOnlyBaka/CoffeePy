from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse


class SystemDashboardTestCase(TestCase):
    def setUp(self):
        self.client = Client()

    def test_requires_login(self):
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_non_superuser_is_rejected(self):
        User.objects.create_user(username="staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertEqual(response.status_code, 302)

    @override_settings(PUBLIC_HOST="192.168.4.1")
    def test_superuser_sees_cpu_memory_disk_and_public_host(self):
        User.objects.create_superuser(username="root", password="pw")
        self.client.login(username="root", password="pw")
        response = self.client.get(reverse("dashboard:system_dashboard"))
        self.assertContains(response, "CPU")
        self.assertContains(response, "Memory")
        self.assertContains(response, "Disk")
        self.assertContains(response, "192.168.4.1")
