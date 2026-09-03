import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.forms import InvoicePaymentForm
from crm.models import (
    AccountingEntry,
    CashBankAccount,
    Customer,
    FinancialAccount,
    FinancialDocument,
    FinancialPeriod,
    Invoice,
    InvoiceFinancialState,
    InvoicePayment,
    JournalEntry,
    JournalLine,
    ReceivableAllocation,
    ReceivableEvent,
)
from crm.services.chart_of_accounts import account_by_key, bootstrap_chart_of_accounts
from crm.services.receivable_accounting import CUSTOMER_PAYMENT_METHOD_CHOICES, issue_invoice_to_financial_core


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="invoice-payment-live-fix-")


@override_settings(
    FINANCIAL_CORE_WRITES_ENABLED=True,
    FINANCIAL_CORE_REPORTING_ACTIVE=True,
    MEDIA_ROOT=TEST_MEDIA_ROOT,
)
class InvoicePaymentLiveFixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ceo = get_user_model().objects.create_superuser(
            username="invoice-payment-ceo",
            email="invoice-payments@example.com",
            password="test-pass",
        )
        cls.employee = get_user_model().objects.create_user(
            username="invoice-payment-employee",
            password="test-pass",
        )
        cls.employee.access.can_accounting_ca = False
        cls.employee.access.can_accounting_bd = False
        cls.employee.access.save(update_fields=("can_accounting_ca", "can_accounting_bd", "updated_at"))
        bootstrap_chart_of_accounts(actor=cls.ceo)
        FinancialPeriod.objects.create(
            name="FY 2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            side="",
            created_by=cls.ceo,
        )
        cls.customer = Customer.objects.create(
            customer_code="PAYMENT-LIVE-CUSTOMER",
            account_brand="Payment Live Customer",
        )
        cls.ca_bank = CashBankAccount.objects.create(
            name="Canada Operating Bank",
            kind=CashBankAccount.KIND_BANK,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("CANADIAN_BANK"),
            created_by=cls.ceo,
        )
        cls.paypal = CashBankAccount.objects.create(
            name="PayPal Canada",
            kind=CashBankAccount.KIND_PAYPAL,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("PAYMENT_ACCOUNTS"),
            created_by=cls.ceo,
        )
        cls.cash = CashBankAccount.objects.create(
            name="Canada Cash",
            kind=CashBankAccount.KIND_CASH,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("CASH_IN_HAND"),
            created_by=cls.ceo,
        )
        cls.bd_bank = CashBankAccount.objects.create(
            name="Bangladesh Operating Bank",
            kind=CashBankAccount.KIND_BANK,
            side="BD",
            currency="BDT",
            gl_account=account_by_key("BANGLADESH_BANK"),
            created_by=cls.ceo,
        )
        cls.invalid_expense_account = CashBankAccount.objects.create(
            name="Invalid Expense Instrument",
            kind=CashBankAccount.KIND_BANK,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("COGS_PRODUCTION_SHIPPING"),
            created_by=cls.ceo,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.client.force_login(self.ceo)

    def invoice(self, number, total="3800.00", *, side="CA", currency="CAD", status="draft"):
        approved = status != "draft"
        return Invoice.objects.create(
            customer=self.customer,
            invoice_number=number,
            issue_date=date(2026, 8, 31),
            due_date=date(2026, 9, 30),
            currency=currency,
            invoice_region=side,
            invoice_market="bangladesh" if side == "BD" else "north_america",
            subtotal=Decimal(total),
            total_amount=Decimal(total),
            paid_amount=Decimal("0"),
            status=status,
            invoice_status="APPROVED" if approved else "DRAFT",
            approved_by=self.ceo if approved else None,
            approved_at=timezone.now() if approved else None,
        )

    def issued_invoice(self, number, total="3800.00"):
        invoice = self.invoice(number, total, status="sent")
        issue_invoice_to_financial_core(invoice, actor=self.ceo, rate_to_bdt=Decimal("100"))
        return invoice

    def payment_data(self, invoice, amount, *, method="e_transfer", account=None, reference="BANK-REF-1"):
        return {
            "payment_date": "2026-08-31",
            "amount": str(amount),
            "currency": invoice.currency,
            "side": invoice.invoice_region,
            "payment_method": method,
            "payment_account": str((account or self.ca_bank).pk),
            "reference": reference,
            "rate_to_cad": "1" if invoice.currency == "CAD" else "100",
            "rate_to_bdt": "100" if invoice.currency == "CAD" else "1",
            "production_order": "",
            "notes": "Invoice payment live fix test",
        }

    def post_payment(self, invoice, amount, **kwargs):
        return self.client.post(
            reverse("invoice_payment_add", args=[invoice.pk]),
            self.payment_data(invoice, amount, **kwargs),
        )

    def revenue_credit_total(self):
        return JournalLine.objects.filter(
            journal__state=JournalEntry.STATE_POSTED,
            account__account_type=FinancialAccount.TYPE_REVENUE,
        ).aggregate(total=Sum("native_credit"))["total"] or Decimal("0")

    def test_add_route_returns_200_and_shows_only_approved_new_methods(self):
        invoice = self.invoice("LIVE-GET-200")

        response = self.client.get(reverse("invoice_payment_add", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["payment_form"].fields["payment_method"].choices),
            list(CUSTOMER_PAYMENT_METHOD_CHOICES),
        )
        for label in ("E Transfer", "PayPal", "Bank Transfer", "Cash"):
            self.assertContains(response, label)
        self.assertContains(response, "Account / Payment Account")
        self.assertContains(response, "Reference")
        self.assertContains(response, "Receipt")
        self.assertContains(
            response,
            "Use this section when recording money received for this specific invoice.",
        )
        self.assertFalse(response.context["payment_form"].fields["reference"].required)
        self.assertTrue(response.context["payment_form"]["submission_token"].value())

    def test_reference_is_optional_and_missing_period_is_shown_inline(self):
        invoice = self.invoice("LIVE-OPTIONAL-REFERENCE", total="50.00")

        without_reference = self.post_payment(invoice, "10.00", reference="")

        self.assertEqual(without_reference.status_code, 302)
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 1)

        outside_period = self.invoice("LIVE-MISSING-PERIOD", total="50.00")
        data = self.payment_data(outside_period, "10.00", reference="PERIOD-BLOCK")
        data["payment_date"] = "2027-01-01"
        blocked = self.client.post(reverse("invoice_payment_add", args=[outside_period.pk]), data)

        self.assertEqual(blocked.status_code, 200)
        self.assertFormError(
            blocked.context["payment_form"],
            "payment_date",
            "No Financial Core period is configured for the journal date and side.",
        )
        self.assertFalse(InvoicePayment.objects.filter(invoice=outside_period).exists())

    def test_repeated_submission_token_cannot_create_a_second_payment_or_journal(self):
        invoice = self.invoice("LIVE-IDEMPOTENT-POST", total="50.00")
        data = self.payment_data(invoice, "10.00", reference="")
        data["submission_token"] = "same-browser-submission-token"
        receipt_journals_before = JournalEntry.objects.filter(
            source_key__startswith="CUSTOMER-RECEIPT:"
        ).count()

        first = self.client.post(reverse("invoice_payment_add", args=[invoice.pk]), data)
        second = self.client.post(reverse("invoice_payment_add", args=[invoice.pk]), data)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 1)
        self.assertEqual(
            JournalEntry.objects.filter(source_key__startswith="CUSTOMER-RECEIPT:").count(),
            receipt_journals_before + 1,
        )

    def test_form_filters_country_currency_and_non_asset_accounts(self):
        ca_invoice = self.invoice("LIVE-CA-FILTER")
        bd_invoice = self.invoice("LIVE-BD-FILTER", side="BD", currency="BDT")

        ca_ids = set(InvoicePaymentForm(invoice=ca_invoice, user=self.ceo).fields["payment_account"].queryset.values_list("pk", flat=True))
        bd_ids = set(InvoicePaymentForm(invoice=bd_invoice, user=self.ceo).fields["payment_account"].queryset.values_list("pk", flat=True))

        self.assertIn(self.ca_bank.pk, ca_ids)
        self.assertIn(self.paypal.pk, ca_ids)
        self.assertIn(self.cash.pk, ca_ids)
        self.assertNotIn(self.bd_bank.pk, ca_ids)
        self.assertNotIn(self.invalid_expense_account.pk, ca_ids)
        self.assertEqual(bd_ids, {self.bd_bank.pk})

    def test_method_requires_matching_account_kind(self):
        invoice = self.invoice("LIVE-METHOD-KIND")
        cases = (
            ("e_transfer", self.ca_bank, True),
            ("bank_transfer", self.ca_bank, True),
            ("paypal", self.paypal, True),
            ("cash", self.cash, True),
            ("paypal", self.ca_bank, False),
            ("cash", self.ca_bank, False),
        )
        for index, (method, account, expected) in enumerate(cases):
            with self.subTest(method=method, account=account.kind):
                form = InvoicePaymentForm(
                    self.payment_data(
                        invoice,
                        "10.00",
                        method=method,
                        account=account,
                        reference=f"METHOD-{index}",
                    ),
                    invoice=invoice,
                    user=self.ceo,
                )
                self.assertEqual(form.is_valid(), expected, form.errors.as_text())

    def test_live_draft_case_posts_customer_deposit_without_revenue(self):
        invoice = self.invoice("INV00049", total="380.00")
        revenue_before = self.revenue_credit_total()
        journal_before = JournalEntry.objects.count()

        response = self.post_payment(invoice, "100.00", reference="LIVE-INV00049")

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("100.00"))
        self.assertEqual(invoice.balance, Decimal("280.00"))
        self.assertEqual(invoice.status, "partial")
        payment = InvoicePayment.objects.get(invoice=invoice)
        event = payment.receivable_event
        self.assertEqual(event.external_reference, "LIVE-INV00049")
        self.assertIsNone(event.source_invoice_id)
        self.assertEqual(event.financial_journal.lines.get(native_debit__gt=0).account.system_key, "CANADIAN_BANK")
        self.assertEqual(event.financial_journal.lines.get(native_credit__gt=0).account.system_key, "CUSTOMER_DEPOSITS")
        self.assertEqual(JournalEntry.objects.count(), journal_before + 1)
        self.assertEqual(self.revenue_credit_total(), revenue_before)
        self.assertFalse(ReceivableAllocation.objects.filter(event=event).exists())

    def test_each_visible_method_posts_only_to_its_selected_account_kind(self):
        cases = (
            ("e_transfer", self.ca_bank, "CANADIAN_BANK"),
            ("paypal", self.paypal, "PAYMENT_ACCOUNTS"),
            ("bank_transfer", self.ca_bank, "CANADIAN_BANK"),
            ("cash", self.cash, "CASH_IN_HAND"),
        )
        for index, (method, account, expected_key) in enumerate(cases):
            with self.subTest(method=method):
                invoice = self.invoice(f"LIVE-METHOD-{index}", total="20.00")
                response = self.post_payment(
                    invoice,
                    "10.00",
                    method=method,
                    account=account,
                    reference=f"VISIBLE-METHOD-{index}",
                )
                self.assertEqual(response.status_code, 302)
                event = InvoicePayment.objects.get(invoice=invoice).receivable_event
                self.assertEqual(
                    event.financial_journal.lines.get(native_debit__gt=0).account.system_key,
                    expected_key,
                )

    def test_bangladesh_payment_uses_bangladesh_account_and_journal_side(self):
        invoice = self.invoice("LIVE-BD-POST", total="1000.00", side="BD", currency="BDT")

        response = self.post_payment(
            invoice,
            "500.00",
            method="bank_transfer",
            account=self.bd_bank,
            reference="BD-PAYMENT",
        )

        self.assertEqual(response.status_code, 302)
        payment = InvoicePayment.objects.get(invoice=invoice)
        self.assertEqual(payment.side, "BD")
        self.assertEqual(payment.receivable_event.financial_journal.side, "BD")
        self.assertEqual(
            payment.receivable_event.financial_journal.lines.get(native_debit__gt=0).account.system_key,
            "BANGLADESH_BANK",
        )

    def test_partial_then_full_payment_updates_ar_once_without_duplicate_revenue(self):
        invoice = self.issued_invoice("LIVE-PARTIAL-FULL")
        revenue_after_issue = self.revenue_credit_total()
        receipt_journals_before = JournalEntry.objects.filter(source_key__startswith="CUSTOMER-RECEIPT:").count()

        partial = self.post_payment(invoice, "500.00", reference="PARTIAL-500")
        self.assertEqual(partial.status_code, 302)
        invoice.refresh_from_db()
        invoice.financial_state.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertEqual(invoice.balance, Decimal("3300.00"))
        self.assertEqual(invoice.status, "partial")
        self.assertEqual(invoice.financial_state.payment_status, InvoiceFinancialState.PAYMENT_PARTIAL)

        full = self.post_payment(invoice, "3300.00", reference="FULL-3300")
        self.assertEqual(full.status_code, 302)
        invoice.refresh_from_db()
        invoice.financial_state.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("3800.00"))
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.financial_state.payment_status, InvoiceFinancialState.PAYMENT_SETTLED)
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 2)
        self.assertEqual(ReceivableEvent.objects.filter(legacy_invoice_payment__invoice=invoice).count(), 2)
        self.assertEqual(
            JournalEntry.objects.filter(source_key__startswith="CUSTOMER-RECEIPT:").count(),
            receipt_journals_before + 2,
        )
        self.assertEqual(self.revenue_credit_total(), revenue_after_issue)
        self.assertEqual(
            JournalLine.objects.filter(
                journal__receivable_event__legacy_invoice_payment__invoice=invoice,
                account__system_key="ACCOUNTS_RECEIVABLE",
            ).aggregate(total=Sum("native_credit"))["total"],
            Decimal("3800.00"),
        )

    def test_overpayment_and_paid_invoice_attempts_write_nothing(self):
        invoice = self.issued_invoice("LIVE-OVERPAY", total="100.00")
        counts_before = (
            InvoicePayment.objects.count(),
            AccountingEntry.objects.count(),
            ReceivableEvent.objects.count(),
            JournalEntry.objects.count(),
        )

        response = self.post_payment(invoice, "100.01", reference="OVERPAY")

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["payment_form"],
            "amount",
            "Payment exceeds the outstanding invoice balance. Use the approved customer credit workflow for any excess.",
        )
        self.assertEqual(
            (
                InvoicePayment.objects.count(),
                AccountingEntry.objects.count(),
                ReceivableEvent.objects.count(),
                JournalEntry.objects.count(),
            ),
            counts_before,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_receipt_is_optional_and_uploaded_receipt_uses_existing_document_model(self):
        without_receipt = self.invoice("LIVE-NO-RECEIPT", total="50.00")
        response = self.post_payment(without_receipt, "25.00", reference="NO-RECEIPT")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FinancialDocument.objects.count(), 0)

        with_receipt = self.invoice("LIVE-WITH-RECEIPT", total="50.00")
        data = self.payment_data(with_receipt, "25.00", reference="WITH-RECEIPT")
        data["receipt"] = SimpleUploadedFile("receipt.pdf", b"receipt evidence", content_type="application/pdf")
        response = self.client.post(reverse("invoice_payment_add", args=[with_receipt.pk]), data)

        self.assertEqual(response.status_code, 302)
        document = FinancialDocument.objects.get()
        self.assertEqual(document.document_type, "CUSTOMER_RECEIPT")
        self.assertEqual(document.description, "WITH-RECEIPT")

    def test_normal_employee_cannot_post_invoice_payment(self):
        invoice = self.invoice("LIVE-PERMISSION")
        self.client.force_login(self.employee)

        response = self.post_payment(invoice, "10.00", reference="DENIED")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())

    def test_historical_method_values_remain_supported(self):
        labels = dict(InvoicePayment.METHOD_CHOICES)
        for code in ("bank", "cheque", "card", "mobile", "other"):
            self.assertIn(code, labels)
