from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from coffee.models import AuditLogEntry

STRONG = "kaffee-ist-fertig-42"


class DashboardUsersTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="boss", password="pw")
        self.client = Client()
        self.client.login(username="boss", password="pw")

    def test_managers_cannot_open_the_user_pages(self):
        User.objects.create_user(username="helper", password="pw")
        client = Client()
        client.login(username="helper", password="pw")
        self.assertEqual(client.get(reverse("dashboard:users")).status_code, 302)
        self.assertEqual(client.post(reverse("dashboard:user_delete", args=[self.admin.pk])).status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_add_a_manager(self):
        response = self.client.post(reverse("dashboard:user_add"), {
            "username": "helper", "role": "manager", "password1": STRONG, "password2": STRONG,
        })
        self.assertRedirects(response, reverse("dashboard:users"))
        user = User.objects.get(username="helper")
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(STRONG))
        self.assertTrue(AuditLogEntry.objects.filter(action="user.create", target="helper").exists())

    def test_add_an_admin(self):
        self.client.post(reverse("dashboard:user_add"), {
            "username": "second", "role": "admin", "password1": STRONG, "password2": STRONG,
        })
        self.assertTrue(User.objects.get(username="second").is_superuser)

    def test_add_rejects_mismatched_or_weak_passwords(self):
        response = self.client.post(reverse("dashboard:user_add"), {
            "username": "helper", "role": "manager", "password1": STRONG, "password2": STRONG + "x",
        })
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("dashboard:user_add"), {
            "username": "helper", "role": "manager", "password1": "1234", "password2": "1234",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="helper").exists())

    def test_edit_role_and_lock(self):
        helper = User.objects.create_user(username="helper", password="pw")
        self.client.post(reverse("dashboard:user_edit", args=[helper.pk]),
                         {"username": "helper", "role": "admin", "is_active": "on"})
        helper.refresh_from_db()
        self.assertTrue(helper.is_superuser)
        self.client.post(reverse("dashboard:user_edit", args=[helper.pk]), {"username": "helper", "role": "manager"})
        helper.refresh_from_db()
        self.assertFalse(helper.is_superuser)
        self.assertFalse(helper.is_active)

    def test_cannot_demote_lock_or_delete_yourself(self):
        # Which also means there is always an admin left who can log in.
        for data in ({"username": "boss", "role": "manager", "is_active": "on"}, {"username": "boss", "role": "admin"}):
            response = self.client.post(reverse("dashboard:user_edit", args=[self.admin.pk]), data)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "your own")
        self.client.post(reverse("dashboard:user_delete", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)
        self.assertTrue(self.admin.is_active)

    def test_set_someone_elses_password(self):
        helper = User.objects.create_user(username="helper", password="pw")
        self.client.post(reverse("dashboard:user_password", args=[helper.pk]),
                         {"new_password1": STRONG, "new_password2": STRONG})
        helper.refresh_from_db()
        self.assertTrue(helper.check_password(STRONG))
        self.assertTrue(AuditLogEntry.objects.filter(action="user.set_password", target="helper").exists())


class AccountTestCase(TestCase):
    def test_everyone_can_change_their_own_password_and_stays_logged_in(self):
        User.objects.create_user(username="helper", password="old-pw")
        client = Client()
        client.login(username="helper", password="old-pw")
        response = client.post(reverse("dashboard:account"), {
            "old_password": "old-pw", "new_password1": STRONG, "new_password2": STRONG,
        })
        self.assertRedirects(response, reverse("dashboard:account"))
        self.assertTrue(User.objects.get(username="helper").check_password(STRONG))
        self.assertEqual(client.get(reverse("dashboard:index")).status_code, 200)

    def test_wrong_old_password_changes_nothing(self):
        User.objects.create_user(username="helper", password="old-pw")
        client = Client()
        client.login(username="helper", password="old-pw")
        client.post(reverse("dashboard:account"), {
            "old_password": "nope", "new_password1": STRONG, "new_password2": STRONG,
        })
        self.assertTrue(User.objects.get(username="helper").check_password("old-pw"))


class NavigationTestCase(TestCase):
    def test_django_admin_is_gone(self):
        User.objects.create_superuser(username="boss", password="pw")
        client = Client()
        client.login(username="boss", password="pw")
        self.assertEqual(client.get("/admin/").status_code, 404)
        self.assertNotContains(client.get(reverse("dashboard:index")), 'href="/admin/')

    def test_managers_see_no_system_pages_in_the_navigation(self):
        User.objects.create_user(username="helper", password="pw")
        client = Client()
        client.login(username="helper", password="pw")
        response = client.get(reverse("dashboard:index"))
        self.assertContains(response, reverse("dashboard:bookings"))
        self.assertNotContains(response, reverse("dashboard:users"))
        self.assertNotContains(response, reverse("dashboard:backup"))

    def test_admins_see_them(self):
        User.objects.create_superuser(username="boss", password="pw")
        client = Client()
        client.login(username="boss", password="pw")
        response = client.get(reverse("dashboard:index"))
        self.assertContains(response, reverse("dashboard:users"))
        self.assertContains(response, reverse("dashboard:reset_database"))
