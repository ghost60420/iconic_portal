from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from crm.forms import InvoiceForm
from crm.models import AccountingEntry, CRMAuditLog, Customer, Invoice, InvoicePayment
from crm.services.invoice_state import (
    APPROVAL_APPROVED,
    DOCUMENT_ISSUED,
    SETTLEMENT_PARTIAL,
    SETTLEMENT_SETTLED,
    approve_invoice,
    capture_protected_state,
    canonical_approval_status,
    canonical_document_status,
    canonical_settlement_status,
    create_draft_invoice,
    save_invoice_details,
    void_invoice,
)
from crm.services.payment_reconciliation import (
    PaymentWriteError,
    UnsupportedReceivableOperation,
    apply_credit_note,
    record_invoice_payment,
    record_refund,
    reconciliation_difference,
)


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=False)
class FinancialFoundationPhase3AWriteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="phase3a-ceo",
            email="phase3a@example.com",
            password="test-pass",
        )
        self.customer = Customer.objects.create(
            account_brand="Phase 3A Customer",
            contact_name="Finance Contact",
        )
        self.today = timezone.localdate()

    def unsaved_invoice(self, number, *, total=Decimal("100.00")):
        return Invoice(
            customer=self.customer,
            invoice_number=number,
            issue_date=self.today,
            due_date=self.today,
            currency="CAD",
            invoice_region="CA",
            invoice_market="north_america",
            subtotal=total,
            total_amount=total,
            paid_amount=total,
            status="paid",
            invoice_status="APPROVED",
            approved_at=timezone.now(),
            approved_by=self.user,
        )

    def draft_invoice(self, number, *, total=Decimal("100.00")):
        return create_draft_invoice(self.unsaved_invoice(number, total=total), actor=self.user)

    def payment(self, amount):
        return InvoicePayment(
            payment_date=self.today,
            amount=amount,
            currency="CAD",
            side="CA",
            payment_method="bank",
            rate_to_cad=Decimal("1"),
            rate_to_bdt=Decimal("85"),
            notes="Phase 3A characterization payment",
        )

    def issued_invoice(self, number, *, total=Decimal("100.00")):
        invoice = self.draft_invoice(number, total=total)
        invoice, approved = approve_invoice(invoice, actor=self.user)
        self.assertTrue(approved)
        return invoice

    def test_new_invoice_is_forced_to_draft_unpaid_pending_state(self):
        invoice = self.draft_invoice("P3A-CREATE")

        self.assertEqual(invoice.status, "draft")
        self.assertEqual(invoice.invoice_status, "DRAFT")
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertIsNone(invoice.approved_at)
        self.assertIsNone(invoice.approved_by)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="invoice",
                record_id=str(invoice.pk),
                action_type=CRMAuditLog.ACTION_CREATED,
                new_value="draft",
            ).exists()
        )

    def test_invoice_approval_updates_both_compatibility_and_approval_state(self):
        invoice = self.draft_invoice("P3A-APPROVE")
        totals_before = (invoice.total_amount, invoice.paid_amount)

        invoice, approved = approve_invoice(invoice, actor=self.user)

        self.assertTrue(approved)
        self.assertEqual(invoice.status, "sent")
        self.assertEqual(invoice.invoice_status, "APPROVED")
        self.assertEqual(invoice.approved_by, self.user)
        self.assertIsNotNone(invoice.approved_at)
        self.assertEqual((invoice.total_amount, invoice.paid_amount), totals_before)
        self.assertEqual(canonical_document_status(invoice), DOCUMENT_ISSUED)
        self.assertEqual(canonical_approval_status(invoice), APPROVAL_APPROVED)

    def test_partial_payment_creates_one_transaction_and_preserves_balance_formula(self):
        invoice = self.issued_invoice("P3A-PARTIAL")

        result = record_invoice_payment(invoice, self.payment(Decimal("40.00")), actor=self.user)
        invoice = result.invoice

        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 1)
        self.assertEqual(AccountingEntry.objects.filter(invoice_payments=result.payment).count(), 1)
        self.assertEqual(invoice.paid_amount, Decimal("40.00"))
        self.assertEqual(invoice.status, "partial")
        self.assertEqual(invoice.balance, Decimal("60.00"))
        self.assertEqual(canonical_settlement_status(invoice), SETTLEMENT_PARTIAL)
        self.assertEqual(reconciliation_difference(invoice), Decimal("0.00"))

    def test_full_payment_after_partial_payment_settles_invoice(self):
        invoice = self.issued_invoice("P3A-FULL")
        invoice = record_invoice_payment(invoice, self.payment(Decimal("40.00")), actor=self.user).invoice

        invoice = record_invoice_payment(invoice, self.payment(Decimal("60.00")), actor=self.user).invoice

        self.assertEqual(invoice.paid_amount, Decimal("100.00"))
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertEqual(canonical_settlement_status(invoice), SETTLEMENT_SETTLED)
        self.assertEqual(reconciliation_difference(invoice), Decimal("0.00"))

    def test_overpayment_is_rejected_without_any_financial_write(self):
        invoice = self.issued_invoice("P3A-OVERPAY")
        counts_before = (InvoicePayment.objects.count(), AccountingEntry.objects.count())

        with self.assertRaisesMessage(PaymentWriteError, "exceeds the outstanding invoice balance"):
            record_invoice_payment(invoice, self.payment(Decimal("100.01")), actor=self.user)

        invoice.refresh_from_db()
        self.assertEqual((InvoicePayment.objects.count(), AccountingEntry.objects.count()), counts_before)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_cancellation_preserves_invoice_and_financial_values(self):
        invoice = self.issued_invoice("P3A-VOID")
        invoice = record_invoice_payment(invoice, self.payment(Decimal("25.00")), actor=self.user).invoice
        totals_before = (invoice.total_amount, invoice.paid_amount, invoice.payments.count())

        invoice = void_invoice(invoice, actor=self.user, reason="Customer cancelled")

        self.assertEqual(invoice.status, "cancelled")
        self.assertTrue(invoice.is_archived)
        self.assertEqual((invoice.total_amount, invoice.paid_amount, invoice.payments.count()), totals_before)

    def test_refund_and_credit_note_remain_explicitly_blocked_without_schema(self):
        invoice = self.issued_invoice("P3A-UNSUPPORTED")
        counts_before = (InvoicePayment.objects.count(), AccountingEntry.objects.count())

        with self.assertRaises(UnsupportedReceivableOperation):
            record_refund(invoice=invoice, amount=Decimal("10.00"), actor=self.user)
        with self.assertRaises(UnsupportedReceivableOperation):
            apply_credit_note(invoice=invoice, amount=Decimal("10.00"), actor=self.user)

        self.assertEqual((InvoicePayment.objects.count(), AccountingEntry.objects.count()), counts_before)

    def test_payment_currency_and_side_must_match_invoice(self):
        invoice = self.issued_invoice("P3A-CURRENCY")
        wrong_currency = self.payment(Decimal("10.00"))
        wrong_currency.currency = "USD"
        wrong_currency.rate_to_bdt = Decimal("90")

        with self.assertRaisesMessage(PaymentWriteError, "currency must match"):
            record_invoice_payment(invoice, wrong_currency, actor=self.user)

        wrong_side = self.payment(Decimal("10.00"))
        wrong_side.side = "BD"
        with self.assertRaisesMessage(PaymentWriteError, "Payment side must be CA"):
            record_invoice_payment(invoice, wrong_side, actor=self.user)

        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())

    def test_invoice_form_keeps_financial_state_fields_visible_but_read_only(self):
        invoice = self.issued_invoice("P3A-FORM")
        form = InvoiceForm(instance=invoice)

        self.assertTrue(form.fields["status"].disabled)
        self.assertTrue(form.fields["paid_amount"].disabled)

    def test_invoice_detail_write_restores_protected_financial_state(self):
        invoice = self.issued_invoice("P3A-EDIT")
        invoice = record_invoice_payment(invoice, self.payment(Decimal("25.00")), actor=self.user).invoice
        protected_state = capture_protected_state(invoice)

        invoice.notes = "Permitted detail change"
        invoice.status = "paid"
        invoice.paid_amount = Decimal("99.00")
        invoice.invoice_status = "DRAFT"
        invoice.approved_at = None
        invoice.approved_by = None
        invoice = save_invoice_details(invoice, protected_state=protected_state, actor=self.user)

        self.assertEqual(invoice.notes, "Permitted detail change")
        self.assertEqual(invoice.status, "partial")
        self.assertEqual(invoice.paid_amount, Decimal("25.00"))
        self.assertEqual(invoice.invoice_status, "APPROVED")
        self.assertIsNotNone(invoice.approved_at)
        self.assertEqual(invoice.approved_by, self.user)

    def test_production_writers_do_not_write_protected_state_directly(self):
        project_root = Path(__file__).resolve().parents[2]
        view_source = (project_root / "crm" / "views_invoice.py").read_text()
        costing_source = (project_root / "crm" / "services" / "costing_workflow.py").read_text()

        for direct_write in (
            "inv.status =",
            "inv2.status =",
            "invoice.status =",
            "inv.paid_amount =",
            "inv2.paid_amount =",
            "invoice.paid_amount =",
            "InvoicePayment.objects.create(",
        ):
            self.assertNotIn(direct_write, view_source)
        self.assertNotIn("Invoice.objects.create(", costing_source)
