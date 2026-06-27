from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import AccountingEntry, Customer, ExchangeRate, Invoice, InvoicePayment


class ExecutiveFinanceDashboardEnhancementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="executive-finance-admin",
            email="finance@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(
            account_brand="Executive Finance Client",
            contact_name="Finance Buyer",
            email="finance-buyer@example.com",
        )
        ExchangeRate.objects.create(cad_to_bdt=Decimal("100"))

        self.cad_invoice = self._invoice("INV-EXEC-CAD", "CAD", Decimal("100"), "CA")
        self.bdt_invoice = self._invoice("INV-EXEC-BDT", "BDT", Decimal("10000"), "BD")
        self.usd_invoice = self._invoice("INV-EXEC-USD", "USD", Decimal("300"), "CA")

        InvoicePayment.objects.create(
            invoice=self.cad_invoice,
            payment_date=self.today,
            amount=Decimal("50"),
            currency="CAD",
            side="CA",
        )
        InvoicePayment.objects.create(
            invoice=self.bdt_invoice,
            payment_date=self.today,
            amount=Decimal("5000"),
            currency="BDT",
            side="BD",
            rate_to_cad=Decimal("0.01"),
        )
        InvoicePayment.objects.create(
            invoice=self.usd_invoice,
            payment_date=self.today,
            amount=Decimal("40"),
            currency="USD",
            side="CA",
            rate_to_cad=Decimal("1.25"),
        )

        self._entry("IN", "INCOME", "CAD", Decimal("500"), Decimal("1"))
        self._entry("IN", "INCOME", "BDT", Decimal("10000"), Decimal("0.01"))
        self._entry("IN", "INCOME", "USD", Decimal("100"), Decimal("1.25"))
        self._entry("OUT", "EXPENSE", "CAD", Decimal("25"), Decimal("1"))
        self._entry("OUT", "EXPENSE", "BDT", Decimal("2500"), Decimal("0.01"))
        self._entry("OUT", "EXPENSE", "USD", Decimal("20"), Decimal("1.25"))
        self._entry("OUT", "EXPENSE", "USD", Decimal("10"), Decimal("0"))

    def _invoice(self, number, currency, amount, region):
        return Invoice.objects.create(
            invoice_number=number,
            customer=self.customer,
            issue_date=self.today,
            due_date=self.today,
            currency=currency,
            invoice_region=region,
            subtotal=amount,
            total_amount=amount,
            paid_amount=Decimal("0"),
            status="sent",
        )

    def _entry(self, direction, main_type, currency, amount, rate_to_cad):
        return AccountingEntry.objects.create(
            date=self.today,
            side="BD" if currency == "BDT" else "CA",
            direction=direction,
            main_type=main_type,
            sub_type="Executive test",
            currency=currency,
            amount_original=amount,
            rate_to_cad=rate_to_cad,
            rate_to_bdt=Decimal("1") if currency == "BDT" else Decimal("100"),
            customer=self.customer,
        )

    def test_dashboard_keeps_original_currencies_separate(self):
        response = self.client.get(reverse("executive_financial_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_revenue"], Decimal("725.00"))
        self.assertEqual(response.context["total_received"], Decimal("150.00"))
        self.assertEqual(response.context["total_receivables"], Decimal("200.00"))
        self.assertEqual(response.context["total_payables"], Decimal("75.00"))

        exposure = {row["currency"]: row for row in response.context["currency_exposure_rows"]}
        self.assertEqual(exposure["CAD"]["receivables"], Decimal("100"))
        self.assertEqual(exposure["CAD"]["received"], Decimal("50"))
        self.assertEqual(exposure["CAD"]["payables"], Decimal("25"))
        self.assertEqual(exposure["BDT"]["receivables"], Decimal("10000"))
        self.assertEqual(exposure["BDT"]["received"], Decimal("5000"))
        self.assertEqual(exposure["BDT"]["payables"], Decimal("2500"))
        self.assertEqual(exposure["USD"]["receivables"], Decimal("300"))
        self.assertEqual(exposure["USD"]["received"], Decimal("40"))
        self.assertEqual(exposure["USD"]["payables"], Decimal("30"))

    def test_dashboard_warns_when_cad_conversion_is_unavailable(self):
        response = self.client.get(reverse("executive_financial_dashboard"))

        self.assertEqual(response.context["unconverted_receivable_count"], 1)
        self.assertEqual(response.context["unconverted_payment_count"], 0)
        self.assertEqual(response.context["unconverted_entry_count"], 1)
        self.assertEqual(response.context["conversion_warning_count"], 2)
        self.assertContains(response, "CAD-equivalent totals exclude 2 records")
        self.assertContains(response, "Original currency exposure remains visible below")

    def test_dashboard_uses_explicit_currency_symbols(self):
        response = self.client.get(reverse("executive_financial_dashboard"))

        self.assertContains(response, "CAD $725.00")
        self.assertContains(response, "USD $300.00")
        self.assertContains(response, "\u09F310,000.00 BDT")
        self.assertContains(response, "Currency exposure")

    def test_dashboard_links_all_executive_finance_reports(self):
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

    def test_monthly_receivable_and_payable_rows_keep_currencies_separate(self):
        receivables = self.client.get(reverse("accounts_receivable_dashboard"))
        payables = self.client.get(reverse("accounts_payable_dashboard"))

        self.assertEqual(receivables.status_code, 200)
        self.assertEqual(payables.status_code, 200)
        ar_totals = {
            row["currency"]: row["amount"]
            for row in receivables.context["monthly_rows"][0]["currency_totals"]
        }
        ap_totals = {
            row["currency"]: row["amount"]
            for row in payables.context["monthly_rows"][0]["currency_totals"]
        }
        self.assertEqual(ar_totals, {"BDT": Decimal("5000"), "CAD": Decimal("50"), "USD": Decimal("40")})
        self.assertEqual(ap_totals, {"BDT": Decimal("2500"), "CAD": Decimal("25"), "USD": Decimal("30")})
        self.assertContains(receivables, "CAD $50.00")
        self.assertContains(receivables, "USD $40.00")
        self.assertContains(receivables, "\u09F35,000.00 BDT")
        self.assertContains(payables, "CAD $25.00")
        self.assertContains(payables, "USD $30.00")
        self.assertContains(payables, "\u09F32,500.00 BDT")

    def test_finance_report_suite_uses_explicit_cad_symbol(self):
        for url_name in [
            "profit_loss_dashboard",
            "balance_sheet_dashboard",
            "cash_flow_dashboard",
            "budget_vs_actual_dashboard",
            "financial_forecast_dashboard",
        ]:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "CAD $")
