from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Customer, Invoice, Opportunity, ProductionOrder
from crm.services.ceo_briefing_metrics import (
    build_open_opportunity_metrics,
    build_production_alert_metrics,
    build_receivable_metrics,
)
from crm.services.ceo_executive import build_ceo_executive_context


class CEODailyBriefingMetricTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.ceo = user_model.objects.create_superuser(
            username="ceo-briefing-admin",
            email="ceo-briefing@example.com",
            password="test-pass",
        )
        self.client.force_login(self.ceo)
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(
            account_brand="Briefing Customer",
            contact_name="Briefing Buyer",
            market="CA",
        )

    def _invoice(self, number, total, paid=Decimal("0"), **overrides):
        values = {
            "invoice_number": number,
            "customer": self.customer,
            "issue_date": self.today,
            "due_date": self.today - timedelta(days=3),
            "currency": "CAD",
            "total_amount": Decimal(str(total)),
            "paid_amount": Decimal(str(paid)),
            "status": "sent",
        }
        values.update(overrides)
        return Invoice.objects.create(**values)

    def test_receivable_metrics_exclude_paid_cancelled_archived_and_zero_balance(self):
        self._invoice("INV-PAID-EXCLUDED", "500", "500", status="paid")
        self._invoice("INV-CANCELLED-EXCLUDED", "500", "0", status="cancelled")
        self._invoice("INV-ZERO-EXCLUDED", "500", "500", status="sent")
        archived = self._invoice("INV-ARCHIVED-EXCLUDED", "500", "0", status="sent")
        archived.is_archived = True
        archived.save(update_fields=["is_archived"])
        partial = self._invoice("INV-PARTIAL-INCLUDED", "500", "125", status="partial")
        future = self._invoice(
            "INV-FUTURE-INCLUDED",
            "250",
            "0",
            status="sent",
            due_date=self.today + timedelta(days=7),
        )

        metrics = build_receivable_metrics(today=self.today)

        self.assertEqual(metrics["outstanding_count"], 2)
        self.assertEqual(metrics["overdue_count"], 1)
        self.assertTrue(metrics["summary_matches_rows"])
        self.assertEqual(metrics["overdue_invoice_rows"][0]["invoice"], partial)
        self.assertEqual(metrics["overdue_invoice_rows"][0]["balance"], Decimal("375"))
        self.assertIn("CAD $625.00", metrics["outstanding_display"])
        self.assertIn("CAD $375.00", metrics["overdue_display"])
        self.assertNotIn("INV-PAID-EXCLUDED", [row["invoice"].invoice_number for row in metrics["overdue_invoice_rows"]])
        self.assertEqual(future.balance, Decimal("250"))

    def test_ceo_executive_dashboard_uses_same_receivable_filter(self):
        self._invoice("INV-CEO-PAID-EXCLUDED", "1000", "1000", status="paid")
        self._invoice("INV-CEO-PARTIAL-INCLUDED", "1000", "400", status="partial")

        context = build_ceo_executive_context()

        receivables = {row["currency"]: row["amount"] for row in context["outstanding_ar"]}
        self.assertEqual(receivables["CAD"], Decimal("600"))

    def test_overdue_currency_formatting_uses_commas_and_native_currency(self):
        self._invoice("INV-COMMA-CAD", "11060391.25", "0", status="sent")
        self._invoice(
            "INV-COMMA-BDT",
            "170000",
            "0",
            status="sent",
            currency="BDT",
            invoice_region="BD",
        )

        response = self.client.get(reverse("daily_ceo_briefing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CAD $11,060,391.25")
        self.assertContains(response, "\u09F3170,000.00")
        self.assertNotContains(response, "CAD $170,000.00")

    def test_open_opportunity_metrics_exclude_closed_archived_and_production_records(self):
        active = Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            is_open=True,
            order_currency="CAD",
            order_value_usd=Decimal("1200"),
        )
        zero_value = Opportunity.objects.create(
            customer=self.customer,
            stage="Qualification",
            is_open=True,
            order_currency="CAD",
        )
        Opportunity.objects.create(customer=self.customer, stage="Closed Lost", is_open=True)
        Opportunity.objects.create(customer=self.customer, stage="Cancelled", is_open=True)
        Opportunity.objects.create(customer=self.customer, stage="Prospecting", is_open=True, is_archived=True)
        production_opportunity = Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            is_open=True,
            order_currency="CAD",
            order_value_usd=Decimal("900"),
        )
        ProductionOrder.objects.create(
            opportunity=production_opportunity,
            customer=self.customer,
            title="Existing production",
            operational_status="sewing",
        )

        metrics = build_open_opportunity_metrics(date_to=self.today)

        self.assertEqual(metrics["count"], 2)
        self.assertEqual(metrics["zero_value_count"], 1)
        self.assertEqual({row.pk for row in metrics["rows"]}, {active.pk, zero_value.pk})
        self.assertIn("CAD $1,200.00", metrics["pipeline_display"])

    def test_daily_briefing_shows_dedicated_zero_value_opportunities_card(self):
        Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            is_open=True,
            order_currency="CAD",
            order_value_usd=Decimal("1200"),
        )
        Opportunity.objects.create(
            customer=self.customer,
            stage="Qualification",
            is_open=True,
            order_currency="CAD",
        )

        response = self.client.get(reverse("daily_ceo_briefing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zero Value Opportunities")
        self.assertContains(response, "Active opportunities without a confirmed pipeline value.")
        self.assertEqual(response.context["open_opportunities"]["zero_value_count"], 1)

    def test_daily_briefing_shows_customers_awaiting_payment_card(self):
        opportunity = Opportunity.objects.create(
            customer=self.customer,
            stage="Proposal",
            is_open=True,
            order_currency="CAD",
            order_value_usd=Decimal("1200"),
        )
        self._invoice(
            "INV-AWAITING-PAYMENT",
            "1200",
            "200",
            status="partial",
            opportunity=opportunity,
        )
        opportunity.refresh_from_db()

        response = self.client.get(reverse("daily_ceo_briefing"))

        self.assertEqual(opportunity.stage, "Awaiting Payment")
        self.assertContains(response, "Customers Awaiting Payment")
        self.assertContains(response, "CAD $1,000.00")
        self.assertEqual(response.context["awaiting_payment_metrics"]["count"], 1)
        self.assertEqual(response.context["awaiting_payment_metrics"]["customer_count"], 1)

    def test_completed_production_is_excluded_and_delayed_active_production_is_included(self):
        delayed_active = ProductionOrder.objects.create(
            customer=self.customer,
            title="Delayed active",
            qty_total=100,
            bulk_deadline=self.today - timedelta(days=2),
            operational_status="sewing",
        )
        ProductionOrder.objects.create(
            customer=self.customer,
            title="Completed old deadline",
            qty_total=50,
            bulk_deadline=self.today - timedelta(days=10),
            operational_status="shipped",
            status="done",
        )
        ProductionOrder.objects.create(
            customer=self.customer,
            title="Due soon active",
            qty_total=75,
            bulk_deadline=self.today + timedelta(days=3),
            operational_status="packing",
        )

        metrics = build_production_alert_metrics(today=self.today)

        self.assertEqual(metrics["delayed_count"], 1)
        self.assertEqual(metrics["due_soon_count"], 1)
        self.assertEqual([order.pk for order in metrics["alert_rows"]][0], delayed_active.pk)
        self.assertNotIn("shipped", [getattr(order, "briefing_operational_status", "") for order in metrics["alert_rows"]])

    def test_daily_briefing_uses_historical_opportunity_date_for_customer_activity(self):
        historical_date = self.today - timedelta(days=90)
        Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            opportunity_date=historical_date,
            order_currency="CAD",
            order_value_usd=Decimal("2300"),
        )
        Opportunity.objects.create(
            customer=self.customer,
            stage="Prospecting",
            order_currency="CAD",
            order_value_usd=Decimal("9999"),
        )

        response = self.client.get(
            reverse("daily_ceo_briefing"),
            {
                "date_from": historical_date.isoformat(),
                "date_to": historical_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["top_customer_activity"][0]["count"], 1)
        self.assertEqual(response.context["top_customer_activity"][0]["total"], Decimal("2300"))
        self.assertContains(response, "CAD $2,300.00")
