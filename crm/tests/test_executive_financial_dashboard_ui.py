from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(FINANCIAL_CORE_REPORTING_ACTIVE=False)
class ExecutiveFinancialDashboardUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="executive-dashboard-ui-admin",
            email="executive-dashboard-ui@example.com",
            password="test-pass",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def template_source(self):
        return (
            Path(settings.BASE_DIR)
            / "crm"
            / "templates"
            / "crm"
            / "accounting_executive_dashboard.html"
        ).read_text()

    def test_dashboard_renders_new_ui_sections_and_actions(self):
        response = self.client.get(reverse("executive_financial_dashboard"))

        self.assertEqual(response.status_code, 200)
        template = self.template_source()
        for label in [
            "Money Waiting to Be Collected",
            "Monthly Cash Movement",
            "Canada vs Bangladesh Summary",
            "Today&#39;s Action Items",
            "Financial Pipeline",
            "Top Customers",
            "Top Suppliers",
            "Currency Exposure",
        ]:
            with self.subTest(label=label):
                self.assertIn(label, template)

        for label, url_name in [
            ("New Quotation", "cost_sheet_create"),
            ("New Invoice", "invoice_add"),
            ("Record Payment", "accounts_receivable_dashboard"),
            ("Add Expense", "accounts_payable_dashboard"),
            ("Add Supplier Bill", "accounts_payable_dashboard"),
            ("View Reports", "accounting_reports"),
        ]:
            with self.subTest(label=label):
                self.assertContains(response, label)
                self.assertContains(response, reverse(url_name))

    def test_requested_table_columns_are_present(self):
        template = self.template_source()

        for label in [
            "Customer",
            "Sales",
            "Received",
            "Outstanding",
            "Profit",
            "Last Order",
            "Supplier",
            "Total Purchased",
            "Paid",
            "Last Purchase",
            "Currency",
            "CAD Equivalent",
        ]:
            with self.subTest(label=label):
                self.assertIn(label, template)

    def test_template_disallows_horizontal_table_scrolling(self):
        template = self.template_source()

        self.assertIn(".exec-table{ width:100%; max-width:100%;", template)
        self.assertIn("table-layout:fixed", template)
        self.assertIn(".exec-table-wrap{ width:100%; max-width:100%;", template)
        self.assertIn("overflow:visible", template)
        self.assertIn("@media (max-width:900px)", template)
        self.assertIn(".exec-responsive-table", template)
        self.assertNotIn("overflow-x:auto", template)
        self.assertNotIn("min-width:620px", template)

    def test_latest_finance_controls_and_bindings_remain_available(self):
        response = self.client.get(reverse("executive_financial_dashboard"))

        for url_name in [
            "profit_loss_dashboard",
            "balance_sheet_dashboard",
            "cash_flow_dashboard",
            "accounts_receivable_dashboard",
            "accounts_payable_dashboard",
            "kpi_scorecard_dashboard",
        ]:
            with self.subTest(url_name=url_name):
                self.assertContains(response, reverse(url_name))

        template = self.template_source()
        for binding in [
            "can_include_archived",
            "conversion_warning_count",
            "currency_exposure_rows",
            "canada_export_revenue_rows",
            "local_sewing_summary",
            "finance_money",
        ]:
            with self.subTest(binding=binding):
                self.assertIn(binding, template)
