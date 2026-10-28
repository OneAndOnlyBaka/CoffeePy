from datetime import timedelta

from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coffee.models import Member, MemberDevice, MemberLinkToken


class DeviceManagementTestCase(TestCase):
    def setUp(self):
        self.alice = Member.objects.create(uid=42, nickname="Alice")

        self.phone = Client()
        self._link(self.phone)
        self.laptop = Client()
        self._link(self.laptop)

    def _link(self, client):
        token = MemberLinkToken.objects.create(member=self.alice, expires_at=timezone.now() + timedelta(minutes=5))
        client.get(reverse("portal:link", args=[token.token]))

    def test_devices_list_shows_both_linked_devices(self):
        response = self.phone.get(reverse("portal:devices"))
        self.assertEqual(len(response.context["devices"]), 2)

    def test_revoking_a_device_logs_it_out_but_not_the_others(self):
        other_device = MemberDevice.objects.exclude(session_key=self.phone.session.session_key).get()

        self.phone.post(reverse("portal:revoke_device", args=[other_device.pk]))

        self.assertFalse(MemberDevice.objects.filter(pk=other_device.pk).exists())
        self.assertFalse(Session.objects.filter(pk=other_device.session_key).exists())
        # revoking laptop's session/device didn't touch the phone's own session
        response = self.phone.get(reverse("portal:index"))
        self.assertEqual(response.status_code, 200)

    def test_cannot_revoke_another_members_device(self):
        bob = Member.objects.create(uid=99, nickname="Bob")
        bob_client = Client()
        token = MemberLinkToken.objects.create(member=bob, expires_at=timezone.now() + timedelta(minutes=5))
        bob_client.get(reverse("portal:link", args=[token.token]))
        bob_device = MemberDevice.objects.get(member=bob)

        response = self.phone.post(reverse("portal:revoke_device", args=[bob_device.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(MemberDevice.objects.filter(pk=bob_device.pk).exists())
