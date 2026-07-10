from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AccountingEntry,
    BDStaff,
    BDStaffMonth,
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
    CurrencyConversionError,
    convert_currency,
    format_compact_finance_money,
    format_finance_money,
)
from crm.services.pipeline import open_pipeline_queryset
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
        for currency, amount, rate_to_cad, rate_to_bdt in (
            ("CAD", Decimal("500"), Decimal("1"), Decimal("100")),
            ("USD", Decimal("400"), Decimal("1.25"), Decimal("125")),
            ("BDT", Decimal("200"), Decimal("100"), Decimal("1")),
        ):
            AccountingEntry.objects.create(
                date=timezone.localdate(),
                side="BD" if currency == "BDT" else "CA",
                direction="IN",
                main_type="INCOME",
                currency=currency,
                amount_original=amount,
                rate_to_cad=rate_to_cad,
                rate_to_bdt=rate_to_bdt,
                customer=self.customer,
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
        self.assertContains(response, "\u09F3200.00")

    def test_main_and_operations_dashboards_do_not_mix_pipeline_currency(self):
        main = self.client.get(reverse("main_dashboard"))
        operations = self.client.get(reverse("ceo_operations_dashboard"))

        self.assertEqual(main.status_code, 200)
        self.assertEqual(operations.status_code, 200)
        main_pipeline = next(card for card in main.context["primary_kpis"] if card["title"] == "Open Pipeline")
        operations_pipeline = next(card for card in operations.context["kpi_cards"] if card["label"] == "Open Pipeline")
        for value in (main_pipeline["note"], operations_pipeline["value"]):
            self.assertIn("CAD $500.00", value)
            self.assertIn("USD $400.00", value)
            self.assertIn("\u09F3200.00", value)

    def test_active_pipeline_surfaces_exclude_production_stage_opportunity_by_default(self):
        production_opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            stage="Production",
            is_open=True,
            order_currency="CAD",
        )
        QuickCosting.objects.create(
            opportunity=production_opportunity,
            buyer_name="HONEST RESTAURANTS CALGARY",
            project_name="Polo Shirt",
            quantity=150,
            currency="CAD",
            selling_price_per_piece=Decimal("21"),
            status=QuickCosting.STATUS_INVOICED,
        )

        pipeline = self.client.get(reverse("opportunities_list"))
        main = self.client.get(reverse("main_dashboard"))
        operations = self.client.get(reverse("ceo_operations_dashboard"))
        customers = self.client.get(reverse("customers_list"))
        ceo = self.client.get(reverse("ceo_dashboard"))

        expected = {"CAD": Decimal("500"), "USD": Decimal("400"), "BDT": Decimal("200")}
        self.assertEqual(self._amounts(pipeline.context["pipeline_values"]), expected)
        self.assertEqual(self._amounts(customers.context["summary"]["pipeline_rows"]), expected)
        self.assertEqual(self._amounts(ceo.context["open_pipeline_rows"]), expected)
        self.assertEqual(customers.context["summary"]["pipeline_count"], 3)
        self.assertEqual(ceo.context["open_pipeline_count"], 3)
        self.assertNotContains(pipeline, production_opportunity.opportunity_id)
        moved_filter = self.client.get(reverse("opportunities_list"), {"status": "moved_to_production"})
        self.assertContains(moved_filter, production_opportunity.opportunity_id)
        for response in (main, operations, customers, ceo, pipeline):
            self.assertContains(response, "CAD $500.00")

    def test_customer_detail_uses_source_labels_and_shared_money_format(self):
        Invoice.objects.create(
            invoice_number="INV-CUSTOMER-FORMAT",
            customer=self.customer,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=14),
            currency="BDT",
            total_amount=Decimal("3611"),
            paid_amount=Decimal("1800"),
            status="partial",
        )

        response = self.client.get(reverse("customer_detail", args=[self.customer.pk]))

        self.assertContains(response, "Accounting revenue")
        self.assertContains(response, "Sales value")
        self.assertContains(response, "\u09F33,611.00")
        self.assertContains(response, "\u09F31,800.00")
        self.assertNotContains(response, "3611.00 BDT")

    def test_zero_cost_invoice_and_costing_hide_margin(self):
        invoice = Invoice.objects.create(
            invoice_number="INV-ZERO-COST",
            customer=self.customer,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=14),
            currency="CAD",
            total_amount=Decimal("100"),
            status="sent",
        )
        zero_costing = CostingHeader.objects.create(
            opportunity=self.cad_opportunity,
            customer=self.customer,
            currency="BDT",
            order_quantity=10,
            manual_fob_per_piece=Decimal("10"),
        )

        invoice_response = self.client.get(reverse("invoice_view", args=[invoice.pk]))
        costing_response = self.client.get(reverse("cost_sheet_detail", args=[zero_costing.pk]))

        self.assertContains(invoice_response, "Cost unavailable")
        self.assertContains(invoice_response, "Margin N/A")
        self.assertContains(costing_response, "Cost unavailable")
        self.assertContains(costing_response, "Margin N/A")

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

    def test_open_pipeline_excludes_every_closed_or_archived_state(self):
        for stage, is_archived in (
            ("Closed Won", False),
            ("Closed Lost", False),
            ("Cancelled", False),
            ("Prospecting", True),
        ):
            Opportunity.objects.create(
                lead=self.lead,
                customer=self.customer,
                stage=stage,
                is_open=True,
                is_archived=is_archived,
            )

        rows = open_pipeline_queryset(Opportunity.objects.all())

        self.assertEqual(rows.count(), 3)
        self.assertFalse(rows.filter(stage__in=["Closed Won", "Closed Lost", "Cancelled"]).exists())
        self.assertFalse(rows.filter(is_archived=True).exists())


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
        self.assertEqual(breakdown["invoice_total"], Decimal("300.00"))
        self.assertEqual(invoice.subtotal, Decimal("300.00"))
        self.assertEqual(invoice.shipping_amount, Decimal("0"))
        self.assertEqual(invoice.total_amount, Decimal("300.00"))
        self.assertEqual(breakdown["shipping_cost"], Decimal("30"))
        self.assertEqual(breakdown["commission_cost"], Decimal("20"))
        self.assertEqual(breakdown["total_cost"], Decimal("220"))
        self.assertEqual(breakdown["net_profit"], Decimal("80.00"))

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


class SharedCurrencyAndPayrollTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="step-two-finance-admin",
            email="step-two-finance@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)

    def test_bdt_1800_at_85_is_cad_21_18_in_helper_and_model(self):
        self.assertEqual(
            convert_currency(Decimal("1800"), "BDT", "CAD", bdt_per_cad=Decimal("85")),
            Decimal("21.18"),
        )
        entry = AccountingEntry.objects.create(
            date=timezone.localdate(),
            side="BD",
            direction="IN",
            main_type="INCOME",
            currency="BDT",
            amount_original=Decimal("1800"),
            rate_to_cad=Decimal("85"),
            rate_to_bdt=Decimal("1"),
        )
        self.assertEqual(entry.amount_cad, Decimal("21.18"))

    def test_shared_helper_rejects_missing_or_invalid_rates(self):
        for rate in (None, Decimal("0"), Decimal("-1"), Decimal("0.01")):
            with self.subTest(rate=rate), self.assertRaises(CurrencyConversionError):
                convert_currency(Decimal("1800"), "BDT", "CAD", bdt_per_cad=rate)

    def test_bd_staff_context_and_payroll_money_format(self):
        staff = BDStaff.objects.create(name="Payroll Staff", base_salary_bdt=Decimal("244200"))
        row = BDStaffMonth.objects.create(staff=staff, year=2026, month=2)

        staff_response = self.client.get(reverse("bd_staff_list"))
        payroll_response = self.client.get(reverse("bd_staff_month_list"))

        self.assertEqual(list(staff_response.context["staff_list"]), [staff])
        self.assertEqual(payroll_response.context["total_payroll"], Decimal("244200.00"))
        self.assertContains(payroll_response, "\u09F3244,200.00")
        self.assertEqual(row.final_pay_bdt, Decimal("244200.00"))
