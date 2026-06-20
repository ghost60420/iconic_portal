from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from crm.forms import InvoiceForm
from crm.models import Invoice, InvoiceSettings, ProductionOrder, ProductionOrderLine
from crm.models_access import UserAccess


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02"
    b"\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


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

    def test_existing_invoice_edit_still_opens(self):
        response = self.client.get(reverse("invoice_edit", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoice")

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

    def test_pdf_export_supports_market_and_type_layouts(self):
        cases = [
            ("INV-NA-SAMPLE-PDF", "north_america", "sample", "CAD", b"North America Sample Invoice"),
            ("INV-NA-BULK-PDF", "north_america", "bulk", "CAD", b"North America Bulk Production Invoice"),
            ("INV-BD-SAMPLE-PDF", "bangladesh", "sample", "BDT", b"Bangladesh Sample Invoice"),
            ("INV-BD-SEWING-LAYOUT-PDF", "bangladesh", "sewing_charge", "BDT", b"Bangladesh Sewing Charge Invoice"),
            ("INV-BD-BULK-PDF", "bangladesh", "bulk", "BDT", b"Bangladesh Bulk Production Invoice"),
        ]
        for invoice_number, market, invoice_type, currency, expected_label in cases:
            with self.subTest(invoice_number=invoice_number):
                invoice = Invoice.objects.create(
                    invoice_number=invoice_number,
                    invoice_market=market,
                    invoice_region="BD" if market == "bangladesh" else "CA",
                    invoice_type=invoice_type,
                    currency=currency,
                    subtotal=Decimal("2500.00"),
                    total_amount=Decimal("2500.00"),
                    sewing_charge=Decimal("2500.00") if invoice_type == "sewing_charge" else Decimal("0.00"),
                    other_internal_cost=Decimal("1000.00"),
                    internal_cost_note="Do not show internal costing in client PDF.",
                    status="sent",
                )

                response = self.client.get(reverse("invoice_pdf", args=[invoice.pk]))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertIn(expected_label, response.content)
                self.assertNotIn(b"Do not show internal costing", response.content)
                self.assertNotIn(b"Estimated Gross Profit", response.content)

    def test_invoice_settings_page_allows_admin_and_accounting_only(self):
        response = self.client.get(reverse("invoice_settings"))

        self.assertEqual(response.status_code, 200)

        user_model = get_user_model()
        accounting_user = user_model.objects.create_user(username="invoice-accounting", password="test-pass")
        accounting_access = UserAccess.objects.get(user=accounting_user)
        accounting_access.role = UserAccess.ROLE_CA
        accounting_access.can_accounting_ca = True
        accounting_access.can_accounting_bd = False
        accounting_access.save()
        self.client.force_login(accounting_user)

        response = self.client.get(reverse("invoice_settings"))
        self.assertEqual(response.status_code, 200)

        restricted_user = user_model.objects.create_user(username="invoice-restricted", password="test-pass")
        restricted_access = UserAccess.objects.get(user=restricted_user)
        restricted_access.can_accounting_ca = False
        restricted_access.can_accounting_bd = False
        restricted_access.save()
        self.client.force_login(restricted_user)

        response = self.client.get(reverse("invoice_settings"))
        self.assertEqual(response.status_code, 403)

    def test_invoice_settings_drive_terms_payment_and_footer_display(self):
        InvoiceSettings.objects.create(
            company_name="Iconic Test Company",
            paypal_email_or_id="paypal-test",
            etransfer_email="payments@example.com",
            canada_payment_terms="Configured Canada payment terms.",
            terms_and_conditions_na="Configured North America terms.",
            slogan="Configured Slogan",
            invoice_footer_note="Configured footer note.",
            default_tax_note="Tax handled by configured note.",
        )

        response = self.client.get(reverse("invoice_client_view", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Iconic Test Company")
        self.assertContains(response, "paypal-test")
        self.assertContains(response, "payments@example.com")
        self.assertContains(response, "Configured Canada payment terms.")
        self.assertContains(response, "Configured North America terms.")
        self.assertContains(response, "Configured Slogan")
        self.assertContains(response, "Configured footer note.")
        self.assertContains(response, "Tax handled by configured note.")

    def test_missing_qr_images_hide_cleanly_and_uploaded_qr_renders(self):
        InvoiceSettings.objects.create(paypal_email_or_id="no-qr-paypal")
        response = self.client.get(reverse("invoice_client_view", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "PayPal QR code")

        InvoiceSettings.objects.all().delete()
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        with override_settings(MEDIA_ROOT=temp_dir.name):
            InvoiceSettings.objects.create(
                paypal_qr_image=SimpleUploadedFile("paypal.png", TINY_PNG, content_type="image/png")
            )
            response = self.client.get(reverse("invoice_client_view", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PayPal QR code")
        self.assertContains(response, "paypal")

    def test_invoice_form_uses_settings_deposit_defaults_when_blank(self):
        InvoiceSettings.objects.create(
            default_sample_deposit_percentage=Decimal("100.00"),
            default_bulk_deposit_percentage=Decimal("60.00"),
            default_bd_sewing_deposit_percentage=Decimal("80.00"),
        )

        sample_form = InvoiceForm(
            data={
                "invoice_number": "INV-SAMPLE-DEFAULT",
                "issue_date": "2026-06-16",
                "currency": "CAD",
                "invoice_market": "north_america",
                "invoice_type": "sample",
                "deposit_percentage": "",
                "subtotal": "100.00",
                "shipping_amount": "0.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "total_amount": "100.00",
                "paid_amount": "0.00",
                "status": "draft",
            },
            can_edit_internal_costs=True,
        )
        self.assertTrue(sample_form.is_valid(), sample_form.errors)
        self.assertEqual(sample_form.cleaned_data["deposit_percentage"], Decimal("100.00"))

        bulk_form = InvoiceForm(
            data={
                "invoice_number": "INV-BULK-DEFAULT",
                "issue_date": "2026-06-16",
                "currency": "CAD",
                "invoice_market": "north_america",
                "invoice_type": "bulk",
                "deposit_percentage": "",
                "subtotal": "100.00",
                "shipping_amount": "0.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "total_amount": "100.00",
                "paid_amount": "0.00",
                "status": "draft",
            },
            can_edit_internal_costs=True,
        )
        self.assertTrue(bulk_form.is_valid(), bulk_form.errors)
        self.assertEqual(bulk_form.cleaned_data["deposit_percentage"], Decimal("60.00"))

        sewing_form = InvoiceForm(
            data={
                "invoice_number": "INV-BD-SEWING-DEFAULT",
                "issue_date": "2026-06-16",
                "currency": "BDT",
                "invoice_market": "bangladesh",
                "invoice_type": "sewing_charge",
                "deposit_percentage": "",
                "subtotal": "100.00",
                "shipping_amount": "0.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "total_amount": "100.00",
                "paid_amount": "0.00",
                "status": "draft",
                "sewing_charge": "100.00",
                "other_internal_cost": "0.00",
            },
            can_edit_internal_costs=True,
        )
        self.assertTrue(sewing_form.is_valid(), sewing_form.errors)
        self.assertEqual(sewing_form.cleaned_data["deposit_percentage"], Decimal("80.00"))

    def test_invoice_settings_previews_render_without_saved_invoice(self):
        for preview_type in ("north-america", "bangladesh", "sewing-charge"):
            response = self.client.get(reverse("invoice_settings_preview", args=[preview_type]))

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Settings Preview")
            self.assertContains(response, "Back to settings")

    def test_bangladesh_sewing_charge_invoice_shows_multiple_style_summary(self):
        order = ProductionOrder.objects.create(title="Multi Style Order", qty_total=200)
        ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
        ProductionOrderLine.objects.create(order=order, line_no=2, style_name="Match Short", quantity=150)
        invoice = Invoice.objects.create(
            invoice_number="INV-BD-SEWING-MULTI",
            order=order,
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            invoice_type="sewing_charge",
            deposit_percentage=Decimal("50.00"),
            subtotal=Decimal("10000.00"),
            total_amount=Decimal("10000.00"),
            sewing_charge=Decimal("7000.00"),
            other_internal_cost=Decimal("3000.00"),
            internal_cost_note="Internal cost remains hidden.",
            status="sent",
        )

        response = self.client.get(reverse("invoice_client_view", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sewing Charge Summary")
        self.assertContains(response, "Number of styles")
        self.assertContains(response, "Training Tee")
        self.assertContains(response, "Match Short")
        self.assertContains(response, "50")
        self.assertContains(response, "150")
        self.assertContains(response, "Grand total sewing charge")
        self.assertNotContains(response, "Internal cost remains hidden.")

    def test_bangladesh_sewing_charge_invoice_does_not_split_when_style_quantities_missing(self):
        order = ProductionOrder.objects.create(title="Legacy Multi Style Order", qty_total=200)
        ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
        ProductionOrderLine.objects.create(order=order, line_no=2, style_name="Match Short")
        invoice = Invoice.objects.create(
            invoice_number="INV-BD-SEWING-MISSING-QTY",
            order=order,
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            invoice_type="sewing_charge",
            subtotal=Decimal("10000.00"),
            total_amount=Decimal("10000.00"),
            sewing_charge=Decimal("7000.00"),
            status="sent",
        )

        response = self.client.get(reverse("invoice_client_view", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consolidated Sewing Charge")
        self.assertContains(response, "style quantities unavailable")
        self.assertNotContains(response, "Training Tee")
        self.assertNotContains(response, "Match Short")

    def test_bangladesh_sewing_charge_invoice_marks_unavailable_when_no_quantity_exists(self):
        order = ProductionOrder.objects.create(title="No Quantity Sewing Order")
        ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee")
        invoice = Invoice.objects.create(
            invoice_number="INV-BD-SEWING-NO-QTY",
            order=order,
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            invoice_type="sewing_charge",
            subtotal=Decimal("5000.00"),
            total_amount=Decimal("5000.00"),
            sewing_charge=Decimal("5000.00"),
            status="sent",
        )

        response = self.client.get(reverse("invoice_client_view", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quantity unavailable")

    def test_bangladesh_sewing_charge_pdf_uses_real_style_quantities(self):
        order = ProductionOrder.objects.create(title="PDF Multi Style Order", qty_total=200)
        ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
        ProductionOrderLine.objects.create(order=order, line_no=2, style_name="Match Short", quantity=150)
        invoice = Invoice.objects.create(
            invoice_number="INV-BD-SEWING-REAL-QTY-PDF",
            order=order,
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            invoice_type="sewing_charge",
            subtotal=Decimal("10000.00"),
            total_amount=Decimal("10000.00"),
            sewing_charge=Decimal("10000.00"),
            status="sent",
        )

        response = self.client.get(reverse("invoice_pdf", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        body = response.content
        self.assertIn(b"Training Tee", body)
        self.assertIn(b"Match Short", body)
        self.assertIn(b"50", body)
        self.assertIn(b"150", body)
