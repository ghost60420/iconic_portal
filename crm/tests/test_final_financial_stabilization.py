from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    CostingHeader,
    Customer,
    Invoice,
    InvoicePayment,
    Lead,
    Opportunity,
    OrderLifecycle,
    ProductionOrder,
    QuickCosting,
)
from crm.services.costing_currency import (
    format_compact_finance_money,
    format_finance_money,
)
from crm.services.costing_workflow import (
    CostingWorkflowError,
    build_production_profit_snapshot,
    create_invoice_from_quick_costing,
)
from crm.services.order_lifecycle import build_lifecycle_profit_breakdown


class NativeCurrencyDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="financial-stabilization-admin",
            email="financial-stabilization@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            account_brand="Native Currency Customer",
            contact_name="Finance Buyer",
            country="Canada",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand=self.customer.account_brand,
            contact_name=self.customer.contact_name,
        )

        self.cad_opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            order_currency="CAD",
            order_value=Decimal("500"),
            is_open=True,
        )
        self.usd_opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            order_currency="CAD",
            order_value=Decimal("48400"),
            order_value_usd=Decimal("400"),
            fx_rate_bdt_per_usd=Decimal("121"),
            is_open=True,
        )
        QuickCosting.objects.create(
            opportunity=self.usd_opportunity,
            buyer_name="USD Buyer",
            project_name="USD Project",
            quantity=2,
            currency="USD",
            selling_price_per_piece=Decimal("200"),
            status=QuickCosting.STATUS_APPROVED,
        )
        self.bdt_opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            order_currency="CAD",
            costing_fob_per_piece=Decimal("50"),
            is_open=True,
        )
        CostingHeader.objects.create(
            opportunity=self.bdt_opportunity,
            customer=self.customer,
            order_quantity=4,
            currency="BDT",
            status="approved",
        )

    @staticmethod
    def _amounts(rows):
        return {row["currency"]: row["amount"] for row in rows}

    def test_opportunity_pipeline_uses_quick_advanced_and_native_currency(self):
        response = self.client.get(reverse("opportunities_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._amounts(response.context["pipeline_values"]),
            {"CAD": Decimal("500"), "USD": Decimal("400"), "BDT": Decimal("200")},
        )
        self.assertContains(response, "CAD $500.00")
        self.assertContains(response, "USD $400.00")
        self.assertContains(response, "\u09F3200.00 BDT")

    def test_main_and_operations_dashboards_do_not_mix_pipeline_currency(self):
        main = self.client.get(reverse("main_dashboard"))
        operations = self.client.get(reverse("ceo_operations_dashboard"))

        self.assertEqual(main.status_code, 200)
        self.assertEqual(operations.status_code, 200)
        main_pipeline = next(card for card in main.context["primary_kpis"] if card["title"] == "Open Opportunities")
        operations_pipeline = next(card for card in operations.context["kpi_cards"] if card["label"] == "Open Pipeline")
        for value in (main_pipeline["note"], operations_pipeline["value"]):
            self.assertIn("CAD $500.00", value)
            self.assertIn("USD $400.00", value)
            self.assertIn("\u09F3200.00", value)

    def test_customer_summaries_keep_revenue_by_currency(self):
        detail = self.client.get(reverse("customer_detail", args=[self.customer.pk]))
        listing = self.client.get(reverse("customers_list"))

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            self._amounts(detail.context["revenue_currency_rows"]),
            {"CAD": Decimal("500"), "USD": Decimal("400"), "BDT": Decimal("200")},
        )
        listed_customer = next(row for row in listing.context["customers"] if row.pk == self.customer.pk)
        self.assertEqual(
            self._amounts(listed_customer.revenue_rows),
            {"CAD": Decimal("500"), "USD": Decimal("400"), "BDT": Decimal("200")},
        )


class ReceivablesAndPaymentSourceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="receivables-stabilization-admin",
            email="receivables-stabilization@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def _invoice(self, number, currency, total, paid=Decimal("0")):
        return Invoice.objects.create(
            invoice_number=number,
            issue_date=self.today,
            due_date=self.today + timedelta(days=14),
            currency=currency,
            total_amount=total,
            paid_amount=paid,
            status="partial" if paid else "sent",
        )

    def test_finance_dashboard_receivables_include_usd_as_native_currency(self):
        self._invoice("INV-NATIVE-CAD", "CAD", Decimal("100"))
        self._invoice("INV-NATIVE-USD", "USD", Decimal("300"))
        self._invoice("INV-NATIVE-BDT", "BDT", Decimal("10000"))

        response = self.client.get(reverse("executive_financial_dashboard"))

        self.assertEqual(response.status_code, 200)
        totals = {row["currency"]: row["amount"] for row in response.context["receivable_currency_totals"]}
        self.assertEqual(totals, {"CAD": Decimal("100"), "USD": Decimal("300"), "BDT": Decimal("10000")})
        self.assertContains(response, "CAD Equivalent")

    def test_legacy_paid_amount_and_payment_history_reconcile_without_offset(self):
        invoice = self._invoice(
            "INV00004",
            "CAD",
            Decimal("2136.96"),
            paid=Decimal("2136.96"),
        )
        InvoicePayment.objects.create(
            invoice=invoice,
            payment_date=self.today,
            amount=Decimal("2133.96"),
            currency="CAD",
            side="CA",
        )

        history_total = sum((payment.amount for payment in invoice.payments.all()), Decimal("0"))
        legacy_paid = invoice.paid_amount - history_total

        self.assertEqual(history_total, Decimal("2133.96"))
        self.assertEqual(legacy_paid, Decimal("3.00"))
        self.assertEqual(invoice.balance, Decimal("0.00"))


class QuickCostingCurrencyAndProfitTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="quick-currency-stabilization-admin",
            email="quick-currency-stabilization@example.com",
            password="test-pass",
        )
        self.customer = Customer.objects.create(
            account_brand="Bangladesh Buyer",
            contact_name="BD Buyer",
            country="Bangladesh",
        )
        self.lead = Lead.objects.create(customer=self.customer, market="BD")
        self.opportunity = Opportunity.objects.create(lead=self.lead, customer=self.customer)

    def _quick(self, **overrides):
        values = {
            "opportunity": self.opportunity,
            "buyer_name": "Quick Buyer",
            "project_name": "Quick Project",
            "quantity": 10,
            "currency": "BDT",
            "material_cost": Decimal("100"),
            "production_cost": Decimal("50"),
            "other_expenses": Decimal("20"),
            "shipping_cost": Decimal("30"),
            "selling_price_per_piece": Decimal("30"),
            "commission_per_piece": Decimal("2"),
            "status": QuickCosting.STATUS_QUOTED,
            "quotation_number": "QQT-STABLE-001",
            "quoted_at": timezone.now(),
        }
        values.update(overrides)
        return QuickCosting.objects.create(**values)

    def test_missing_cross_currency_rate_blocks_quick_invoice(self):
        quick = self._quick(currency="CAD", exchange_rate_bdt_per_cad=None)

        with self.assertRaisesMessage(
            CostingWorkflowError,
            "Currency conversion rate is required before creating this invoice.",
        ):
            create_invoice_from_quick_costing(quick, user=self.user)
        self.assertFalse(Invoice.objects.filter(quick_costing=quick).exists())

    def test_quick_lifecycle_profit_includes_shipping_and_commission_once(self):
        quick = self._quick()
        invoice, created = create_invoice_from_quick_costing(quick, user=self.user)
        self.assertTrue(created)
        lifecycle = OrderLifecycle.objects.select_related("invoice__quick_costing").get(invoice=invoice)

        breakdown = build_lifecycle_profit_breakdown(lifecycle)

        self.assertTrue(breakdown["is_comparable"])
        self.assertEqual(breakdown["invoice_total"], Decimal("330.00"))
        self.assertEqual(breakdown["shipping_cost"], Decimal("30"))
        self.assertEqual(breakdown["commission_cost"], Decimal("20"))
        self.assertEqual(breakdown["total_cost"], Decimal("220"))
        self.assertEqual(breakdown["net_profit"], Decimal("110.00"))

    def test_production_profit_refuses_mixed_invoice_currency(self):
        order = ProductionOrder.objects.create(title="Mixed currency order")
        for currency in ("CAD", "USD"):
            Invoice.objects.create(
                order=order,
                invoice_number=f"INV-MIXED-{currency}",
                issue_date=timezone.localdate(),
                currency=currency,
                total_amount=Decimal("100"),
            )

        snapshot = build_production_profit_snapshot(order)

        self.assertFalse(snapshot["can_compare_standard"])
        self.assertIsNone(snapshot["estimated_profit"])
        self.assertIn("multiple currencies", snapshot["comparison_reason"])


class CompactPresentationTests(TestCase):
    def test_compact_formatting_is_presentation_only(self):
        self.assertEqual(format_compact_finance_money(Decimal("1250000"), "CAD"), "CAD $1.25M")
        self.assertEqual(format_compact_finance_money(Decimal("835000"), "USD"), "USD $835K")
        self.assertEqual(format_compact_finance_money(Decimal("12800000"), "BDT"), "\u09F312.8M")
        self.assertEqual(format_finance_money(Decimal("1250000"), "CAD"), "CAD $1,250,000.00")
