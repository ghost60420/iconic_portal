from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Customer, Invoice, Lead, ProductionOrder
from crm.production_forms import ProductionOrderForm
from crm.services.local_sewing import (
    calculate_local_sewing,
    is_bangladesh_local_sewing,
    summarize_canada_export_orders,
    summarize_local_sewing_orders,
    summarize_production_business_models,
)


class LocalSewingCalculationTests(TestCase):
    def create_order(self, **overrides):
        values = {
            "title": "Bangladesh local sewing",
            "factory_location": "bd",
            "order_type": "sewing_charge",
            "qty_total": 100,
            "qty_reject": 3,
            "completed_quantity": 80,
            "sewing_charge_per_piece_bdt": Decimal("50.00"),
            "sewing_cost_per_piece_bdt": Decimal("30.00"),
            "extra_local_cost_bdt": Decimal("500.00"),
        }
        values.update(overrides)
        return ProductionOrder.objects.create(**values)

    def test_calculates_revenue_cost_profit_margin_and_output(self):
        order = self.create_order()
        stage = order.stages.get(stage_key="sewing")
        stage.actual_start = timezone.localdate() - timedelta(days=3)
        stage.actual_end = timezone.localdate()
        stage.save(update_fields=["actual_start", "actual_end"])

        result = calculate_local_sewing(order)

        self.assertEqual(result["total_sewing_revenue"], Decimal("5000.00"))
        self.assertEqual(result["total_sewing_cost"], Decimal("3500.00"))
        self.assertEqual(result["profit"], Decimal("1500.00"))
        self.assertEqual(result["margin"], Decimal("30.00"))
        self.assertEqual(result["days_used"], 4)
        self.assertEqual(result["daily_output"], Decimal("20.00"))

    def test_missing_or_zero_cost_never_returns_a_hundred_percent_margin(self):
        for value in (None, Decimal("0")):
            order = self.create_order(sewing_cost_per_piece_bdt=value)
            result = calculate_local_sewing(order)
            self.assertFalse(result["cost_available"])
            self.assertIsNone(result["total_sewing_cost"])
            self.assertIsNone(result["profit"])
            self.assertIsNone(result["margin"])

    def test_summary_is_native_bdt_and_export_currencies_stay_separate(self):
        self.create_order()
        ProductionOrder.objects.create(
            title="FOB CAD",
            order_type="fob",
            approved_currency="CAD",
            approved_total_value=Decimal("1250.00"),
        )
        ProductionOrder.objects.create(
            title="Canada USD",
            order_type="canada_full",
            approved_currency="USD",
            approved_total_value=Decimal("850.00"),
        )

        local = summarize_local_sewing_orders()
        export = {row["currency"]: row["amount"] for row in summarize_canada_export_orders()}

        self.assertEqual(local["currency"], "BDT")
        self.assertEqual(local["total_sewing_revenue"], Decimal("5000.00"))
        self.assertEqual(export, {"CAD": Decimal("1250.00"), "USD": Decimal("850.00")})

        with self.assertNumQueries(1):
            combined = summarize_production_business_models()
        combined_export = {
            row["currency"]: row["amount"]
            for row in combined["canada_export_revenue_rows"]
        }
        self.assertEqual(combined["local_sewing"]["total_sewing_revenue"], Decimal("5000.00"))
        self.assertEqual(combined_export, export)

    def test_production_form_saves_local_inputs_and_sewing_dates(self):
        today = timezone.localdate()
        form = ProductionOrderForm(
            data={
                "title": "Form local sewing",
                "factory_location": "bd",
                "production_order_type": "bulk",
                "operational_status": "sewing",
                "order_type": "sewing_charge",
                "qty_total": "50",
                "qty_reject": "2",
                "completed_quantity": "20",
                "sewing_charge_per_piece_bdt": "90.00",
                "sewing_cost_per_piece_bdt": "",
                "extra_local_cost_bdt": "100.00",
                "sewing_start_date": today.isoformat(),
                "sewing_end_date": today.isoformat(),
                "size_group": "unisex",
            },
            can_edit_local_sewing_financials=True,
        )
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        stage = order.stages.get(stage_key="sewing")
        self.assertEqual(stage.actual_start, today)
        self.assertEqual(stage.actual_end, today)
        self.assertIsNone(calculate_local_sewing(order)["margin"])

    def test_canada_form_does_not_require_local_sewing_fields(self):
        form = ProductionOrderForm(
            data={
                "title": "Canada export unchanged",
                "factory_location": "ca",
                "production_order_type": "bulk",
                "operational_status": "planning",
                "order_type": "canada_full",
                "qty_total": "25",
                "qty_reject": "0",
                "size_group": "unisex",
            },
            can_edit_local_sewing_financials=True,
        )
        self.assertTrue(form.is_valid(), form.errors)


class LocalSewingWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="local-sewing-admin",
            email="local-sewing@example.com",
            password="test-pass",
        )
        cls.customer = Customer.objects.create(
            account_brand="Bangladesh Local Customer",
            contact_name="Local Buyer",
            country="Bangladesh",
        )
        cls.order = ProductionOrder.objects.create(
            title="Local CMT Order",
            customer=cls.customer,
            factory_location="bd",
            order_type="sewing_charge",
            qty_total=200,
            completed_quantity=75,
            sewing_charge_per_piece_bdt=Decimal("120.00"),
            sewing_cost_per_piece_bdt=Decimal("80.00"),
            extra_local_cost_bdt=Decimal("1000.00"),
            operational_status="sewing",
            status="in_progress",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_local_detail_is_simplified_and_canada_detail_is_unchanged(self):
        local_response = self.client.get(reverse("production_detail", args=[self.order.pk]))
        canada = ProductionOrder.objects.create(
            title="Existing Canada Export",
            factory_location="ca",
            order_type="canada_full",
            qty_total=40,
        )
        canada_response = self.client.get(reverse("production_detail", args=[canada.pk]))

        self.assertContains(local_response, "Bangladesh Local Sewing")
        self.assertContains(local_response, "৳24,000.00")
        self.assertContains(local_response, "In progress")
        self.assertContains(local_response, "37% complete")
        self.assertNotContains(local_response, "Stage Progress Tracker")
        self.assertContains(canada_response, "Stage Progress Tracker")
        self.assertFalse(is_bangladesh_local_sewing(canada))

    def test_bdt_invoice_uses_order_revenue_and_not_internal_sewing_cost(self):
        response = self.client.post(
            reverse("invoice_add_bd") + f"?order_id={self.order.pk}",
            {
                "order": self.order.pk,
                "customer": self.customer.pk,
                "invoice_number": "",
                "issue_date": timezone.localdate().isoformat(),
                "due_date": (timezone.localdate() + timedelta(days=14)).isoformat(),
                "currency": "USD",
                "invoice_market": "north_america",
                "invoice_type": "bulk",
                "deposit_percentage": "50",
                "subtotal": "1.00",
                "shipping_amount": "999.00",
                "discount_amount": "0",
                "tax_amount": "0",
                "paid_amount": "0",
                "status": "draft",
                "notes": "Local sewing invoice",
                "sewing_charge": "0",
                "other_internal_cost": "0",
                "internal_cost_note": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(order=self.order)
        self.assertEqual(invoice.currency, "BDT")
        self.assertEqual(invoice.invoice_market, "bangladesh")
        self.assertEqual(invoice.invoice_type, "sewing_charge")
        self.assertEqual(invoice.subtotal, Decimal("24000.00"))
        self.assertEqual(invoice.shipping_amount, Decimal("0"))
        self.assertEqual(invoice.sewing_charge, Decimal("0"))
        rendered = self.client.get(reverse("invoice_view", args=[invoice.pk]))
        self.assertContains(rendered, "Service Type: Bangladesh Local Sewing")
        self.assertContains(rendered, "Charge Type: Sewing Charge / CMT")

    def test_main_dashboard_and_report_show_separate_local_totals(self):
        main = self.client.get(reverse("main_dashboard"))
        report = self.client.get(reverse("production_profit_report"))

        self.assertEqual(main.status_code, 200)
        cards = {card["title"]: card for card in main.context["primary_kpis"]}
        self.assertIn("Bangladesh Sewing Revenue", cards)
        self.assertIn("\u09F324.0K", cards["Bangladesh Sewing Revenue"]["value"])
        self.assertEqual(main.context["local_sewing_summary"]["total_sewing_revenue"], Decimal("24000.00"))
        self.assertContains(report, "Bangladesh Sewing Revenue")
        self.assertContains(report, "\u09F324,000.00")


class LocalSewingPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.sales = user_model.objects.create_user(username="local-sales", password="pass")
        cls.other_sales = user_model.objects.create_user(username="other-sales", password="pass")
        cls.production = user_model.objects.create_user(username="local-production", password="pass")
        cls.accounts = user_model.objects.create_user(username="local-accounts", password="pass")
        cls.admin = user_model.objects.create_user(username="local-admin", password="pass")
        cls.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        cls.other_sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        cls.production.groups.add(Group.objects.get_or_create(name="Production")[0])
        cls.accounts.groups.add(Group.objects.get_or_create(name="Accounts")[0])
        cls.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        assigned_lead = Lead.objects.create(account_brand="Assigned Local", assigned_to=cls.sales)
        other_lead = Lead.objects.create(account_brand="Restricted Local", assigned_to=cls.other_sales)
        cls.assigned_order = ProductionOrder.objects.create(
            title="Assigned sewing order",
            lead=assigned_lead,
            assigned_production_manager=cls.production,
            factory_location="bd",
            order_type="sewing_charge",
            qty_total=20,
            sewing_charge_per_piece_bdt=Decimal("100"),
            sewing_cost_per_piece_bdt=Decimal("70"),
        )
        cls.restricted_order = ProductionOrder.objects.create(
            title="Restricted sewing order",
            lead=other_lead,
            factory_location="bd",
            order_type="sewing_charge",
            qty_total=10,
            sewing_charge_per_piece_bdt=Decimal("100"),
        )

    def test_sales_only_sees_assigned_records_and_not_financial_totals(self):
        self.client.force_login(self.sales)
        allowed = self.client.get(reverse("production_detail", args=[self.assigned_order.pk]))
        denied = self.client.get(reverse("production_detail", args=[self.restricted_order.pk]))
        self.assertEqual(allowed.status_code, 200)
        self.assertNotContains(allowed, "Total sewing revenue")
        self.assertEqual(denied.status_code, 404)

    def test_production_is_scoped_to_assigned_orders(self):
        self.client.force_login(self.production)
        self.assertEqual(
            self.client.get(reverse("production_detail", args=[self.assigned_order.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("production_detail", args=[self.restricted_order.pk])).status_code,
            404,
        )

    def test_accounts_and_admin_can_view_local_financials(self):
        for user in (self.accounts, self.admin):
            self.client.force_login(user)
            response = self.client.get(reverse("production_detail", args=[self.assigned_order.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Total sewing revenue")
