from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    BankReconciliation,
    BankStatementLine,
    CashBankAccount,
    Customer,
    ExpenseCategory,
    ExpenseRecord,
    FactoryRunningCostDefault,
    FinancialAccount,
    FinancialBudget,
    FinancialPeriod,
    HistoricalExchangeRate,
    Invoice,
    InvoiceFinancialState,
    JournalEntry,
    JournalLine,
    PayrollBatch,
    PayrollLine,
    ProductionOrder,
    QuickCosting,
    ReceivableEvent,
    RecurringExpenseTemplate,
    Supplier,
    SupplierBill,
)
from crm.services.bank_reconciliation import (
    approve_bank_reconciliation,
    match_statement_transaction,
    record_company_transfer,
    submit_bank_reconciliation,
)
from crm.services.chart_of_accounts import ACCOUNT_DEFINITIONS, account_by_key, bootstrap_chart_of_accounts
from crm.services.expense_management import (
    approve_expense,
    approve_payroll_batch,
    generate_recurring_expense_drafts,
    pay_payroll_batch,
)
from crm.services.factory_timeline import (
    lock_factory_timeline_for_approval,
    record_actual_factory_timeline,
    save_estimated_factory_timeline,
)
from crm.services.financial_currency import MissingExchangeRate, resolve_currency_snapshot
from crm.services.financial_journal import (
    JournalError,
    JournalLineSpec,
    create_draft_journal,
    post_journal,
    reverse_journal,
)
from crm.services.financial_permissions import (
    can_manage_financial_transactions,
    can_view_payroll_detail,
    can_view_sensitive_financials,
)
from crm.services.financial_reporting import (
    accounts_payable_aging,
    accounts_receivable_aging,
    balance_sheet,
    budget_vs_actual,
    cash_flow,
    executive_financial_summary,
    profit_and_loss,
    trial_balance,
)
from crm.services.payables_ledger import (
    apply_supplier_credit,
    approve_supplier_bill,
    record_supplier_payment,
)
from crm.services.production_costs import approve_production_cost
from crm.services.receivable_accounting import (
    apply_customer_credit_note,
    invoice_outstanding,
    issue_invoice_to_financial_core,
    record_customer_receipt,
    record_customer_refund,
)


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=False, FINANCIAL_CORE_REPORTING_ACTIVE=False)
class FinancialCoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="core-admin", email="core@example.com", password="test-pass"
        )
        bootstrap_chart_of_accounts(actor=cls.user)
        cls.period = FinancialPeriod.objects.create(
            name="FY 2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            side="",
            created_by=cls.user,
        )
        cls.customer = Customer.objects.create(customer_code="C-FIN", account_brand="Core Customer")
        cls.supplier = Supplier.objects.create(
            code="SUP-FIN",
            name="Core Supplier",
            side="CA",
            default_currency="CAD",
            created_by=cls.user,
        )
        cls.bank = CashBankAccount.objects.create(
            name="Canada Operating",
            kind=CashBankAccount.KIND_BANK,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("CANADIAN_BANK"),
            masked_reference="****1000",
            created_by=cls.user,
        )
        cls.bd_bank = CashBankAccount.objects.create(
            name="Bangladesh Operating",
            kind=CashBankAccount.KIND_BANK,
            side="BD",
            currency="BDT",
            gl_account=account_by_key("BANGLADESH_BANK"),
            masked_reference="****2000",
            created_by=cls.user,
        )
        cls.office_rent = ExpenseCategory.objects.get(code="OFFICE_RENT")

    def snapshot(self, amount="100.00", currency="CAD", transaction_date=date(2026, 1, 10)):
        return resolve_currency_snapshot(
            native_amount=amount,
            currency=currency,
            transaction_date=transaction_date,
            rate_to_cad=Decimal("1") if currency == "CAD" else Decimal("0.01"),
            rate_to_bdt=Decimal("100") if currency == "CAD" else Decimal("1"),
            actor=self.user,
            create_review=False,
        )

    def invoice(self, number="CORE-INV-1", total="1000.00", issue_date=date(2026, 1, 10)):
        return Invoice.objects.create(
            customer=self.customer,
            invoice_number=number,
            issue_date=issue_date,
            invoice_date=issue_date,
            due_date=date(2026, 2, 10),
            currency="CAD",
            invoice_region="CA",
            invoice_market="north_america",
            subtotal=Decimal(total),
            total_amount=Decimal(total),
            status="sent",
            invoice_status="APPROVED",
            approved_by=self.user,
            approved_at=timezone.now(),
        )

    def supplier_bill(self, number="BILL-1", total="500.00", due=date(2026, 2, 1)):
        return SupplierBill.objects.create(
            supplier=self.supplier,
            bill_number=number,
            bill_date=date(2026, 1, 15),
            due_date=due,
            currency="CAD",
            amount_before_tax=Decimal(total),
            tax_amount=Decimal("0"),
            total_amount=Decimal(total),
            rate_to_cad=Decimal("1"),
            rate_to_bdt=Decimal("100"),
            amount_cad=Decimal(total),
            amount_bdt=Decimal(total) * Decimal("100"),
            expense_account=account_by_key("OFFICE_RENT"),
            side="CA",
            remaining_amount=Decimal(total),
            created_by=self.user,
        )

    def test_chart_of_accounts_and_categories_are_idempotent(self):
        first_count = FinancialAccount.objects.count()
        result = bootstrap_chart_of_accounts(actor=self.user)
        self.assertEqual(first_count, len(ACCOUNT_DEFINITIONS))
        self.assertEqual(FinancialAccount.objects.count(), first_count)
        self.assertEqual(result["accounts_created"], 0)
        self.assertTrue(FinancialAccount.objects.get(system_key="ACCOUNTS_RECEIVABLE").is_control_account)
        self.assertTrue(ExpenseCategory.objects.filter(code="HYDRO").exists())

    def test_currency_snapshot_uses_dated_evidence_and_rejects_missing_rate(self):
        rate = HistoricalExchangeRate.objects.create(
            rate_date=date(2026, 3, 1),
            source_currency="CAD",
            target_currency="BDT",
            rate=Decimal("101"),
            source_name="Approved evidence",
            evidence_reference="RATE-1",
            is_approved=True,
            created_by=self.user,
        )
        rate.rate = Decimal("102")
        with self.assertRaises(ValidationError):
            rate.save()
        snapshot = resolve_currency_snapshot(
            native_amount="20",
            currency="CAD",
            transaction_date=date(2026, 3, 1),
            actor=self.user,
            create_review=False,
        )
        self.assertEqual(snapshot.amount_cad, Decimal("20.00"))
        self.assertEqual(snapshot.amount_bdt, Decimal("2020.00"))
        with self.assertRaises(MissingExchangeRate):
            resolve_currency_snapshot(
                native_amount="10",
                currency="USD",
                transaction_date=date(2026, 3, 2),
                actor=self.user,
                create_review=False,
            )

    def test_balanced_journal_posting_reversal_and_immutability(self):
        journal = create_draft_journal(
            journal_date=date(2026, 1, 10),
            reference="JE-1",
            description="Owner investment",
            side="CA",
            snapshot=self.snapshot(),
            source_key="TEST:JE-1",
            source_record=self.customer,
            actor=self.user,
            lines=(
                JournalLineSpec("CANADIAN_BANK", debit=Decimal("100")),
                JournalLineSpec("OWNER_INVESTMENT", credit=Decimal("100")),
            ),
        )
        journal = post_journal(journal, actor=self.user)
        self.assertEqual(journal.state, JournalEntry.STATE_POSTED)
        journal.description = "Changed"
        with self.assertRaises(ValidationError):
            journal.save()
        reversal = reverse_journal(journal, actor=self.user, reason="Approved correction")
        journal.refresh_from_db()
        self.assertEqual(journal.state, JournalEntry.STATE_REVERSED)
        self.assertEqual(reversal.state, JournalEntry.STATE_POSTED)
        trial = trial_balance(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
        self.assertTrue(trial["is_balanced"])
        with self.assertRaises(JournalError):
            create_draft_journal(
                journal_date=date(2026, 1, 10), reference="BAD", description="Bad", side="CA",
                snapshot=self.snapshot(), source_key="TEST:BAD", actor=self.user,
                lines=(JournalLineSpec("CANADIAN_BANK", debit=100), JournalLineSpec("OWNER_INVESTMENT", credit=90)),
            )

    def test_closed_period_blocks_posting(self):
        self.period.state = FinancialPeriod.STATE_CLOSED
        self.period.save(update_fields=("state", "modified_at"))
        with self.assertRaises(JournalError):
            create_draft_journal(
                journal_date=date(2026, 1, 10), reference="CLOSED", description="Closed", side="CA",
                snapshot=self.snapshot(), source_key="TEST:CLOSED", actor=self.user,
                lines=(JournalLineSpec("CANADIAN_BANK", debit=100), JournalLineSpec("OWNER_INVESTMENT", credit=100)),
            )

    def test_invoice_issue_partial_full_overpayment_credit_and_refund(self):
        invoice = self.invoice()
        state = issue_invoice_to_financial_core(invoice, actor=self.user, rate_to_bdt=Decimal("100"))
        self.assertEqual(state.document_status, InvoiceFinancialState.DOCUMENT_ISSUED)
        self.assertEqual(invoice_outstanding(invoice), Decimal("1000.00"))
        partial = record_customer_receipt(
            customer=self.customer, amount="400", currency="CAD", receipt_date=date(2026, 1, 20),
            payment_account=self.bank, reference="RCPT-1", actor=self.user, invoices=(invoice,),
            rate_to_bdt=Decimal("100"), evidence_reference="BANK-1",
        )
        self.assertEqual(partial["allocated"], Decimal("400.00"))
        self.assertEqual(invoice_outstanding(invoice), Decimal("600.00"))
        full = record_customer_receipt(
            customer=self.customer, amount="650", currency="CAD", receipt_date=date(2026, 1, 21),
            payment_account=self.bank, reference="RCPT-2", actor=self.user, invoices=(invoice,),
            rate_to_bdt=Decimal("100"), evidence_reference="BANK-2",
        )
        self.assertEqual(full["allocated"], Decimal("600.00"))
        self.assertEqual(full["customer_credit"], Decimal("50.00"))
        self.assertEqual(invoice_outstanding(invoice), Decimal("0.00"))
        refund_event, allocation, _journal = record_customer_refund(
            customer=self.customer, amount="100", currency="CAD", refund_date=date(2026, 1, 22),
            payment_account=self.bank, reference="REF-1", evidence_reference="REFUND-DOC", actor=self.user,
            invoice=invoice, rate_to_bdt=Decimal("100"),
        )
        self.assertEqual(refund_event.kind, ReceivableEvent.KIND_REFUND)
        self.assertEqual(allocation.signed_amount, Decimal("-100.00"))
        self.assertEqual(invoice_outstanding(invoice), Decimal("100.00"))

    def test_credit_note_reduces_only_invoice_outstanding(self):
        invoice = self.invoice(number="CORE-INV-CN", total="300")
        issue_invoice_to_financial_core(invoice, actor=self.user, rate_to_bdt=Decimal("100"))
        event, allocation, journal = apply_customer_credit_note(
            invoice=invoice, amount="75", credit_date=date(2026, 1, 12), reference="CN-1",
            evidence_reference="CREDIT-NOTE-1", actor=self.user, rate_to_bdt=Decimal("100"),
        )
        self.assertEqual(event.kind, ReceivableEvent.KIND_CREDIT_NOTE)
        self.assertEqual(allocation.signed_amount, Decimal("75.00"))
        self.assertEqual(journal.state, JournalEntry.STATE_POSTED)
        self.assertEqual(invoice_outstanding(invoice), Decimal("225.00"))

    def test_receivable_reports_use_phase3b_principal_without_projection_state(self):
        invoice = self.invoice(number="CORE-INV-LEDGER", total="125")
        issue_invoice_to_financial_core(invoice, actor=self.user, rate_to_bdt=Decimal("100"))
        InvoiceFinancialState.objects.filter(invoice=invoice).delete()
        aging = accounts_receivable_aging(as_of_date=date(2026, 1, 31))
        self.assertEqual(aging["total_cad"], Decimal("125.00"))
        summary = executive_financial_summary(
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), as_of_date=date(2026, 1, 31)
        )
        self.assertEqual(summary["invoiced_revenue"], Decimal("125.00"))
        self.assertEqual(summary["accounts_receivable"], Decimal("125.00"))

    def test_supplier_bill_partial_full_overpayment_and_credit(self):
        bill = approve_supplier_bill(self.supplier_bill(), actor=self.user)
        bill.total_amount = Decimal("999")
        with self.assertRaises(ValidationError):
            bill.save()
        bill.refresh_from_db()
        first = record_supplier_payment(
            supplier=self.supplier, amount="200", currency="CAD", payment_date=date(2026, 1, 20),
            payment_account=self.bank, reference="SP-1", actor=self.user, bills=(bill,), rate_to_bdt=Decimal("100"),
            evidence_reference="BANK-SP-1",
        )
        bill.refresh_from_db()
        self.assertEqual(first["allocated"], Decimal("200.00"))
        self.assertEqual(bill.remaining_amount, Decimal("300.00"))
        second = record_supplier_payment(
            supplier=self.supplier, amount="350", currency="CAD", payment_date=date(2026, 1, 21),
            payment_account=self.bank, reference="SP-2", actor=self.user, bills=(bill,), rate_to_bdt=Decimal("100"),
            evidence_reference="BANK-SP-2",
        )
        bill.refresh_from_db()
        self.assertEqual(second["supplier_advance"], Decimal("50.00"))
        self.assertEqual(bill.payment_status, SupplierBill.PAYMENT_PAID)
        bill2 = approve_supplier_bill(self.supplier_bill(number="BILL-CREDIT", total="200"), actor=self.user)
        apply_supplier_credit(
            bill=bill2, amount="50", credit_date=date(2026, 1, 25), reference="SC-1",
            evidence_reference="SUPPLIER-CREDIT", actor=self.user, rate_to_bdt=Decimal("100"),
        )
        bill2.refresh_from_db()
        self.assertEqual(bill2.remaining_amount, Decimal("150.00"))

    def test_paid_and_unpaid_expenses_and_recurring_drafts(self):
        paid = ExpenseRecord.objects.create(
            expense_number="EXP-PAID", expense_date=date(2026, 2, 1), vendor=self.supplier,
            category=self.office_rent, side="CA", currency="CAD", amount_before_tax=Decimal("100"),
            tax_amount=Decimal("0"), total_amount=Decimal("100"), rate_to_cad=Decimal("1"),
            rate_to_bdt=Decimal("100"), amount_cad=Decimal("100"), amount_bdt=Decimal("10000"),
            payment_method="bank", payment_account=self.bank, payment_status=ExpenseRecord.PAYMENT_PAID,
            description="Office rent", business_purpose="Office", created_by=self.user,
        )
        paid = approve_expense(paid, actor=self.user)
        self.assertEqual(paid.approval_status, ExpenseRecord.APPROVAL_APPROVED)
        self.assertIsNotNone(paid.journal_id)
        paid.total_amount = Decimal("125")
        with self.assertRaises(ValidationError):
            paid.save()
        paid.refresh_from_db()
        unpaid = ExpenseRecord.objects.create(
            expense_number="EXP-UNPAID", expense_date=date(2026, 2, 2), due_date=date(2026, 2, 20),
            vendor=self.supplier, category=self.office_rent, side="CA", currency="CAD",
            amount_before_tax=Decimal("200"), tax_amount=Decimal("0"), total_amount=Decimal("200"),
            rate_to_cad=Decimal("1"), rate_to_bdt=Decimal("100"), amount_cad=Decimal("200"), amount_bdt=Decimal("20000"),
            payment_status=ExpenseRecord.PAYMENT_UNPAID, description="Factory office rent", business_purpose="Operations",
            created_by=self.user,
        )
        unpaid = approve_expense(unpaid, actor=self.user)
        self.assertIsNotNone(unpaid.supplier_bill_id)
        self.assertEqual(unpaid.supplier_bill.approval_status, SupplierBill.APPROVAL_APPROVED)
        template = RecurringExpenseTemplate.objects.create(
            name="Monthly rent", frequency=RecurringExpenseTemplate.FREQ_MONTHLY, start_date=date(2026, 1, 1),
            expected_amount=Decimal("300"), currency="CAD", vendor=self.supplier, category=self.office_rent,
            side="CA", due_day=5, created_by=self.user,
        )
        drafts = generate_recurring_expense_drafts(through_date=date(2026, 3, 31), actor=self.user)
        self.assertEqual(len(drafts), 3)
        self.assertTrue(all(row.approval_status == ExpenseRecord.APPROVAL_DRAFT for row in drafts))
        self.assertTrue(all(row.payment_status == ExpenseRecord.PAYMENT_UNPAID for row in drafts))
        self.assertEqual(generate_recurring_expense_drafts(through_date=date(2026, 3, 31), actor=self.user), [])

    def test_payroll_posts_department_totals_and_restricts_detail(self):
        batch = PayrollBatch.objects.create(
            reference="PAY-2026-01", period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            side="CA", currency="CAD", created_by=self.user,
        )
        PayrollLine.objects.create(
            batch=batch, employee_reference="PRIVATE-1", base_salary=Decimal("1000"), deductions=Decimal("100"),
            employer_cost=Decimal("50"), net_pay=Decimal("900"), created_by=self.user,
        )
        batch = approve_payroll_batch(batch, actor=self.user, rate_to_bdt=Decimal("100"))
        self.assertEqual(batch.state, PayrollBatch.STATE_APPROVED)
        self.assertFalse(batch.journal.lines.exclude(customer_id__isnull=True, supplier_id__isnull=True).exists())
        private_line = batch.private_lines.get()
        private_line.net_pay = Decimal("901")
        with self.assertRaises(ValidationError):
            private_line.save()
        pay_payroll_batch(
            batch, actor=self.user, payment_date=date(2026, 2, 1), payment_account=self.bank, rate_to_bdt=Decimal("100")
        )
        batch.refresh_from_db()
        self.assertEqual(batch.state, PayrollBatch.STATE_PAID)
        normal = get_user_model().objects.create_user(username="normal", password="x")
        self.assertFalse(can_view_payroll_detail(normal))

    def test_production_cost_and_factory_timeline_snapshot(self):
        opportunity = self.customer.opportunities.create(product_category="Other", product_type="Other")
        order = ProductionOrder.objects.create(title="Core Order", customer=self.customer, opportunity=opportunity)
        bill = approve_supplier_bill(self.supplier_bill(number="BILL-PROD", total="120"), actor=self.user)
        cost = order.financial_costs.create(
            customer=self.customer, opportunity=opportunity, category="FABRIC", estimated_amount=Decimal("100"),
            actual_amount=Decimal("120"), currency="CAD", supplier=self.supplier, supplier_bill=bill,
            cost_date=date(2026, 1, 15), created_by=self.user,
        )
        cost = approve_production_cost(cost, actor=self.user)
        self.assertEqual(cost.approval_status, SupplierBill.APPROVAL_APPROVED)
        cost.actual_amount = Decimal("119")
        with self.assertRaises(ValidationError):
            cost.save()
        quick = QuickCosting.objects.create(
            opportunity=opportunity, buyer_name="Buyer", project_name="Timeline", quantity=100,
            currency="BDT", selling_price_per_piece=Decimal("1000"), target_margin_percent=Decimal("20"),
            created_by=self.user,
        )
        default = FactoryRunningCostDefault.objects.create(
            name="Factory daily", side="BD", currency="BDT", daily_amount=Decimal("10000"),
            effective_from=date(2026, 1, 1), created_by=self.user,
        )
        snapshot = save_estimated_factory_timeline(
            quick, estimated_days=5, daily_default=default, estimated_revenue=Decimal("100000"),
            other_estimated_cost=Decimal("30000"), actor=self.user, target_margin_percent=Decimal("20"),
            approved_minimum_margin_percent=Decimal("10"),
        )
        self.assertEqual(snapshot.estimated_timeline_cost, Decimal("50000.00"))
        quick.status = QuickCosting.STATUS_APPROVED
        quick.approved_by = self.user
        quick.approved_at = timezone.now()
        quick.save(update_fields=("status", "approved_by", "approved_at", "updated_at"))
        snapshot = lock_factory_timeline_for_approval(quick, actor=self.user)
        default.daily_amount = Decimal("20000")
        default.save()
        snapshot = record_actual_factory_timeline(
            snapshot, actual_days=7, actual_revenue=Decimal("90000"), other_actual_cost=Decimal("30000"), actor=self.user
        )
        self.assertEqual(snapshot.actual_timeline_cost, Decimal("70000.00"))
        self.assertEqual(snapshot.daily_factory_cost, Decimal("10000.00"))
        self.assertEqual(snapshot.status, snapshot.STATUS_RED)

    def test_formal_reports_reconcile_and_budget_actual_uses_ledger(self):
        invoice = self.invoice(number="CORE-INV-REPORT", total="1000")
        issue_invoice_to_financial_core(invoice, actor=self.user, rate_to_bdt=Decimal("100"))
        record_customer_receipt(
            customer=self.customer, amount="1000", currency="CAD", receipt_date=date(2026, 1, 20),
            payment_account=self.bank, reference="REPORT-RCPT", actor=self.user, invoices=(invoice,),
            rate_to_bdt=Decimal("100"), evidence_reference="REPORT-BANK",
        )
        bill = approve_supplier_bill(self.supplier_bill(number="REPORT-BILL", total="300"), actor=self.user)
        record_supplier_payment(
            supplier=self.supplier, amount="300", currency="CAD", payment_date=date(2026, 1, 25),
            payment_account=self.bank, reference="REPORT-SP", actor=self.user, bills=(bill,),
            rate_to_bdt=Decimal("100"), evidence_reference="REPORT-SP-BANK",
        )
        pnl = profit_and_loss(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
        self.assertEqual(pnl["revenue"], Decimal("1000.00"))
        self.assertEqual(pnl["net_profit"], Decimal("700.00"))
        trial = trial_balance(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
        self.assertTrue(trial["is_balanced"])
        sheet = balance_sheet(as_of_date=date(2026, 1, 31))
        self.assertTrue(sheet["is_balanced"])
        flow = cash_flow(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
        self.assertTrue(flow["is_reconciled"])
        self.assertEqual(flow["ending_cash"], Decimal("700.00"))
        summary = executive_financial_summary(
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), as_of_date=date(2026, 1, 31)
        )
        self.assertEqual(summary["cash"], Decimal("0.00"))
        self.assertEqual(summary["bank_balance"], Decimal("700.00"))
        self.assertEqual(accounts_receivable_aging(as_of_date=date(2026, 1, 31))["total_cad"], Decimal("0.00"))
        self.assertEqual(accounts_payable_aging(as_of_date=date(2026, 1, 31))["total_cad"], Decimal("0.00"))
        FinancialBudget.objects.create(
            year=2026, month=1, account=account_by_key("PRODUCT_SALES"), currency="CAD", amount=Decimal("900"),
            created_by=self.user,
        )
        budget = budget_vs_actual(year=2026, month=1)
        self.assertEqual(budget["rows"][0]["actual"], Decimal("1000.00"))
        self.assertEqual(budget["rows"][0]["variance"], Decimal("100.00"))

    def test_bank_reconciliation_and_intercompany_transfer(self):
        journal = post_journal(
            create_draft_journal(
                journal_date=date(2026, 4, 1), reference="BANK-BOOK", description="Bank deposit", side="CA",
                snapshot=self.snapshot(amount="250", transaction_date=date(2026, 4, 1)), source_key="TEST:BANK-BOOK",
                source_record=self.customer, actor=self.user,
                lines=(JournalLineSpec("CANADIAN_BANK", debit=250), JournalLineSpec("OWNER_INVESTMENT", credit=250)),
            ), actor=self.user,
        )
        reconciliation = BankReconciliation.objects.create(
            account=self.bank, statement_start_date=date(2026, 4, 1), statement_end_date=date(2026, 4, 30),
            statement_opening_balance=Decimal("0"), statement_closing_balance=Decimal("250"), created_by=self.user,
        )
        statement_line = BankStatementLine.objects.create(
            reconciliation=reconciliation, transaction_date=date(2026, 4, 1), reference="STMT-1", amount=Decimal("250"),
            created_by=self.user,
        )
        bank_line = journal.lines.get(account=account_by_key("CANADIAN_BANK"))
        match_statement_transaction(
            statement_line=statement_line, journal_line=bank_line, matched_amount=Decimal("250"), actor=self.user
        )
        submit_bank_reconciliation(reconciliation, actor=self.user)
        reconciliation = approve_bank_reconciliation(reconciliation, actor=self.user)
        self.assertEqual(reconciliation.state, BankReconciliation.STATE_APPROVED)
        transfer = record_company_transfer(
            source_account=self.bank, destination_account=self.bd_bank, source_amount=Decimal("10"),
            destination_amount=Decimal("1000"), transfer_date=date(2026, 5, 1), reference="XFER-1", actor=self.user,
            source_rate_to_bdt=Decimal("100"), destination_rate_to_cad=Decimal("0.01"),
        )
        self.assertEqual(transfer["difference_cad"], Decimal("0.00"))
        self.assertFalse(
            JournalLine.objects.filter(
                journal__reference__contains="XFER", account__account_type__in=(FinancialAccount.TYPE_REVENUE, FinancialAccount.TYPE_OPERATING_EXPENSE)
            ).exists()
        )
        flow = cash_flow(start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
        self.assertEqual(flow["net_operating"], Decimal("0.00"))
        self.assertTrue(flow["is_reconciled"])

    def test_permission_matrix_hides_sensitive_data(self):
        finance = get_user_model().objects.create_user(username="finance-user", password="x")
        Group.objects.get_or_create(name="Finance")[0].user_set.add(finance)
        finance.access.can_accounting_ca = True
        finance.access.role = "CA"
        finance.access.save()
        hr = get_user_model().objects.create_user(username="hr-user", password="x")
        Group.objects.get_or_create(name="HR")[0].user_set.add(hr)
        normal = get_user_model().objects.create_user(username="staff-user", password="x")
        self.assertTrue(can_manage_financial_transactions(finance))
        self.assertTrue(can_view_sensitive_financials(finance))
        self.assertTrue(can_view_payroll_detail(hr))
        self.assertFalse(can_view_sensitive_financials(hr))
        self.assertFalse(can_manage_financial_transactions(normal))

    def test_executive_dashboard_stays_within_ten_queries(self):
        with CaptureQueriesContext(connection) as queries:
            summary = executive_financial_summary(
                start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), as_of_date=date(2026, 12, 31)
            )
        self.assertLessEqual(len(queries), 9)
        self.assertIn("health_score", summary)
        client = Client()
        client.force_login(self.user)
        client.get(reverse("financial_core_dashboard"))
        with CaptureQueriesContext(connection) as request_queries:
            response = client.get(reverse("financial_core_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(request_queries), 10)

    def test_preview_flag_blocks_direct_financial_core_writes(self):
        client = Client()
        client.force_login(self.user)
        before = Supplier.objects.count()
        response = client.post(reverse("financial_supplier_center"), {"action": "supplier"})
        self.assertRedirects(response, reverse("financial_core_dashboard"))
        self.assertEqual(Supplier.objects.count(), before)
        page = client.get(reverse("financial_supplier_center"))
        self.assertContains(page, "fc-readonly")

    @override_settings(FINANCIAL_CORE_WRITES_ENABLED=True)
    def test_finance_admin_can_add_expense_categories_and_update_one_budget(self):
        client = Client()
        client.force_login(self.user)
        self.assertNotContains(client.get(reverse("financial_expense_center")), "This field is required.")
        response = client.post(
            reverse("financial_expense_center"),
            {
                "action": "category",
                "category-code": "LOCAL_TEST_CATEGORY",
                "category-name": "Local Test Category",
                "category-subcategory": "Verification",
                "category-default_account": account_by_key("ADMINISTRATIVE_EXPENSES").pk,
                "category-is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("financial_expense_center"))
        self.assertTrue(ExpenseCategory.objects.filter(code="LOCAL_TEST_CATEGORY").exists())

        budget_url = reverse("financial_budget_add")
        budget_data = {
            "year": 2026,
            "month": 6,
            "side": "CA",
            "department": "",
            "account": account_by_key("OFFICE_RENT").pk,
            "currency": "CAD",
            "amount": "1000.00",
            "change_reason": "Initial approved budget",
        }
        self.assertRedirects(client.post(budget_url, budget_data), reverse("financial_budget_actual"))
        budget_data.update(amount="1250.00", change_reason="Approved revision")
        self.assertRedirects(client.post(budget_url, budget_data), reverse("financial_budget_actual"))
        budgets = FinancialBudget.objects.filter(year=2026, month=6, side="CA", account=account_by_key("OFFICE_RENT"))
        self.assertEqual(budgets.count(), 1)
        self.assertEqual(budgets.get().amount, Decimal("1250.00"))
        self.assertEqual(budgets.get().created_by, self.user)

    def test_major_preview_pages_render_on_desktop_and_mobile_requests(self):
        client = Client()
        client.force_login(self.user)
        routes = (
            "financial_core_dashboard", "financial_chart", "financial_general_ledger", "financial_trial_balance",
            "financial_core_profit_loss", "financial_core_balance_sheet", "financial_core_cash_flow",
            "financial_core_ar_aging", "financial_core_ap_aging", "financial_budget_actual",
            "financial_expense_center", "financial_supplier_center", "financial_bank_reconciliations",
            "financial_customer_receipts", "financial_recurring_expenses", "financial_currency_center",
            "financial_production_costs", "financial_payroll",
        )
        for route in routes:
            with self.subTest(route=route):
                response = client.get(reverse(route), HTTP_USER_AGENT="Mozilla/5.0 (iPhone; Mobile)")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Finance Dashboard")
