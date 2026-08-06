import csv
import io
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.models import (
    CashBankAccount,
    ExpenseCategory,
    FinancialAccount,
    FinancialPeriod,
    HistoricalExchangeRate,
    InvoiceSettings,
    Supplier,
)
from crm.services.finance_setup_import import FinanceSetupImportError, import_finance_setup_csv
from crm.services.financial_exception_classification import (
    CLASS_AUTO_FIX,
    CLASS_CEO_REVIEW,
    classify_exception,
)


FIELDS = (
    "record_type", "code", "name", "account_type", "normal_balance", "system_key",
    "parent_system_key", "subtype", "is_control_account", "allow_manual_posting",
    "is_sensitive", "is_active", "description", "kind", "side", "currency",
    "gl_account_system_key", "institution_name", "masked_reference", "default_currency",
    "contact_name", "email", "phone", "tax_identifier", "subcategory",
    "default_account_system_key", "is_production_cost", "rate_date", "source_currency",
    "target_currency", "rate", "source_name", "evidence_reference", "is_approved",
    "start_date", "end_date", "state", "company_name", "default_tax_note",
)


def setup_rows(*, supplier_name="Approved Supplier"):
    return [
        {
            "record_type": "financial_account", "code": "1000", "name": "Assets",
            "account_type": "ASSET", "normal_balance": "DEBIT", "system_key": "ASSETS",
            "allow_manual_posting": "true", "is_active": "true",
        },
        {
            "record_type": "financial_account", "code": "1020", "name": "Canadian Bank",
            "account_type": "ASSET", "normal_balance": "DEBIT", "system_key": "CANADIAN_BANK",
            "parent_system_key": "ASSETS", "subtype": "BANK", "is_control_account": "true",
            "allow_manual_posting": "true", "is_sensitive": "true", "is_active": "true",
        },
        {
            "record_type": "financial_account", "code": "6000", "name": "Operating Expenses",
            "account_type": "OPERATING_EXPENSE", "normal_balance": "DEBIT",
            "system_key": "OPERATING_EXPENSES", "allow_manual_posting": "true",
            "is_active": "true",
        },
        {
            "record_type": "cash_bank_account", "name": "Canada Operating",
            "kind": "BANK", "side": "CA", "currency": "CAD",
            "gl_account_system_key": "CANADIAN_BANK", "institution_name": "Approved Bank",
            "masked_reference": "****1234", "is_active": "true",
        },
        {
            "record_type": "supplier", "code": "SUP-001", "name": supplier_name,
            "side": "CA", "default_currency": "CAD", "contact_name": "Accounts",
            "email": "accounts@example.com", "is_active": "true",
        },
        {
            "record_type": "expense_category", "code": "OFFICE", "name": "Office",
            "default_account_system_key": "OPERATING_EXPENSES", "is_active": "true",
        },
        {
            "record_type": "exchange_rate", "rate_date": "2026-08-01",
            "source_currency": "USD", "target_currency": "CAD", "rate": "1.3700000000",
            "source_name": "Approved source", "evidence_reference": "FX-2026-08-01",
            "is_approved": "true",
        },
        {
            "record_type": "financial_period", "name": "August 2026",
            "start_date": "2026-08-01", "end_date": "2026-08-31", "side": "CA",
            "state": "OPEN",
        },
        {
            "record_type": "tax_setting", "company_name": "Iconic Apparel House Inc.",
            "default_tax_note": "Apply only the tax supported by the approved invoice evidence.",
        },
    ]


