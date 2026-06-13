from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models import Invoice


class InvoiceInternalCostingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="invoice-admin",
            email="invoice-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)
        self.invoice = Invoice.objects.create(
            invoice_number="INV-TEST-COSTING",
            currency="CAD",
            subtotal=Decimal("100.00"),
            shipping_amount=Decimal("15.00"),
            discount_amount=Decimal("5.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("110.00"),
            paid_amount=Decimal("40.00"),
            sewing_charge=Decimal("25.50"),
            other_internal_cost=Decimal("10.00"),
            internal_cost_note="Factory costing note should stay internal.",
            status="partial",
        )

    def test_invoice_profit_properties_use_decimal_values(self):
        self.assertEqual(self.invoice.total_internal_cost, Decimal("35.50"))
        self.assertEqual(self.invoice.estimated_gross_profit, Decimal("74.50"))
        self.assertEqual(self.invoice.estimated_profit_margin.quantize(Decimal("0.01")), Decimal("67.73"))
        self.assertEqual(self.invoice.balance, Decimal("70.00"))

    def test_client_invoice_does_not_show_internal_costing(self):
        response = self.client.get(reverse("invoice_client_view", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grand total")
        self.assertNotContains(response, "Sewing Charge")
        self.assertNotContains(response, "Other Internal Cost")
        self.assertNotContains(response, "Factory costing note should stay internal.")
        self.assertNotContains(response, "Estimated Gross Profit")
        self.assertNotContains(response, "Estimated Profit Margin")

    def test_invoice_pdf_does_not_show_internal_costing(self):
        response = self.client.get(reverse("invoice_pdf", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        body = response.content
        self.assertNotIn(b"Sewing Charge", body)
        self.assertNotIn(b"Other Internal Cost", body)
        self.assertNotIn(b"Factory costing note should stay internal.", body)
        self.assertNotIn(b"Estimated Gross Profit", body)
        self.assertNotIn(b"Estimated Profit Margin", body)

    def test_deposit_terms_are_calculated_from_new_percentage(self):
        self.invoice.deposit_percentage = Decimal("60.00")
        self.invoice.total_amount = Decimal("500.00")

        self.assertEqual(self.invoice.deposit_amount, Decimal("300.00"))
        self.assertEqual(self.invoice.deposit_balance_due, Decimal("200.00"))

    def test_bangladesh_sewing_charge_invoice_shows_only_client_sewing_charge(self):
        invoice = Invoice.objects.create(
            invoice_number="INV-BD-SEWING",
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            invoice_type="sewing_charge",
            deposit_percentage=Decimal("70.00"),
            subtotal=Decimal("12000.00"),
            shipping_amount=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("12000.00"),
            sewing_charge=Decimal("8000.00"),
            other_internal_cost=Decimal("5000.00"),
            internal_cost_note="Hidden BD factory note.",
            status="sent",
        )

        response = self.client.get(reverse("invoice_client_view", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bangladesh Sewing Charge Invoice")
        self.assertContains(response, "Sewing Charge Per Piece")
        self.assertContains(response, "Total Sewing Charge")
        self.assertContains(response, "Advance Required")
        self.assertNotContains(response, "Other Internal Cost")
        self.assertNotContains(response, "Hidden BD factory note.")
        self.assertNotContains(response, "Estimated Gross Profit")
        self.assertNotContains(response, "Estimated Profit Margin")

    def test_bangladesh_sewing_charge_pdf_hides_internal_profit_fields(self):
        invoice = Invoice.objects.create(
            invoice_number="INV-BD-SEWING-PDF",
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            invoice_type="sewing_charge",
            subtotal=Decimal("9000.00"),
            total_amount=Decimal("9000.00"),
            sewing_charge=Decimal("6000.00"),
            other_internal_cost=Decimal("3000.00"),
            internal_cost_note="Hidden PDF internal note.",
            status="sent",
        )

        response = self.client.get(reverse("invoice_pdf", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        body = response.content
        self.assertIn(b"Bangladesh Sewing Charge Invoice", body)
        self.assertIn(b"Total Sewing", body)
        self.assertNotIn(b"Other Internal Cost", body)
        self.assertNotIn(b"Hidden PDF internal note.", body)
        self.assertNotIn(b"Estimated Gross Profit", body)
