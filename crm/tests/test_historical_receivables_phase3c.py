import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from crm.models import (
    AccountingEntry,
    Customer,
    Invoice,
    InvoicePayment,
    ReceivableAllocation,
    ReceivableEvent,
)
from crm.services.historical_receivables import (
    build_historical_ledger_plan,
    populate_historical_ledger,
)
from crm.services.receivables_ledger import (
    ReceivablesLedgerError,
    create_draft_allocation,
    create_draft_event,
    post_allocation,
    post_event,
)


class HistoricalReceivablesPhase3CTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="phase3c-finance",
            password="test-password",
            is_staff=True,
        )
        self.customer = Customer.objects.create(
            customer_code="CUS-PHASE3C",
            account_brand="Phase 3C Customer",
        )
        self.today = date(2026, 7, 31)

    def _invoice(
        self,
        *,
        number="INV-PHASE3C-1",
        total=Decimal("500.00"),
        paid=Decimal("0.00"),
        status="sent",
        currency="CAD",
        archived=False,
    ):
        return Invoice.objects.create(
            invoice_number=number,
            customer=self.customer,
            issue_date=self.today,
            invoice_date=self.today,
            currency=currency,
            invoice_region="BD" if currency == "BDT" else "CA",
            total_amount=total,
            paid_amount=paid,
            status=status,
            invoice_status="APPROVED" if status != "draft" else "DRAFT",
            approved_by=self.user if status != "draft" else None,
            is_archived=archived,
        )

    def _payment(
        self,
        invoice,
        *,
        amount,
        currency=None,
        accounting_entry=None,
        transfer_ref="BANK-PHASE3C",
    ):
        currency = currency or invoice.currency
        rates = {
            "CAD": (Decimal("1"), Decimal("90")),
            "BDT": (Decimal("90"), Decimal("1")),
            "USD": (Decimal("1.35"), Decimal("121.50")),
        }
        rate_to_cad, rate_to_bdt = rates[currency]
        if accounting_entry is None:
            accounting_entry = AccountingEntry.objects.create(
                date=self.today,
                side="BD" if currency == "BDT" else "CA",
                direction=AccountingEntry.DIR_IN,
                status="PAID",
                main_type="INCOME",
                sub_type="Invoice payment received",
                customer=invoice.customer,
                currency=currency,
                amount_original=amount,
                rate_to_cad=rate_to_cad,
                rate_to_bdt=rate_to_bdt,
                transfer_ref=transfer_ref,
                created_by=self.user,
            )
        return InvoicePayment.objects.create(
            invoice=invoice,
            accounting_entry=accounting_entry,
            payment_date=self.today,
            amount=amount,
            currency=currency,
            side="BD" if currency == "BDT" else "CA",
            rate_to_cad=rate_to_cad,
            rate_to_bdt=rate_to_bdt,
            created_by=self.user,
        )

    def test_dry_run_is_read_only_reconciled_and_two_queries(self):
        invoice = self._invoice(paid=Decimal("125.00"), status="partial")
        self._payment(invoice, amount=Decimal("125.00"))

        with self.assertNumQueries(2):
            result = populate_historical_ledger()

        self.assertTrue(result.dry_run)
        self.assertTrue(result.ready)
        self.assertEqual(result.events_planned, {"CASH_RECEIPT": 1, "INVOICE_ISSUED": 1})
        self.assertEqual(result.allocations_planned, 1)
        self.assertEqual(result.reconciliation[0]["current_outstanding"], Decimal("375.00"))
        self.assertEqual(result.reconciliation[0]["ledger_outstanding"], Decimal("375.00"))
        self.assertFalse(ReceivableEvent.objects.exists())
        self.assertFalse(ReceivableAllocation.objects.exists())

    def test_apply_populates_exact_sources_without_changing_legacy_records(self):
        invoice = self._invoice(paid=Decimal("500.00"), status="paid")
        payment = self._payment(invoice, amount=Decimal("500.00"))
        invoice_snapshot = (invoice.status, invoice.paid_amount, invoice.updated_at)
        payment_snapshot = (
            payment.invoice_id,
            payment.accounting_entry_id,
            payment.amount,
            payment.currency,
            payment.payment_date,
        )

        result = populate_historical_ledger(actor=self.user, apply=True)

        self.assertTrue(result.ready)
        self.assertEqual(result.events_created, {"CASH_RECEIPT": 1, "INVOICE_ISSUED": 1})
        self.assertEqual(result.allocations_created, 1)
        issue = ReceivableEvent.objects.get(kind=ReceivableEvent.KIND_INVOICE_ISSUED)
        receipt = ReceivableEvent.objects.get(kind=ReceivableEvent.KIND_CASH_RECEIPT)
        allocation = ReceivableAllocation.objects.get()
        self.assertEqual(issue.source_invoice_id, invoice.pk)
        self.assertEqual(issue.native_amount, invoice.total_amount)
        self.assertEqual(issue.event_date, invoice.effective_invoice_date)
        self.assertEqual(receipt.source_invoice_id, invoice.pk)
        self.assertEqual(receipt.legacy_invoice_payment_id, payment.pk)
        self.assertEqual(receipt.accounting_entry_id, payment.accounting_entry_id)
        self.assertEqual(allocation.invoice_id, invoice.pk)
        self.assertEqual(allocation.event_id, receipt.pk)
        self.assertEqual(allocation.signed_amount, Decimal("500.00"))
        invoice.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual((invoice.status, invoice.paid_amount, invoice.updated_at), invoice_snapshot)
        self.assertEqual(
            (
                payment.invoice_id,
                payment.accounting_entry_id,
                payment.amount,
                payment.currency,
                payment.payment_date,
            ),
            payment_snapshot,
        )

    def test_apply_is_idempotent(self):
        invoice = self._invoice(paid=Decimal("125.00"), status="partial")
        self._payment(invoice, amount=Decimal("125.00"))
        first = populate_historical_ledger(actor=self.user, apply=True)
        second = populate_historical_ledger(actor=self.user, apply=True)

        self.assertEqual(first.events_created, {"CASH_RECEIPT": 1, "INVOICE_ISSUED": 1})
        self.assertEqual(second.events_created, {})
        self.assertEqual(second.events_reused, {"CASH_RECEIPT": 1, "INVOICE_ISSUED": 1})
        self.assertEqual(second.allocations_created, 0)
        self.assertEqual(second.allocations_reused, 1)
        self.assertEqual(ReceivableEvent.objects.count(), 2)
        self.assertEqual(ReceivableAllocation.objects.count(), 1)

    def test_apply_uses_bounded_batch_queries(self):
        for index in range(20):
            invoice = self._invoice(
                number=f"INV-BATCH-{index}",
                total=Decimal("100.00"),
                paid=Decimal("25.00"),
                status="partial",
            )
            self._payment(invoice, amount=Decimal("25.00"), transfer_ref=f"BANK-BATCH-{index}")

        with CaptureQueriesContext(connection) as queries:
            result = populate_historical_ledger(actor=self.user, apply=True)

        self.assertTrue(result.ready)
        self.assertEqual(ReceivableEvent.objects.count(), 40)
        self.assertEqual(ReceivableAllocation.objects.count(), 20)
        self.assertLessEqual(len(queries), 14)

    def test_draft_and_archived_open_invoices_are_exceptions(self):
        self._invoice(number="INV-DRAFT", total=Decimal("100.00"), status="draft")
        self._invoice(number="INV-ARCHIVED", total=Decimal("200.00"), archived=True)

        result = populate_historical_ledger()
        codes = {row.code for row in result.exceptions}

        self.assertIn("draft_invoice_not_issued", codes)
        self.assertIn("archived_invoice_open_balance", codes)
        self.assertFalse(result.ready)
        self.assertEqual(result.reconciliation[0]["current_outstanding"], Decimal("100.00"))
        self.assertEqual(result.reconciliation[0]["ledger_outstanding"], Decimal("200.00"))

    def test_paid_amount_without_transaction_does_not_invent_opening_receipt(self):
        self._invoice(paid=Decimal("100.00"), status="partial")

        result = populate_historical_ledger()

        self.assertIn("untracked_paid_balance", {row.code for row in result.exceptions})
        self.assertNotIn(ReceivableEvent.KIND_OPENING_RECEIPT, result.events_planned)
        self.assertEqual(result.reconciliation[0]["outstanding_difference"], Decimal("100.00"))
        self.assertFalse(result.ready)

    def test_currency_mismatch_is_not_imported(self):
        invoice = self._invoice(paid=Decimal("100.00"), status="partial")
        self._payment(invoice, amount=Decimal("100.00"), currency="USD")

        plan = build_historical_ledger_plan()

        self.assertEqual([spec.kind for spec in plan.event_specs], [ReceivableEvent.KIND_INVOICE_ISSUED])
        self.assertFalse(plan.allocation_specs)
        exception = next(row for row in plan.exceptions if row.code == "payment_integrity_error")
        self.assertIn("payment currency does not match invoice currency", exception.reason)

    def test_bdt_and_usd_keep_native_amounts_without_inventing_invoice_fx(self):
        for currency, amount in (("BDT", Decimal("9000.00")), ("USD", Decimal("100.00"))):
            invoice = self._invoice(
                number=f"INV-{currency}",
                total=amount,
                paid=amount,
                status="paid",
                currency=currency,
            )
            self._payment(invoice, amount=amount, transfer_ref=f"BANK-{currency}")

        result = populate_historical_ledger(actor=self.user, apply=True)

        self.assertTrue(result.ready)
        self.assertEqual(
            {row["currency"] for row in result.reconciliation if row["reconciled"]},
            {"BDT", "USD"},
        )
        self.assertEqual(
            ReceivableEvent.objects.filter(kind=ReceivableEvent.KIND_INVOICE_ISSUED).count(),
            2,
        )
        for event in ReceivableEvent.objects.filter(kind=ReceivableEvent.KIND_INVOICE_ISSUED):
            self.assertEqual(event.native_amount, event.source_invoice.total_amount)
            self.assertEqual(event.currency, event.source_invoice.currency)
            self.assertEqual(event.amount_cad, Decimal("0.00"))
        self.assertEqual(
            set(ReceivableAllocation.objects.values_list("currency", flat=True)),
            {"BDT", "USD"},
        )
        self.assertEqual(
            sum(row.code == "invoice_fx_snapshot_unavailable" for row in result.exceptions),
            2,
        )

    def test_no_credit_refund_reversal_or_opening_event_is_inferred(self):
        self._invoice()

        result = populate_historical_ledger()

        self.assertEqual(result.events_planned, {"INVOICE_ISSUED": 1})
        for kind in (
            ReceivableEvent.KIND_CREDIT_NOTE,
            ReceivableEvent.KIND_REFUND,
            ReceivableEvent.KIND_REVERSAL,
            ReceivableEvent.KIND_OPENING_RECEIPT,
        ):
            self.assertNotIn(kind, result.events_planned)
        self.assertIn("No canonical historical source", result.source_capabilities["credit_note"])

    def test_shared_accounting_entry_is_counted_once_and_not_imported(self):
        invoice = self._invoice(total=Decimal("200.00"), paid=Decimal("200.00"), status="paid")
        first = self._payment(invoice, amount=Decimal("100.00"))
        self._payment(invoice, amount=Decimal("100.00"), accounting_entry=first.accounting_entry)

        result = populate_historical_ledger()

        self.assertEqual(result.events_planned, {"INVOICE_ISSUED": 1})
        self.assertEqual(result.reconciliation[0]["recorded_payments"], Decimal("200.00"))
        self.assertEqual(result.reconciliation[0]["linked_accounting_receipts"], Decimal("100.00"))
        payment_errors = [row for row in result.exceptions if row.code == "payment_integrity_error"]
        self.assertEqual(len(payment_errors), 2)
        self.assertTrue(all("more than one InvoicePayment" in row.reason for row in payment_errors))

    def test_overpayment_is_capped_and_requires_manual_classification(self):
        invoice = self._invoice(total=Decimal("100.00"), paid=Decimal("100.00"), status="paid")
        self._payment(invoice, amount=Decimal("80.00"), transfer_ref="BANK-1")
        self._payment(invoice, amount=Decimal("40.00"), transfer_ref="BANK-2")

        result = populate_historical_ledger(actor=self.user, apply=True, allow_exceptions=True)

        self.assertEqual(
            sum(ReceivableAllocation.objects.values_list("signed_amount", flat=True), Decimal("0")),
            Decimal("100.00"),
        )
        self.assertEqual(ReceivableEvent.objects.filter(kind="CASH_RECEIPT").count(), 2)
        self.assertIn("payment_exceeds_unallocated_balance", {row.code for row in result.exceptions})
        self.assertIn("tracked_payments_exceed_stored_paid", {row.code for row in result.exceptions})
        self.assertFalse(result.ready)

    def test_cancelled_invoice_does_not_create_issued_principal(self):
        invoice = self._invoice(total=Decimal("100.00"), paid=Decimal("100.00"), status="cancelled", archived=True)
        self._payment(invoice, amount=Decimal("100.00"))

        result = populate_historical_ledger()

        self.assertEqual(result.events_planned, {"CASH_RECEIPT": 1})
        self.assertEqual(result.allocations_planned, 1)
        self.assertIn("cancelled_invoice_issuance_unverified", {row.code for row in result.exceptions})
        self.assertFalse(result.ready)

    def test_apply_refuses_blocking_exceptions_by_default(self):
        self._invoice(status="draft")

        with self.assertRaisesMessage(ValueError, "blocking exceptions"):
            populate_historical_ledger(actor=self.user, apply=True)
        self.assertFalse(ReceivableEvent.objects.exists())

    def test_invoice_principal_requires_source_and_cannot_be_allocated(self):
        invoice = self._invoice(total=Decimal("100.00"))
        event = create_draft_event(
            customer=self.customer,
            source_invoice=invoice,
            kind=ReceivableEvent.KIND_INVOICE_ISSUED,
            event_date=self.today,
            effective_date=self.today,
            native_amount=Decimal("100.00"),
            currency="CAD",
            rate_to_bdt=Decimal("90"),
            idempotency_key="phase3c-manual-principal",
            actor=self.user,
        )
        event = post_event(event, actor=self.user)
        allocation = create_draft_allocation(
            event=event,
            invoice=invoice,
            signed_amount=Decimal("10.00"),
            allocation_date=self.today,
            idempotency_key="phase3c-invalid-principal-allocation",
            actor=self.user,
        )

        with self.assertRaisesMessage(ReceivablesLedgerError, "cannot be allocated"):
            post_allocation(allocation, actor=self.user)

    def test_management_command_writes_reports_but_no_ledger_in_dry_run(self):
        self._invoice()
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "phase3c.json"
            markdown_path = Path(directory) / "phase3c.md"
            call_command(
                "populate_historical_receivables",
                json_output=str(json_path),
                markdown_output=str(markdown_path),
                verbosity=0,
            )
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            report = markdown_path.read_text(encoding="utf-8")

        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["invoices_scanned"], 1)
        self.assertIn("Phase 3C Historical Receivables Ledger Report", report)
        self.assertFalse(ReceivableEvent.objects.exists())

    def test_management_command_requires_actor_for_apply(self):
        with self.assertRaisesMessage(CommandError, "--actor-username"):
            call_command("populate_historical_receivables", apply=True, verbosity=0)
