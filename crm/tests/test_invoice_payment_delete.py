from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AccountingEntry,
    AccountingEntryAudit,
    AccountingMonthClose,
    CRMAuditLog,
    Customer,
    Invoice,
    InvoicePayment,
)


class InvoicePaymentDeleteTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.ceo = user_model.objects.create_user("payment-delete-ceo", password="pass")
        self.ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        self.accounts = user_model.objects.create_user("payment-delete-accounts", password="pass")
        self.accounts.groups.add(Group.objects.get_or_create(name="Accounts")[0])
        self.sales = user_model.objects.create_user("payment-delete-sales", password="pass")
        self.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        self.customer = Customer.objects.create(account_brand="Payment Delete Co", contact_name="Finance Contact")
        self.today = timezone.localdate()

    def _invoice(self, *, number="INV-PAY-DELETE", total=Decimal("1000.00"), paid=Decimal("0.00"), currency="CAD", status="sent"):
        return Invoice.objects.create(
            customer=self.customer,
            invoice_number=number,
            issue_date=self.today,
            due_date=self.today,
            currency=currency,
            invoice_region="BD" if currency == "BDT" else "CA",
            subtotal=total,
            total_amount=total,
            paid_amount=paid,
            status=status,
        )

    def _entry(self, *, amount, currency="CAD", side="CA"):
        return AccountingEntry.objects.create(
            date=self.today,
            side=side,
            direction=AccountingEntry.DIR_IN,
            status="PAID",
            main_type="INCOME",
            sub_type="Invoice payment received",
            customer=self.customer,
            currency=currency,
            amount_original=amount,
            rate_to_cad=Decimal("1") if currency == "CAD" else Decimal("85"),
            rate_to_bdt=Decimal("1") if currency == "BDT" else Decimal("85"),
            description="Payment received for test invoice",
        )

    def _payment(self, invoice, *, amount, currency="CAD", side="CA", entry=True):
        accounting_entry = self._entry(amount=amount, currency=currency, side=side) if entry else None
        return InvoicePayment.objects.create(
            invoice=invoice,
            accounting_entry=accounting_entry,
            payment_date=self.today,
            amount=amount,
            currency=currency,
            side=side,
            payment_method="bank",
            rate_to_cad=Decimal("1") if currency == "CAD" else Decimal("85"),
            rate_to_bdt=Decimal("1") if currency == "BDT" else Decimal("85"),
            notes="Original payment",
        )

    def _delete(self, invoice, payment, user=None, reason="Duplicate payment entered by mistake.", **client_kwargs):
        self.client.force_login(user or self.ceo)
        return self.client.post(
            reverse("invoice_payment_delete", args=[invoice.pk, payment.pk]),
            {"delete_reason": reason},
            **client_kwargs,
        )

    def test_delete_payment_requires_accounting_permission(self):
        invoice = self._invoice(paid=Decimal("100.00"), status="partial")
        payment = self._payment(invoice, amount=Decimal("100.00"))

        response = self._delete(invoice, payment, self.sales)

        self.assertEqual(response.status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("100.00"))
        self.assertTrue(InvoicePayment.objects.filter(pk=payment.pk).exists())

    def test_accounting_role_can_delete_payment_and_recalculate_partial_invoice(self):
        invoice = self._invoice(paid=Decimal("500.00"), status="partial")
        keep = self._payment(invoice, amount=Decimal("300.00"))
        delete = self._payment(invoice, amount=Decimal("200.00"))

        response = self._delete(invoice, delete, self.accounts)

        self.assertRedirects(response, reverse("invoice_view", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("300.00"))
        self.assertEqual(invoice.status, "partial")
        self.assertTrue(InvoicePayment.objects.filter(pk=keep.pk).exists())
        self.assertFalse(InvoicePayment.objects.filter(pk=delete.pk).exists())

    def test_delete_full_payment_marks_invoice_unpaid_without_overpaid_status(self):
        invoice = self._invoice(total=Decimal("750.00"), paid=Decimal("750.00"), status="paid")
        payment = self._payment(invoice, amount=Decimal("750.00"))

        self._delete(invoice, payment)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(invoice.status, "sent")
        self.assertEqual(invoice.payment_status_key, "unpaid")

    def test_delete_duplicate_from_overpaid_invoice_recalculates_to_paid(self):
        invoice = self._invoice(total=Decimal("700.00"), paid=Decimal("1400.00"), status="paid")
        keep = self._payment(invoice, amount=Decimal("700.00"))
        delete = self._payment(invoice, amount=Decimal("700.00"))

        self._delete(invoice, delete)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("700.00"))
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.payment_status_key, "paid")
        self.assertTrue(InvoicePayment.objects.filter(pk=keep.pk).exists())

    def test_delete_payment_removes_only_payment_accounting_entry_and_logs_audit(self):
        invoice = self._invoice(paid=Decimal("250.00"), status="partial")
        unrelated_entry = self._entry(amount=Decimal("90.00"))
        payment = self._payment(invoice, amount=Decimal("250.00"))
        deleted_entry_id = payment.accounting_entry_id

        self._delete(invoice, payment, reason="Duplicate payment entered by mistake.")

        self.assertFalse(AccountingEntry.objects.filter(pk=deleted_entry_id).exists())
        self.assertTrue(AccountingEntry.objects.filter(pk=unrelated_entry.pk).exists())
        self.assertTrue(
            AccountingEntryAudit.objects.filter(action="DELETE", before_data__id=deleted_entry_id).exists()
        )
        audit = CRMAuditLog.objects.get(module="invoice_payment", record_id=str(payment.pk))
        self.assertIn("Duplicate payment entered by mistake.", audit.new_value)
        self.assertIn('"invoice_id"', audit.previous_value)
        self.assertIn('"original_amount": "250.00"', audit.previous_value)

    def test_delete_in_locked_accounting_period_is_blocked(self):
        invoice = self._invoice(paid=Decimal("120.00"), status="partial")
        payment = self._payment(invoice, amount=Decimal("120.00"))
        AccountingMonthClose.objects.create(
            year=self.today.year,
            month=self.today.month,
            side="CA",
            is_closed=True,
            closed_by=self.ceo,
        )

        response = self._delete(invoice, payment, follow=True)

        self.assertContains(response, "This payment is in a locked accounting period")
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("120.00"))
        self.assertTrue(InvoicePayment.objects.filter(pk=payment.pk).exists())

    def test_second_delete_request_is_idempotent(self):
        invoice = self._invoice(paid=Decimal("100.00"), status="partial")
        payment = self._payment(invoice, amount=Decimal("100.00"))

        self._delete(invoice, payment)
        second = self._delete(invoice, payment, follow=True)

        invoice.refresh_from_db()
        self.assertContains(second, "Payment was already removed")
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(InvoicePayment.objects.filter(pk=payment.pk).count(), 0)

    def test_delete_payments_keeps_currency_totals_separate(self):
        cad_invoice = self._invoice(number="INV-DELETE-CAD", total=Decimal("100.00"), paid=Decimal("100.00"), currency="CAD", status="paid")
        usd_invoice = self._invoice(number="INV-DELETE-USD", total=Decimal("200.00"), paid=Decimal("200.00"), currency="USD", status="paid")
        bdt_invoice = self._invoice(number="INV-DELETE-BDT", total=Decimal("3000.00"), paid=Decimal("3000.00"), currency="BDT", status="paid")
        cad_payment = self._payment(cad_invoice, amount=Decimal("100.00"), currency="CAD", side="CA")
        self._payment(usd_invoice, amount=Decimal("200.00"), currency="USD", side="CA")
        self._payment(bdt_invoice, amount=Decimal("3000.00"), currency="BDT", side="BD")

        self._delete(cad_invoice, cad_payment)

        paid_by_currency = {
            row["currency"]: row["total"]
            for row in Invoice.objects.values("currency").order_by("currency").annotate(total=Sum("paid_amount"))
        }
        self.assertEqual(paid_by_currency["CAD"], Decimal("0"))
        self.assertEqual(paid_by_currency["USD"], Decimal("200"))
        self.assertEqual(paid_by_currency["BDT"], Decimal("3000"))

    def test_invoice_view_only_shows_delete_control_to_authorized_users(self):
        invoice = self._invoice(paid=Decimal("100.00"), status="partial")
        self._payment(invoice, amount=Decimal("100.00"))

        self.client.force_login(self.ceo)
        response = self.client.get(reverse("invoice_view", args=[invoice.pk]))
        self.assertContains(response, 'class="pay-delete-form js-payment-delete-form"')
        self.assertContains(response, 'aria-label="Delete payment')
        self.assertContains(response, "Enter deletion reason:")

        self.client.force_login(self.sales)
        response = self.client.get(reverse("invoice_view", args=[invoice.pk]))
        self.assertNotContains(response, 'class="pay-delete-form js-payment-delete-form"')
        self.assertNotContains(response, 'aria-label="Delete payment')

    def test_ajax_delete_returns_refreshed_invoice_totals_and_payment_history(self):
        invoice = self._invoice(total=Decimal("700.00"), paid=Decimal("1400.00"), status="paid")
        keep = self._payment(invoice, amount=Decimal("700.00"))
        delete = self._payment(invoice, amount=Decimal("700.00"))
        self.client.force_login(self.ceo)

        response = self.client.post(
            reverse("invoice_payment_delete", args=[invoice.pk, delete.pk]),
            {"delete_reason": "Duplicate payment entered by mistake."},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["invoice"]["paid_display"], "CAD $700.00")
        self.assertEqual(payload["invoice"]["balance_display"], "CAD $0.00")
        self.assertEqual(payload["invoice"]["status_key"], "paid")
        self.assertEqual(payload["invoice"]["payment_count"], 1)
        self.assertIn(f'data-payment-row="{keep.pk}"', payload["payment_history_html"])
        self.assertNotIn(f'data-payment-row="{delete.pk}"', payload["payment_history_html"])

    def test_payment_audit_log_page_lists_deleted_payment(self):
        invoice = self._invoice(paid=Decimal("250.00"), status="partial")
        payment = self._payment(invoice, amount=Decimal("250.00"))

        self._delete(invoice, payment, reason="Duplicate payment entered by mistake.")

        self.client.force_login(self.accounts)
        response = self.client.get(reverse("payment_audit_log"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment Audit")
        self.assertContains(response, invoice.invoice_number)
        self.assertContains(response, str(payment.pk))
        self.assertContains(response, "CAD $250.00")
        self.assertContains(response, "Duplicate payment entered by mistake.")