def setup_file(*, supplier_name="Approved Supplier"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(setup_rows(supplier_name=supplier_name))
    return SimpleUploadedFile("approved-finance-setup.csv", stream.getvalue().encode("utf-8"), "text/csv")


class FinanceSetupImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ceo = get_user_model().objects.create_superuser(
            username="finance-setup-ceo",
            email="finance-setup@example.com",
            password="test-pass",
        )

    def test_preview_validates_every_record_and_rolls_back(self):
        result = import_finance_setup_csv(
            uploaded_file=setup_file(),
            actor=self.ceo,
            approval_reference="CEO-APPROVAL-001",
            apply=False,
        )

        self.assertEqual(result["mode"], "preview")
        self.assertEqual(result["row_count"], 9)
        self.assertEqual(sum(result["created"].values()), 9)
        self.assertEqual(FinancialAccount.objects.count(), 0)
        self.assertEqual(InvoiceSettings.objects.count(), 0)

    def test_apply_creates_approved_master_records_and_is_idempotent(self):
        result = import_finance_setup_csv(
            uploaded_file=setup_file(),
            actor=self.ceo,
            approval_reference="CEO-APPROVAL-001",
            apply=True,
        )

        self.assertEqual(sum(result["created"].values()), 9)
        self.assertEqual(FinancialAccount.objects.count(), 3)
        self.assertEqual(CashBankAccount.objects.count(), 1)
        self.assertEqual(Supplier.objects.count(), 1)
        self.assertEqual(ExpenseCategory.objects.count(), 1)
        self.assertEqual(HistoricalExchangeRate.objects.filter(is_approved=True).count(), 1)
        self.assertEqual(FinancialPeriod.objects.filter(state="OPEN").count(), 1)
        self.assertEqual(InvoiceSettings.objects.filter(is_active=True).count(), 1)
        account = FinancialAccount.objects.get(system_key="CANADIAN_BANK")
        self.assertEqual(account.approved_by, self.ceo)
        self.assertIn("CEO-APPROVAL-001", account.change_reason)

        repeated = import_finance_setup_csv(
            uploaded_file=setup_file(),
            actor=self.ceo,
            approval_reference="CEO-APPROVAL-001",
            apply=True,
        )
        self.assertEqual(repeated["created"], {})
        self.assertEqual(sum(repeated["unchanged"].values()), 9)

    def test_conflicting_existing_record_stops_the_whole_import(self):
        import_finance_setup_csv(
            uploaded_file=setup_file(),
            actor=self.ceo,
            approval_reference="CEO-APPROVAL-001",
            apply=True,
        )

        with self.assertRaisesRegex(FinanceSetupImportError, "supplier SUP-001 conflicts"):
            import_finance_setup_csv(
                uploaded_file=setup_file(supplier_name="Different Supplier"),
                actor=self.ceo,
                approval_reference="CEO-APPROVAL-002",
                apply=True,
            )
        self.assertEqual(Supplier.objects.get(code="SUP-001").name, "Approved Supplier")

    def test_readiness_uses_existing_page_and_stays_within_query_budget(self):
        client = Client()
        client.force_login(self.ceo)
        client.get(reverse("finance_live_readiness"))
        with CaptureQueriesContext(connection) as queries:
            response = client.get(reverse("finance_live_readiness"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production Finance Setup Checklist")
        for label in (
            "Chart of Accounts", "Bank Accounts", "Cash Accounts", "Suppliers",
            "Customers verification", "Expense Categories", "Payment Methods", "Exchange Rates",
            "Accounting Periods", "Tax Settings", "Opening Balances",
        ):
            self.assertContains(response, label)
        self.assertLessEqual(len(queries), 10)

    def test_readiness_import_preview_does_not_save_records(self):
        client = Client()
        client.force_login(self.ceo)
        response = client.post(
            reverse("finance_live_readiness"),
            {
                "data_file": setup_file(),
                "approval_reference": "CEO-APPROVAL-001",
                "mode": "preview",
                "confirmation": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["import_result"]["row_count"], 9)
        self.assertEqual(FinancialAccount.objects.count(), 0)

    def test_readiness_import_apply_requires_exact_confirmation(self):
        client = Client()
        client.force_login(self.ceo)
        rejected = client.post(
            reverse("finance_live_readiness"),
            {
                "data_file": setup_file(),
                "approval_reference": "CEO-APPROVAL-001",
                "mode": "apply",
                "confirmation": "IMPORT",
            },
        )

        self.assertEqual(rejected.status_code, 200)
        self.assertFormError(
            rejected.context["import_form"],
            "confirmation",
            "Enter exactly: IMPORT APPROVED FINANCE MASTER DATA",
        )
        self.assertEqual(FinancialAccount.objects.count(), 0)

        applied = client.post(
            reverse("finance_live_readiness"),
            {
                "data_file": setup_file(),
                "approval_reference": "CEO-APPROVAL-001",
                "mode": "apply",
                "confirmation": "IMPORT APPROVED FINANCE MASTER DATA",
            },
        )

        self.assertRedirects(applied, reverse("finance_live_readiness"))
        self.assertEqual(FinancialAccount.objects.count(), 3)

    def test_exception_classifier_only_auto_fixes_the_approved_duplicate(self):
        duplicate_payment = SimpleNamespace(
            record="Invoice INV00019",
            current_value="350.00 CAD; exception payment_exceeds_unallocated_balance",
        )
        duplicate_invoice = SimpleNamespace(
            record="Invoice INV00019",
            current_value="350.00 CAD; exception tracked_payments_exceed_stored_paid",
        )
        evidence_required = SimpleNamespace(
            record="Invoice INV00002",
            current_value="850.00 BDT; exception untracked_paid_balance",
        )

        self.assertEqual(classify_exception(duplicate_payment), CLASS_AUTO_FIX)
        self.assertEqual(classify_exception(duplicate_invoice), CLASS_AUTO_FIX)
        self.assertEqual(classify_exception(evidence_required), CLASS_CEO_REVIEW)
