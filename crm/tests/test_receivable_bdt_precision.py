from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from crm.tests import test_financial_core as core_fixtures
from crm.services.receivable_accounting import issue_invoice_to_financial_core, record_customer_receipt
from crm.services.receivables_ledger import ReceivablesLedgerError, _validate_financial_journal


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True)
class ReceivableBDTPrecisionTests(TestCase):
    setUpTestData = classmethod(core_fixtures.FinancialCoreTests.setUpTestData.__func__)

    def issue_bdt_invoice(self):
        invoice = core_fixtures.FinancialCoreTests.invoice(self, number="BDT-PRECISION")
        invoice.currency = "BDT"
        invoice.invoice_region = "BD"
        invoice.invoice_market = "bangladesh"
        invoice.save()
        state = issue_invoice_to_financial_core(
            invoice, actor=self.user, rate_to_cad=Decimal("0.0134"), rate_to_bdt=Decimal("1"),
        )
        return invoice, state

    def test_nonterminating_reciprocal_invoice_and_receipt_post_without_changing_amounts(self):
        invoice, state = self.issue_bdt_invoice()
        self.assertEqual(state.rate_to_cad, Decimal("0.0134"))
        self.assertEqual(state.receivable_event.rate_to_cad, Decimal("74.626866"))
        self.assertEqual(state.issued_amount_cad, Decimal("13.40"))
        self.assertEqual(state.receivable_event.amount_cad, Decimal("13.40"))
        result = record_customer_receipt(
            customer=self.customer, amount=Decimal("500"), currency="BDT",
            receipt_date=date(2026, 1, 10), payment_account=self.bd_bank,
            reference="BDT-PRECISION-RECEIPT", actor=self.user, invoices=(invoice,),
            payment_method="bank_transfer", rate_to_cad=Decimal("0.0134"), rate_to_bdt=Decimal("1"),
        )
        self.assertEqual(result["event"].amount_cad, Decimal("6.70"))
        self.assertEqual(result["event"].amount_bdt, Decimal("500.00"))
        self.assertEqual(result["journal"].rate_to_cad, Decimal("0.0134"))
        self.assertEqual(result["allocated"], Decimal("500.00"))

    def test_different_bdt_rate_is_still_rejected(self):
        _invoice, state = self.issue_bdt_invoice()
        event = state.receivable_event
        event.rate_to_cad = Decimal("75")
        with self.assertRaisesMessage(ReceivablesLedgerError, "CAD rate must match"):
            _validate_financial_journal(event)

    def test_converted_amount_discrepancy_is_still_rejected(self):
        _invoice, state = self.issue_bdt_invoice()
        event = state.receivable_event
        event.amount_cad += Decimal("0.01")
        with self.assertRaisesMessage(ReceivablesLedgerError, "converted amounts must match"):
            _validate_financial_journal(event)
