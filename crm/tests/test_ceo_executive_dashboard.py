from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AccountingEntry,
    CostingHeader,
    Customer,
    Invoice,
    Lead,
    Opportunity,
    ProductionOrder,
    Shipment,
)
from crm.services.ceo_executive import build_ceo_executive_context


class CEOExecutiveDashboardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.ceo = user_model.objects.create_superuser(
            username="ceo-executive",
            email="ceo@example.com",
            password="test-pass",
        )
        self.salesperson = user_model.objects.create_user(
            username="sales-owner",
            first_name="Sales",
            last_name="Owner",
        )
        self.manager = user_model.objects.create_user(
            username="production-owner",
            first_name="Hossain",
            last_name="Forhad",
        )
        self.manager.employee_profile.display_name = "Hossain"
        self.manager.employee_profile.aliases = ["Hossein", "Hossain", "Hossain Forhad"]
        self.manager.employee_profile.save()
        self.client.force_login(self.ceo)
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(
            account_brand="Executive Customer",
            contact_name="Executive Buyer",
            email="executive@example.com",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Executive Customer",
            contact_name="Executive Buyer",
            email="executive@example.com",
            assigned_to=self.salesperson,
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            product_type="Activewear",
            product_category="Hoodie",
        )
        self.order = ProductionOrder.objects.create(
            lead=self.lead,
            opportunity=self.opportunity,
            customer=self.customer,
            title="Executive production",
            qty_total=100,
            bulk_deadline=self.today - timedelta(days=1),
            assigned_production_manager=self.manager,
            operational_status="sewing",
        )
        Shipment.objects.create(
            order=self.order,
            customer=self.customer,
            ship_date=self.today + timedelta(days=5),
            status="planned",
        )
        self.costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            status="approved",
            quotation_number="QT-CEO-001",
            quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT,
            order_quantity=100,
            currency="CAD",
        )
        self.pending_costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            status="approved",
            quotation_number="QT-CEO-PENDING",
            quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT,
            order_quantity=100,
            currency="CAD",
        )
        for currency, amount, paid in [
            ("CAD", Decimal("100"), Decimal("25")),
            ("USD", Decimal("200"), Decimal("0")),
            ("BDT", Decimal("3000"), Decimal("0")),
        ]:
            Invoice.objects.create(
                invoice_number=f"INV-CEO-{currency}",
                order=self.order,
                costing_header=self.costing,
                customer=self.customer,
                issue_date=self.today,
                due_date=self.today + timedelta(days=15),
                currency=currency,
                total_amount=amount,
                paid_amount=paid,
                status="sent",
            )
        for currency, income, expense in [
            ("CAD", Decimal("1000"), Decimal("100")),
            ("USD", Decimal("500"), Decimal("50")),
            ("BDT", Decimal("10000"), Decimal("1000")),
        ]:
            self._entry(currency, "IN", "INCOME", income)
            self._entry(currency, "OUT", "EXPENSE", expense)

    def _entry(self, currency, direction, main_type, amount):
        rate_to_cad = {"CAD": Decimal("1"), "USD": Decimal("1.25"), "BDT": Decimal("100")}[currency]
        rate_to_bdt = {"CAD": Decimal("100"), "USD": Decimal("125"), "BDT": Decimal("1")}[currency]
        AccountingEntry.objects.create(
            date=self.today,
            side="BD" if currency == "BDT" else "CA",
            direction=direction,
            main_type=main_type,
            currency=currency,
            amount_original=amount,
            rate_to_cad=rate_to_cad,
            rate_to_bdt=rate_to_bdt,
            customer=self.customer,
        )

    def test_dashboard_shows_required_executive_metrics_and_currency_labels(self):
        response = self.client.get(reverse("ceo_dashboard"))

        self.assertEqual(response.status_code, 200)
        for label in [
            "Monthly Sales Value",
            "Outstanding AR",
            "Outstanding AP",
            "Current Cash",
            "Production Orders",
            "Late Production Orders",
            "Awaiting Payment Orders",
            "CRM Integrity Status",
            "Workflow Errors",
            "Legacy Test Records",
            "Pending CEO Approvals",
            "Accounting Revenue by Currency",
            "Open Pipeline",
            "Profit by Currency",
            "Top Customers",
            "Top Salesperson",
            "Top Production Manager",
            "Upcoming Shipments",
        ]:
            self.assertContains(response, label)
        self.assertContains(response, "Today&#x27;s Sales Value")
        self.assertContains(response, "CAD $100.00")
        self.assertContains(response, "USD $200.00")
        self.assertContains(response, "\u09F33,000.00")
        self.assertContains(response, "Executive Customer")
        self.assertContains(response, "Sales")
        self.assertContains(response, "Hossain")
        self.assertNotContains(response, "Hossain Forhad")
        self.assertContains(response, self.order.purchase_order_number)
        self.assertNotContains(response, self.order.internal_order_id)
        self.assertIn("ceo-dashboard;dur=", response.headers["Server-Timing"])

    def test_context_keeps_currency_exposure_separate(self):
        context = build_ceo_executive_context()

        sales = {row["currency"]: row["amount"] for row in context["monthly_sales"]}
        receivables = {row["currency"]: row["amount"] for row in context["outstanding_ar"]}
        payables = {row["currency"]: row["amount"] for row in context["outstanding_ap"]}
        profit = {row["currency"]: row["amount"] for row in context["profit_by_currency"]}
        self.assertEqual(sales, {"CAD": Decimal("100"), "USD": Decimal("200"), "BDT": Decimal("3000")})
        self.assertEqual(receivables, {"CAD": Decimal("75"), "USD": Decimal("200"), "BDT": Decimal("3000")})
        self.assertEqual(payables, {"CAD": Decimal("100"), "USD": Decimal("50"), "BDT": Decimal("1000")})
        self.assertEqual(profit, {"CAD": Decimal("900"), "USD": Decimal("450"), "BDT": Decimal("9000")})
        self.assertEqual(context["production_total"], 1)
        self.assertEqual(context["late_production_orders"], 1)
        self.assertEqual(context["pending_ceo_approvals"], 1)

    def test_context_reports_awaiting_payment_orders_separately(self):
        opportunity = Opportunity.objects.create(
            customer=self.customer,
            product_type="Activewear",
            product_category="Hoodie",
            stage="Proposal",
            order_currency="CAD",
            order_value_usd=Decimal("750"),
        )
        Invoice.objects.create(
            invoice_number="INV-CEO-AWAITING",
            opportunity=opportunity,
            customer=self.customer,
            issue_date=self.today,
            due_date=self.today + timedelta(days=7),
            currency="CAD",
            total_amount=Decimal("750"),
            paid_amount=Decimal("250"),
            status="partial",
        )
        opportunity.refresh_from_db()

        context = build_ceo_executive_context()

        self.assertEqual(opportunity.stage, "Awaiting Payment")
        self.assertEqual(context["awaiting_payment_count"], 1)
        self.assertEqual(context["awaiting_payment_customer_count"], 1)
        self.assertEqual(context["awaiting_payment_rows"][0]["amount"], Decimal("500"))
        self.assertIn("workflow_errors", context)
        self.assertIn("broken_opportunities", context)
        self.assertIn("broken_production_links", context)
        self.assertIn("broken_invoice_links", context)
        self.assertIn("legacy_test_records", context)
        self.assertIn("crm_integrity_status", context)

    def test_context_builder_has_bounded_query_count(self):
        with CaptureQueriesContext(connection) as captured:
            build_ceo_executive_context()

        self.assertLessEqual(len(captured), 10)

    def test_crm_integrity_csv_export_is_ceo_only_and_filterable(self):
        Invoice.objects.create(
            invoice_number="INV-CEO-INTEGRITY",
            customer=self.customer,
            issue_date=self.today,
            due_date=self.today + timedelta(days=7),
            currency="CAD",
            total_amount=Decimal("750"),
            paid_amount=Decimal("250"),
            status="partial",
        )

        response = self.client.get(reverse("crm_integrity_export_csv"), {"filter": "broken"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn("opportunity_id", content)
        self.assertIn("MANUAL_REVIEW", content)
        self.assertIn("invoice_link_missing", content)

    def test_detailed_operations_dashboard_remains_available(self):
        response = self.client.get(reverse("ceo_operations_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CEO Dashboard")
