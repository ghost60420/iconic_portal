"""Optional Canadian invoice inputs retain approved prices and financial controls."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.forms import InvoiceForm
from crm.models import (
    CRMAuditLog, CostingHeader, FinanceOperation, Invoice, InvoicePayment, JournalEntry,
    ProductionOrder, QuickCosting,
)
from crm.services.costing_workflow import (
    CostingWorkflowError, create_invoice_from_quick_costing,
)
from crm.services.finance_operations import FinanceOperationError, review_operation
from crm.services.invoice_state import (
    InvoiceStateError, capture_protected_state, save_invoice_details,
)
from crm.tests import test_quotation_invoice_form


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True, FINANCIAL_CORE_REPORTING_ACTIVE=True)
class InvoiceCostShippingTests(TestCase):
    setUpTestData = classmethod(test_quotation_invoice_form.QuotationInvoiceFormTests.setUpTestData.__func__)
    actor = test_quotation_invoice_form.QuotationInvoiceFormTests.actor
    quick = test_quotation_invoice_form.QuotationInvoiceFormTests.quick
    payment_data = test_quotation_invoice_form.QuotationInvoiceFormTests.payment_data

    def setUp(self):
        self.sales = self.actor("Sales")
        self.source = self.quick(self.sales)
        self.url = f"{reverse('invoice_add')}?quick_quotation_id={self.source.pk}"
        self.client.force_login(self.ceo)

    def data(self, **overrides):
        values = {
            "source_quick_quotation_id": self.source.pk,
            "invoice_number": "", "issue_date": "2026-08-01", "due_date": "2026-08-31",
            "deposit_percentage": "100", "shipping_amount": "0", "discount_amount": "0",
            "tax_amount": "0", "notes": "Synthetic invoice notes.", "customer_po_number": "CUSTOMER-123",
            "style_count": "2", "production_quantity": "2", "internal_sewing_cost_per_piece": "7.25",
            "shipment_weight_kg": "1.80", "chargeable_weight_kg": "2.00", "fedex_rate_per_kg": "10.00",
            "additional_shipping_charges": "5.00", "shipping_treatment": "included",
        }
        values.update(overrides)
        return values

    def create(self, **overrides):
        response = self.client.post(self.url, self.data(**overrides))
        self.assertEqual(response.status_code, 302,
                         response.context["form"].errors if response.status_code == 200 else "")
        return Invoice.objects.get(quick_costing=self.source)

    def assert_invalid(self, **overrides):
        response = self.client.post(self.url, self.data(**overrides))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(Invoice.objects.filter(quick_costing=self.source).exists())
        self.assertFalse(JournalEntry.objects.exists())
        return response.context["form"]

    def test_sample_multiple_styles_and_internal_sewing_do_not_increase_customer_total(self):
        invoice = self.create()
        self.assertEqual(invoice.invoice_type, "sample")
        self.assertEqual((invoice.style_count, invoice.production_quantity), (2, 2))
        self.assertEqual(invoice.internal_sewing_cost_per_piece, Decimal("7.25"))
        self.assertEqual(invoice.sewing_charge, Decimal("14.50"))
        self.assertEqual(invoice.shipping_cost, Decimal("25.00"))
        self.assertEqual(invoice.shipping_amount, 0)
        self.assertEqual(invoice.total_amount, Decimal("100.00"))
        self.assertEqual(invoice.balance, Decimal("100.00"))
        self.assertEqual(invoice.customer_id, self.customer.pk)
        self.assertEqual(invoice.opportunity_id, self.source.opportunity_id)
        self.assertEqual(invoice.customer_po_number, "CUSTOMER-123")
        self.assertFalse(ProductionOrder.objects.exists())
        self.assertFalse(JournalEntry.objects.exists())

    def test_bulk_internal_shipping_preserves_approved_price(self):
        QuickCosting.objects.filter(pk=self.source.pk).update(
            costing_purpose=QuickCosting.PURPOSE_BULK, pricing_type=QuickCosting.PRICING_FULL_PACKAGE,
        )
        invoice = self.create(shipping_treatment="internal")
        self.assertEqual(invoice.invoice_type, "bulk")
        self.assertEqual(invoice.shipping_cost, Decimal("25.00"))
        self.assertEqual(invoice.shipping_amount, 0)
        self.assertEqual(invoice.total_amount, 100)
        self.assertFalse(CRMAuditLog.objects.filter(field_name="price_adjustment").exists())

    def test_explicit_approved_shipping_replaces_charge_once_and_records_exact_audit(self):
        invoice = self.create(
            shipping_treatment="separate", shipping_amount="99",
            invoice_adjustment_reason="Customer authorized additional FedEx shipping.",
            invoice_adjustment_approved="on",
        )
        self.assertEqual(invoice.shipping_amount, Decimal("25.00"))
        self.assertEqual(invoice.total_amount, Decimal("125.00"))
        audit = CRMAuditLog.objects.get(
            module="invoice", record_id=str(invoice.pk), field_name="price_adjustment",
        )
        self.assertEqual(audit.actor_id, self.ceo.pk)
        self.assertEqual(audit.action_type, CRMAuditLog.ACTION_APPROVED)
        self.assertIsNotNone(audit.created_at)
        before, after = json.loads(audit.previous_value), json.loads(audit.new_value)
        self.assertEqual(Decimal(before["total_amount"]), 100)
        self.assertEqual(Decimal(after["total_amount"]), 125)
        self.assertEqual(Decimal(after["shipping_amount"]), 25)
        self.assertEqual(after["reason"], "Customer authorized additional FedEx shipping.")
        self.source.refresh_from_db()
        self.assertEqual(self.source.selling_price_per_piece, 50)
        for method in ("get", "post"):
            response = self.client.get(self.url) if method == "get" else self.client.post(self.url, self.data())
            self.assertRedirects(response, reverse("invoice_view", args=[invoice.pk]), fetch_redirect_response=False)
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, 125)
        self.assertEqual(Invoice.objects.filter(quick_costing=self.source).count(), 1)
        self.assertEqual(CRMAuditLog.objects.filter(field_name="price_adjustment").count(), 1)

    def test_separate_shipping_requires_reason_and_explicit_approval(self):
        for extra in ({}, {"invoice_adjustment_reason": "Approved cost"}, {"invoice_adjustment_approved": "on"}):
            with self.subTest(extra=extra):
                self.assert_invalid(shipping_treatment="separate", **extra)

    def test_zero_total_price_change_cannot_hide_changed_financial_components(self):
        self.assert_invalid(shipping_treatment="separate", discount_amount="25")

    def test_sales_cannot_forge_shipping_adjustment_approval(self):
        self.client.force_login(self.sales)
        self.assert_invalid(shipping_treatment="separate", invoice_adjustment_reason="Forged", invoice_adjustment_approved="on")

    def test_sales_cannot_read_or_write_internal_rates(self):
        self.client.force_login(self.sales)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for name in ("internal_sewing_cost_per_piece", "fedex_rate_per_kg", "additional_shipping_charges"):
            self.assertNotIn(name, response.context["form"].fields)
            self.assertNotContains(response, f'name="{name}"')
        invoice = self.create(internal_sewing_cost_per_piece="999.99", subtotal="1")
        self.assertIsNone(invoice.internal_sewing_cost_per_piece)
        self.assertIsNone(invoice.fedex_rate_per_kg)
        self.assertEqual(invoice.total_amount, 100)

    def test_source_quantity_and_known_internal_rate_prefill_without_guessed_freight(self):
        QuickCosting.objects.filter(pk=self.source.pk).update(making_cost_per_piece=Decimal("7.25"))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        initial = response.context["form"].initial
        self.assertEqual(initial["production_quantity"], 2)
        self.assertEqual(initial["internal_sewing_cost_per_piece"], Decimal("7.25"))
        self.assertIsNone(initial.get("fedex_rate_per_kg"))
        self.assertIsNone(initial.get("shipment_weight_kg"))
        self.assertContains(response, self.source.quotation_number)
        self.assertContains(response, self.customer.account_brand)

    def test_negative_cost_or_weight_is_rejected(self):
        for name in ("internal_sewing_cost_per_piece", "shipment_weight_kg", "chargeable_weight_kg", "fedex_rate_per_kg", "additional_shipping_charges"):
            with self.subTest(field=name):
                form = self.assert_invalid(**{name: "-0.01"})
                self.assertIn(name, form.errors)

    def test_counts_and_weight_relationship_are_validated(self):
        for extra in ({"style_count": "0"}, {"production_quantity": "0"}, {"style_count": "3"}, {"chargeable_weight_kg": "1"}):
            with self.subTest(extra=extra):
                self.assert_invalid(**extra)

    def test_incomplete_shipping_never_invents_a_rate_or_chargeable_weight(self):
        for extra in ({"fedex_rate_per_kg": ""}, {"chargeable_weight_kg": ""}, {"shipping_treatment": ""}):
            with self.subTest(extra=extra):
                self.assert_invalid(**extra)

    def test_calculated_totals_exceeding_existing_field_precision_are_rejected(self):
        for extra in (
            {"production_quantity": "2000000000", "internal_sewing_cost_per_piece": "9999999999.99"},
            {"chargeable_weight_kg": "99999999.99", "fedex_rate_per_kg": "9999999999.99", "shipping_treatment": "separate",
             "invoice_adjustment_reason": "Oversized test", "invoice_adjustment_approved": "on"},
        ):
            with self.subTest(extra=extra):
                self.assert_invalid(**extra)

    def test_customer_po_reuses_notes_without_conflating_internal_order_number(self):
        invoice = Invoice(notes="First note.\nCustomer PO: OLD\nLast note.\nCustomer PO: DUPLICATE\n")
        invoice.customer_po_number = "NEW-PO"
        self.assertEqual(invoice.customer_po_number, "NEW-PO")
        self.assertEqual(invoice.notes, "Customer PO: NEW-PO\nFirst note.\nLast note.\n")
        original = invoice.notes
        invoice.customer_po_number = "NEW-PO"
        self.assertEqual(invoice.notes, original)
        self.assertIsNone(invoice.order_id)
        invoice.customer_po_number = ""
        self.assertEqual(invoice.notes, "First note.\nLast note.\n")
        with self.assertRaises(ValidationError):
            invoice.customer_po_number = "PO\nInjected note"
        self.assert_invalid(customer_po_number="A" * 129)

    def test_shipping_estimate_uses_chargeable_weight_and_half_up_rounding(self):
        invoice = Invoice(shipment_weight_kg=Decimal("1"), chargeable_weight_kg=Decimal("1.25"),
                          fedex_rate_per_kg=Decimal("2.50"), additional_shipping_charges=Decimal("0"))
        self.assertEqual(invoice.shipping_cost, Decimal("3.13"))
        self.assertEqual(invoice.shipping_amount, 0)
        invoice.fedex_rate_per_kg = None
        self.assertIsNone(invoice.shipping_cost)

    def test_new_cost_inputs_are_not_offered_to_bangladesh_invoice(self):
        form = InvoiceForm(initial={"invoice_market": "bangladesh", "currency": "BDT"},
                           actor=self.ceo, can_edit_internal_costs=True)
        for name in ("style_count", "production_quantity", "internal_sewing_cost_per_piece", "shipment_weight_kg",
                     "chargeable_weight_kg", "fedex_rate_per_kg", "additional_shipping_charges", "shipping_treatment"):
            self.assertNotIn(name, form.fields)

    def test_customer_documents_exclude_new_internal_costs(self):
        invoice = self.create(internal_cost_note="PRIVATE-SEWING-NOTE")
        page = self.client.get(reverse("invoice_client_view", args=[invoice.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context["invoice"].internal_sewing_cost_per_piece)
        self.assertNotContains(page, "PRIVATE-SEWING-NOTE")
        self.assertNotContains(page, "Sewing cost per piece")
        pdf = self.client.get(reverse("invoice_pdf", args=[invoice.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertNotIn(b"PRIVATE-SEWING-NOTE", pdf.content)

    def test_direct_quick_source_service_cannot_bypass_price_approval(self):
        with self.assertRaises((CostingWorkflowError, InvoiceStateError)):
            create_invoice_from_quick_costing(self.source, user=self.ceo, invoice_details={"shipping_amount": Decimal("25")})
        self.assertFalse(Invoice.objects.exists())

    def test_required_price_audit_failure_rolls_back_invoice_and_source(self):
        original_create = CRMAuditLog.objects.create

        def fail_adjustment_audit(**kwargs):
            if kwargs.get("field_name") == "price_adjustment":
                raise RuntimeError("Synthetic approval audit storage failure")
            return original_create(**kwargs)

        original_status = self.source.status
        with patch.object(CRMAuditLog.objects, "create", side_effect=fail_adjustment_audit):
            with self.assertRaisesRegex(RuntimeError, "Synthetic approval audit"):
                self.client.post(self.url, self.data(shipping_treatment="separate", invoice_adjustment_reason="Customer approved", invoice_adjustment_approved="on"))
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, original_status)
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(JournalEntry.objects.exists())

    def test_existing_issued_invoice_cannot_be_repriced_even_by_ceo(self):
        invoice = self.create()
        Invoice.objects.filter(pk=invoice.pk).update(status="sent", invoice_status="APPROVED")
        invoice.refresh_from_db()
        protected = capture_protected_state(invoice)
        invoice.subtotal = Decimal("200")
        invoice.total_amount = Decimal("200")
        with self.assertRaisesRegex(InvoiceStateError, "Issued or paid"):
            save_invoice_details(invoice, protected_state=protected, actor=self.ceo,
                                 adjustment_reason="Attempted historical reprice", adjustment_approved=True)
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, 100)
        self.assertFalse(CRMAuditLog.objects.filter(field_name="price_adjustment").exists())

    def test_draft_repricing_requires_documented_approval_and_keeps_source_unchanged(self):
        invoice = self.create()
        protected = capture_protected_state(invoice)
        invoice.discount_amount = Decimal("10")
        invoice.total_amount = Decimal("90")
        with self.assertRaises(InvoiceStateError):
            save_invoice_details(invoice, protected_state=protected, actor=self.ceo)
        save_invoice_details(invoice, protected_state=protected, actor=self.ceo,
                             adjustment_reason="Authorized customer discount", adjustment_approved=True)
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, 90)
        self.assertEqual(invoice.quick_costing.selling_price_per_piece, 50)
        audit = CRMAuditLog.objects.get(field_name="price_adjustment")
        self.assertEqual(json.loads(audit.previous_value)["total_amount"], "100.00")
        self.assertEqual(json.loads(audit.new_value)["total_amount"], "90.00")

    def test_stale_detail_form_never_overwrites_a_new_verified_payment_projection(self):
        invoice = self.create()
        protected = capture_protected_state(invoice)
        # Simulate another transaction completing verification after the form loaded.
        Invoice.objects.filter(pk=invoice.pk).update(paid_amount=40, status="partial")
        invoice.notes = "Updated nonfinancial note"
        save_invoice_details(invoice, protected_state=protected, actor=self.ceo)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 40)
        self.assertEqual(invoice.balance, 60)
        self.assertEqual(invoice.status, "partial")

    def test_advanced_approved_quotation_uses_same_costs_and_adjustment_control(self):
        from django.utils import timezone
        source = CostingHeader.objects.create(
            opportunity=self.source.opportunity, customer=self.customer,
            style_name="Approved advanced sample", order_quantity=2, currency="CAD",
            factory_location="ca",
            manual_fob_per_piece=50, status="approved", quotation_number="QT-INPUTS",
            quoted_at=timezone.now(), quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_by=self.ceo, quotation_approved_at=timezone.now(),
        )
        url = f"{reverse('invoice_add')}?quotation_id={source.pk}"
        data = self.data(shipping_treatment="separate", invoice_adjustment_reason="Authorized freight", invoice_adjustment_approved="on")
        data.pop("source_quick_quotation_id")
        data["source_quotation_id"] = source.pk
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.status_code == 200 else "")
        invoice = Invoice.objects.get(costing_header=source)
        self.assertEqual(invoice.total_amount, 125)
        self.assertEqual(invoice.sewing_charge, Decimal("14.50"))
        self.assertEqual(invoice.production_quantity, 2)
        self.assertEqual(invoice.customer_po_number, "CUSTOMER-123")
        self.assertEqual(invoice.opportunity_id, source.opportunity_id)
        self.assertRedirects(self.client.get(url), reverse("invoice_view", args=[invoice.pk]), fetch_redirect_response=False)
        self.assertEqual(CRMAuditLog.objects.filter(field_name="price_adjustment").count(), 1)

    def test_new_cost_invoice_retains_unpaid_release_and_verified_partial_full_payments(self):
        invoice = self.create()
        approved_at = self.source.approved_at
        finance, production = self.actor("Finance"), self.actor("Production")
        self.client.force_login(production)
        release_url = reverse("production_from_opportunity", args=[self.source.opportunity_id])
        self.assertEqual(self.client.post(release_url, {"action": "release"}).status_code, 302)
        order = ProductionOrder.objects.get(opportunity=self.source.opportunity)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 0)
        self.assertEqual(invoice.balance, 100)
        self.assertEqual(self.client.post(release_url, {"action": "release"}).status_code, 302)
        self.assertEqual(ProductionOrder.objects.count(), 1)
        for index, (amount, verifier, method) in enumerate((("40", finance, "e_transfer"), ("60", self.ceo, "bank_transfer"))):
            self.client.force_login(self.sales)
            data = self.payment_data(invoice, amount, method=method, reference=f"COSTS-{index}")
            url = reverse("invoice_payment_add", args=[invoice.pk])
            self.assertEqual(self.client.post(url, data).status_code, 302)
            operation = FinanceOperation.objects.filter(invoice=invoice).latest("pk")
            self.assertEqual(operation.state, FinanceOperation.STATE_PENDING)
            old_paid = invoice.paid_amount
            invoice.refresh_from_db()
            self.assertEqual(invoice.paid_amount, old_paid)
            with self.assertRaises(FinanceOperationError):
                review_operation(operation, actor=self.sales, action="APPROVE", notes="Sales cannot verify")
            posted = review_operation(operation, actor=verifier, action="APPROVE", notes="Synthetic bank receipt verified")
            self.assertEqual(posted.state, FinanceOperation.STATE_POSTED)
            totals = posted.primary_journal.lines.aggregate(debit=Sum("native_debit"), credit=Sum("native_credit"))
            self.assertEqual(totals["debit"], totals["credit"])
            journal_count = JournalEntry.objects.count()
            with self.assertRaises(FinanceOperationError):
                review_operation(operation, actor=verifier, action="APPROVE", notes="Duplicate verification")
            self.client.post(url, data)
            self.assertEqual(JournalEntry.objects.count(), journal_count)
            self.assertEqual(FinanceOperation.objects.filter(invoice=invoice).count(), index + 1)
            invoice.refresh_from_db()
            self.assertEqual(invoice.paid_amount, Decimal("40") if index == 0 else Decimal("100"))
            self.client.force_login(self.ceo)
            page = self.client.get(reverse("production_detail", args=[order.pk]))
            self.assertEqual(page.context["production_payment_rows"][0]["summary"]["remaining_balance"], invoice.balance)
        self.assertEqual(invoice.balance, 0)
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 2)
        self.assertEqual(JournalEntry.objects.filter(source_key__startswith="CUSTOMER-RECEIPT:").count(), 2)
        self.assertEqual(invoice.sewing_charge, Decimal("14.50"))
        self.source.refresh_from_db()
        self.assertEqual(self.source.approved_at, approved_at)
        self.assertEqual(ProductionOrder.objects.count(), 1)
