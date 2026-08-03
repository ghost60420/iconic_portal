from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from crm.models import JournalEntry


@override_settings(
    FINANCIAL_CORE_WRITES_ENABLED=False,
    FINANCIAL_CORE_REPORTING_ACTIVE=False,
)
class FinanceLiveVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ceo = cls.user("visibility-ceo", "CEO", ca=True, bd=True)
        cls.super_admin = get_user_model().objects.create_superuser(
            username="visibility-super", email="super@example.com", password="test-pass"
        )
        cls.finance = cls.user("visibility-finance", "Finance", ca=True)
        cls.accounts = cls.user("visibility-accounts", "Accounts", ca=True)
        cls.director = cls.user("visibility-director", "Director", ca=True)
        cls.manager = cls.user("visibility-manager", "Manager", ca=True)
        cls.production = cls.user("visibility-production", "Production", side="BD", bd=True)
        cls.sales = cls.user("visibility-sales", "Sales", side="CA")
        cls.hr = cls.user("visibility-hr", "HR", side="BD", bd=True)
        cls.staff = cls.user("visibility-staff", "Staff", side="CA")

    @classmethod
    def user(cls, username, role, *, side="CA", ca=False, bd=False):
        user = get_user_model().objects.create_user(username=username, password="test-pass")
        Group.objects.get_or_create(name=role)[0].user_set.add(user)
        access = user.access
        access.role = side
        access.can_accounting_ca = ca
        access.can_accounting_bd = bd
        access.save()
        return user

    def response_for(self, user, route="finance_operations_center"):
        client = Client()
        client.force_login(user)
        return client.get(reverse(route))

    def test_ceo_menu_exposes_scoped_finance_operations_groups(self):
        response = self.response_for(self.ceo)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-finance-operations-menu="ready"')
        self.assertContains(response, "Finance Operations Center")
        self.assertContains(response, "Daily Operations")
        self.assertContains(response, "Bank and Cash")
        self.assertContains(response, "Management")
        self.assertContains(response, "Other Transactions")
        self.assertContains(response, "Finance Live Readiness")

    def test_operations_center_shows_live_flags_and_empty_setup(self):
        response = self.response_for(self.ceo)
        self.assertContains(response, "LIVE FOR WORKFLOW TESTING")
        self.assertContains(response, "Financial Core Posting")
        self.assertContains(response, "Financial Core Reporting")
        self.assertContains(response, "FINANCE SETUP REQUIRED")
        self.assertContains(response, "0 configured", count=4)
        self.assertContains(response, "6 workflow values")
        self.assertContains(response, "3 supported")
        self.assertContains(
            response,
            "Transactions can be entered and reviewed, but they will not post to the new General Ledger",
        )
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_readiness_is_restricted_to_ceo_and_super_admin(self):
        for user in (self.ceo, self.super_admin):
            with self.subTest(user=user.username):
                response = self.response_for(user, "finance_live_readiness")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Finance Live Readiness")
                self.assertContains(response, "Finance routes present")
                self.assertContains(response, "Financial Core writes")
        for user in (self.finance, self.accounts, self.director, self.manager, self.production, self.sales, self.hr):
            with self.subTest(user=user.username):
                self.assertEqual(self.response_for(user, "finance_live_readiness").status_code, 403)

    def test_role_scoped_navigation_does_not_offer_restricted_workflows(self):
        production = self.response_for(self.production)
        self.assertContains(production, "Production Costs")
        self.assertContains(production, "Factory Daily Costs")
        self.assertNotContains(production, ">Payroll<")
        self.assertNotContains(production, "Bank and Cash")

        hr = self.response_for(self.hr)
        self.assertContains(hr, ">Payroll<")
        self.assertNotContains(hr, "Production Costs")
        self.assertNotContains(hr, "Owner Transactions")

        sales = self.response_for(self.sales)
        self.assertContains(sales, "Finance Operations Center")
        self.assertNotContains(sales, "Daily Operations")
        self.assertNotContains(sales, "Bank and Cash")

    def test_permission_roles_can_open_center_and_staff_cannot(self):
        allowed = (
            self.ceo, self.super_admin, self.finance, self.accounts, self.director,
            self.manager, self.production, self.sales, self.hr,
        )
        for user in allowed:
            with self.subTest(user=user.username):
                self.assertEqual(self.response_for(user).status_code, 200)
        self.assertEqual(self.response_for(self.staff).status_code, 403)

    def test_all_named_visibility_routes_resolve_and_open_for_ceo(self):
        client = Client()
        client.force_login(self.ceo)
        for route in (
            "finance_operations_center",
            "finance_approval_center",
            "finance_today_activity",
            "finance_live_readiness",
        ):
            with self.subTest(route=route):
                self.assertEqual(client.get(reverse(route)).status_code, 200)
