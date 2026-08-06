from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from crm.models import JournalEntry


@override_settings(
    FINANCIAL_CORE_WRITES_ENABLED=True,
    FINANCIAL_CORE_REPORTING_ACTIVE=True,
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
        self.assertContains(response, 'data-finance-menu="production"')
        self.assertContains(response, "Canada Finance")
        self.assertContains(response, "Bangladesh Finance")
        self.assertContains(response, "Company Finance")
        self.assertContains(response, "Canada Daily Transactions")
        self.assertContains(response, "Bangladesh Daily Transactions")
        self.assertContains(response, "Canada Invoices")
        self.assertContains(response, "Accounts")
        self.assertContains(response, "Executive Finance Dashboard")
        self.assertContains(response, "Payment Audit")
        self.assertContains(response, "Bangladesh Dashboard")
        self.assertContains(response, "Canada Dashboard")
        self.assertContains(response, "KPI")
        self.assertContains(response, "Commercial Costing")
        self.assertNotContains(response, "Daily Operations")
        self.assertNotContains(response, ">Management<")
        self.assertNotContains(response, "Other Transactions")
        self.assertNotContains(response, "Bangladesh Accounting")
        self.assertNotContains(response, "Canada Accounting")
        self.assertNotContains(response, "Finance Live Readiness")
        self.assertNotContains(response, "Core Dashboard")

    def test_operations_center_allows_empty_historical_balances(self):
        response = self.response_for(self.ceo)
        self.assertContains(response, "Production")
        self.assertContains(response, "MASTER DATA REQUIRED FOR POSTING")
        self.assertContains(response, "0 configured", count=4)
        self.assertContains(response, "6 workflow values")
        self.assertContains(response, "3 supported")
        self.assertContains(
            response,
            "Historical balances may remain zero and can be entered later.",
        )
        self.assertNotContains(response, "Financial Core Posting")
        self.assertNotContains(response, "Financial Core Reporting")
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_obsolete_readiness_route_is_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("finance_live_readiness")

    def test_role_scoped_navigation_does_not_offer_restricted_workflows(self):
        production = self.response_for(self.production)
        self.assertContains(production, "Bangladesh Production Costs")
        self.assertContains(production, "Bangladesh Factory Daily Costs")
        self.assertNotContains(production, "Bangladesh Payroll")
        self.assertNotContains(production, "Bangladesh Bank and Cash")
        self.assertNotContains(production, "Bangladesh Dashboard")
        self.assertNotContains(production, "Bangladesh Reports")
        self.assertNotContains(production, "Canada Finance")

        hr = self.response_for(self.hr)
        self.assertContains(hr, "Bangladesh Payroll")
        self.assertNotContains(hr, "Bangladesh Production Costs")
        self.assertNotContains(hr, "Bangladesh Dashboard")
        self.assertNotContains(hr, "Bangladesh Reports")
        self.assertNotContains(hr, "Canada Finance")

        sales = self.response_for(self.sales)
        self.assertContains(sales, "Canada Finance")
        self.assertContains(sales, "Canada Daily Transactions")
        self.assertNotContains(sales, "Bangladesh Finance")
        self.assertNotContains(sales, "Canada Bank and Cash")

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
        ):
            with self.subTest(route=route):
                self.assertEqual(client.get(reverse(route)).status_code, 200)

    def test_duplicate_canada_invoice_route_redirects_to_the_locked_main_form(self):
        client = Client()
        client.force_login(self.super_admin)
        response = client.get(reverse("invoice_add_ca"))
        self.assertRedirects(
            response,
            f"{reverse('invoice_add')}?side=CA",
            fetch_redirect_response=False,
        )
        form_response = client.get(f"{reverse('invoice_add')}?side=CA")
        self.assertEqual(form_response.status_code, 200)
        form = form_response.context["form"]
        self.assertTrue(form.fields["invoice_market"].disabled)
        self.assertEqual(form.initial["currency"], "CAD")
