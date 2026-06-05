from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models_access import UserAccess


ACCESS_FLAGS = [
    "can_leads",
    "can_opportunities",
    "can_customers",
    "can_inventory",
    "can_production",
    "can_shipping",
    "can_ai",
    "can_calendar",
    "can_marketing",
    "can_whatsapp",
    "can_costing",
    "can_view_internal_costing",
    "can_costing_approve",
    "can_view_ceo_tools",
    "can_accounting_bd",
    "can_accounting_ca",
    "can_library",
]


class AccountingRBACTests(TestCase):
    def create_user_with_access(self, username, **enabled_flags):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="test-pass",
        )
        access = UserAccess.objects.get(user=user)
        for flag in ACCESS_FLAGS:
            setattr(access, flag, False)
        for flag, value in enabled_flags.items():
            setattr(access, flag, value)
        access.save()
        return user

    def test_bd_accounting_access_uses_useraccess_not_legacy_groups(self):
        user = self.create_user_with_access("bd-accounting", can_accounting_bd=True)
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("accounting_entry_add_bd")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounting_bd_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounting_entry_list")).status_code, 200)

        home_response = self.client.get(reverse("accounting_home"))
        self.assertEqual(home_response.status_code, 302)
        self.assertEqual(home_response.url, reverse("accounting_bd_daily"))

    def test_admin_accounting_access_still_passes(self):
        admin = get_user_model().objects.create_superuser(
            username="accounting-admin",
            email="accounting-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(admin)

        self.assertEqual(self.client.get(reverse("accounting_entry_add_bd")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounting_bd_dashboard")).status_code, 200)

    def test_non_accounting_users_are_blocked(self):
        users = [
            self.create_user_with_access("sales-only", can_leads=True, can_opportunities=True),
            self.create_user_with_access("production-only", can_production=True),
            self.create_user_with_access("restricted-user"),
        ]

        for user in users:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("accounting_entry_add_bd")).status_code, 403)
                self.assertEqual(self.client.get(reverse("accounting_bd_dashboard")).status_code, 403)
                self.assertEqual(self.client.get(reverse("accounting_entry_list")).status_code, 403)
                self.assertEqual(self.client.get(reverse("accounting_home")).status_code, 403)
