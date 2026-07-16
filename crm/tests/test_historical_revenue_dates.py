from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.forms import InvoiceForm
from crm.models import Customer, Invoice, Opportunity
from crm.services.historical_dates import (
    apply_invoice_reporting_date_filter,
    apply_opportunity_reporting_date_filter,
)
from crm.services.production_profit import build_production_profit_report
from crm.services.sales_attribution import build_sales_kpis


class HistoricalRevenueDateTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="historical-admin",
            email="historical-admin@example.com",
            password="test-pass",
        )
        self.sales = user_model.objects.create_user(
            username="historical-sales",
            email="historical-sales@example.com",
            password="test-pass",
        )
        access = self.sales.access
        access.can_customers = True
        access.can_opportunities = True
        access.save()
        self.customer = Customer.objects.create(
            account_brand="Historical Brand",
            contact_name="History Buyer",
            email="history@example.com",
        )

    def _invoice(self, number, **overrides):
        values = {
            "invoice_number": number,
            "customer": self.customer,
            "currency": "CAD",
            "issue_date": timezone.localdate(),
            "subtotal": Decimal("1000.00"),
            "tax_amount": Decimal("0.00"),
            "total_amount": Decimal("1000.00"),
            "paid_amount": Decimal("0.00"),
            "status": "sent",
        }
        values.update(overrides)
        return Invoice.objects.create(**values)

    def test_new_historical_fields_default_to_empty_and_do_not_change_created_dates(self):
        invoice = self._invoice("INV-HIST-DEFAULT")
        opportunity = Opportunity.objects.create(customer=self.customer, stage="Prospecting")

        self.assertIsNone(invoice.invoice_date)
        self.assertIsNone(opportunity.opportunity_date)
        self.assertEqual(invoice.effective_invoice_date, invoice.created_at.date())
        self.assertEqual(opportunity.effective_opportunity_date, opportunity.created_date)

    def test_historical_invoice_badge_uses_invoice_date_older_than_created_at(self):
        historical_date = timezone.localdate() - timedelta(days=45)
        invoice = self._invoice("INV-HIST-BADGE", invoice_date=historical_date)

        self.assertTrue(invoice.is_historical_entry)
        self.assertEqual(invoice.effective_invoice_date, historical_date)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("invoice_view", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historical Entry")
        self.assertContains(response, "Revenue date")

    def test_invoice_reporting_date_filter_uses_invoice_date_then_created_at(self):
        today = timezone.localdate()
        historical_date = today - timedelta(days=60)
        historical = self._invoice("INV-HIST-FILTER", invoice_date=historical_date)
        current = self._invoice("INV-HIST-CURRENT")

        historical_qs = apply_invoice_reporting_date_filter(Invoice.objects.all(), historical_date, historical_date)
        current_qs = apply_invoice_reporting_date_filter(Invoice.objects.all(), today, today)

        self.assertIn(historical, historical_qs)
        self.assertNotIn(current, historical_qs)
        self.assertIn(current, current_qs)
        self.assertNotIn(historical, current_qs)

    def test_invoice_list_filters_by_reporting_date_not_issue_date(self):
        today = timezone.localdate()
        historical_date = today - timedelta(days=60)
        historical = self._invoice("INV-HIST-LIST", issue_date=today, invoice_date=historical_date)
        current = self._invoice("INV-HIST-LIST-CURRENT", issue_date=today)

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("invoice_list"),
            {"date_from": historical_date.isoformat(), "date_to": historical_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, historical.invoice_number)
        self.assertNotContains(response, current.invoice_number)

    def test_opportunity_reporting_date_filter_uses_opportunity_date_then_created_date(self):
        today = timezone.localdate()
        historical_date = today - timedelta(days=60)
        historical = Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            opportunity_date=historical_date,
        )
        current = Opportunity.objects.create(customer=self.customer, stage="Prospecting")

        historical_qs = apply_opportunity_reporting_date_filter(Opportunity.objects.all(), historical_date, historical_date)
        current_qs = apply_opportunity_reporting_date_filter(Opportunity.objects.all(), today, today)

        self.assertIn(historical, historical_qs)
        self.assertNotIn(current, historical_qs)
        self.assertIn(current, current_qs)
        self.assertNotIn(historical, current_qs)

    def test_main_dashboard_opportunity_period_uses_opportunity_date(self):
        Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            opportunity_date=timezone.localdate() - timedelta(days=60),
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("main_dashboard"), {"days": "7"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["opp_period"], 0)

    def test_invoice_form_exposes_invoice_date_only_for_ceo_admin_scope(self):
        admin_form = InvoiceForm(can_edit_historical_dates=True)
        regular_form = InvoiceForm(can_edit_historical_dates=False)

        self.assertIn("invoice_date", admin_form.fields)
        self.assertNotIn("invoice_date", regular_form.fields)

    def test_customer_based_opportunity_ignores_historical_date_for_non_admin_user(self):
        self.client.force_login(self.sales)
        historical_date = timezone.localdate() - timedelta(days=30)

        response = self.client.post(
            reverse("add_opportunity"),
            {
                "customer": str(self.customer.pk),
                "stage": "Prospecting",
                "product_type": "Activewear",
                "product_category": "Leggings",
                "order_currency": "CAD",
                "order_value_usd": "2000.00",
                "notes": "Customer direct opportunity",
                "opportunity_date": historical_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        opportunity = Opportunity.objects.get(customer=self.customer)
        self.assertIsNone(opportunity.lead)
        self.assertIsNone(opportunity.opportunity_date)

    def test_ceo_admin_can_create_customer_opportunity_with_historical_date(self):
        self.client.force_login(self.admin)
        historical_date = timezone.localdate() - timedelta(days=30)

        response = self.client.post(
            reverse("add_opportunity"),
            {
                "customer": str(self.customer.pk),
                "stage": "Prospecting",
                "product_type": "Activewear",
                "product_category": "Leggings",
                "order_currency": "CAD",
                "order_value_usd": "2000.00",
                "notes": "Customer direct historical opportunity",
                "opportunity_date": historical_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        opportunity = Opportunity.objects.get(customer=self.customer)
        self.assertIsNone(opportunity.lead)
        self.assertEqual(opportunity.opportunity_date, historical_date)

    def test_opportunity_list_filters_by_reporting_opportunity_date(self):
        historical_date = timezone.localdate() - timedelta(days=400)
        historical = Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            opportunity_date=historical_date,
        )
        current = Opportunity.objects.create(customer=self.customer, stage="Prospecting")

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("opportunities_list"),
            {
                "status": "all",
                "created_from": historical_date.isoformat(),
                "created_to": historical_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, historical.opportunity_id)
        self.assertNotContains(response, current.opportunity_id)

    def test_main_dashboard_custom_range_counts_historical_opportunity_date(self):
        historical_date = timezone.localdate() - timedelta(days=400)
        Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            opportunity_date=historical_date,
        )
        Opportunity.objects.create(customer=self.customer, stage="Prospecting")

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("main_dashboard"),
            {"date_from": historical_date.isoformat(), "date_to": historical_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["opp_period"], 1)

    def test_invoice_reporting_filters_support_2024_2025_and_current_invoices(self):
        invoice_2024 = self._invoice("INV-HIST-2024", invoice_date=date(2024, 5, 10))
        invoice_2025 = self._invoice("INV-HIST-2025", invoice_date=date(2025, 8, 15))
        current = self._invoice("INV-HIST-CURRENT")

        qs_2024 = apply_invoice_reporting_date_filter(Invoice.objects.all(), invoice_2024.invoice_date, invoice_2024.invoice_date)
        qs_2025 = apply_invoice_reporting_date_filter(Invoice.objects.all(), invoice_2025.invoice_date, invoice_2025.invoice_date)
        qs_current = apply_invoice_reporting_date_filter(Invoice.objects.all(), timezone.localdate(), timezone.localdate())

        self.assertIn(invoice_2024, qs_2024)
        self.assertNotIn(invoice_2025, qs_2024)
        self.assertIn(invoice_2025, qs_2025)
        self.assertNotIn(invoice_2024, qs_2025)
        self.assertIn(current, qs_current)

    def test_sales_monthly_revenue_chart_moves_when_invoice_date_is_edited(self):
        current_month = timezone.localdate().replace(day=1)
        previous_month = (current_month - timedelta(days=1)).replace(day=1)
        opportunity = Opportunity.objects.create(
            customer=self.customer,
            assigned_to=self.admin,
            stage="Closed Won",
        )
        invoice = self._invoice(
            "INV-HIST-CHART-MOVE",
            opportunity=opportunity,
            invoice_date=current_month,
            total_amount=Decimal("2300.00"),
            status="paid",
        )

        current_metrics = build_sales_kpis(self.admin)
        cad_series = next(row for row in current_metrics["sales_charts"]["monthly_revenue"]["series"] if row["currency"] == "CAD")
        current_month_point = next(row for row in cad_series["points_meta"] if row["label"] == current_month.strftime("%b %Y"))
        previous_month_point = next(row for row in cad_series["points_meta"] if row["label"] == previous_month.strftime("%b %Y"))
        self.assertEqual(current_month_point["amount"], Decimal("2300.00"))
        self.assertEqual(previous_month_point["amount"], Decimal("0"))

        invoice.invoice_date = previous_month
        invoice.save(update_fields=["invoice_date"])

        moved_metrics = build_sales_kpis(self.admin)
        moved_cad_series = next(row for row in moved_metrics["sales_charts"]["monthly_revenue"]["series"] if row["currency"] == "CAD")
        moved_current_point = next(row for row in moved_cad_series["points_meta"] if row["label"] == current_month.strftime("%b %Y"))
        moved_previous_point = next(row for row in moved_cad_series["points_meta"] if row["label"] == previous_month.strftime("%b %Y"))
        self.assertEqual(moved_current_point["amount"], Decimal("0"))
        self.assertEqual(moved_previous_point["amount"], Decimal("2300.00"))

    def test_production_profit_report_and_export_use_invoice_reporting_date(self):
        historical_date = date(2024, 5, 10)
        invoice = self._invoice(
            "INV-HIST-PROFIT-EXPORT",
            issue_date=timezone.localdate(),
            invoice_date=historical_date,
            invoice_type="sample",
            total_amount=Decimal("750.00"),
            subtotal=Decimal("750.00"),
        )

        historical_report = build_production_profit_report(year=2024, month=5)
        current_report = build_production_profit_report(
            year=timezone.localdate().year,
            month=timezone.localdate().month,
        )

        self.assertIn(invoice.pk, {row["invoice_id"] for row in historical_report["sample_rows"]})
        self.assertNotIn(invoice.pk, {row["invoice_id"] for row in current_report["sample_rows"]})
        export_row = next(row for row in historical_report["export_rows"] if row["reference"] == invoice.invoice_number)
        self.assertEqual(export_row["date"], historical_date)
