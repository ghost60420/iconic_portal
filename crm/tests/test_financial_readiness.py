from datetime import date
from decimal import Decimal
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.models import (
    AccountingAttachment,
    AccountingEntry,
    Customer,
    Department,
    ExpenseCategory,
    ExpenseRecord,
    FinancialAdjustmentRequest,
    FinancialDocument,
    FinancialExceptionReview,
    Invoice,
    Opportunity,
    PayrollBatch,
)
from crm.services.chart_of_accounts import account_by_key, bootstrap_chart_of_accounts
from crm.services.financial_adjustments import (
    FinancialAdjustmentError,
    decide_adjustment_request,
    post_approved_adjustment,
    submit_adjustment_request,
)
from crm.services.financial_permissions import (
    can_view_bank_details,
    can_view_payroll_detail,
    scope_expenses_for_user,
    scope_invoices_for_user,
)


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="iconic-financial-readiness-")


@override_settings(
    MEDIA_ROOT=TEST_MEDIA_ROOT,
    FINANCIAL_CORE_WRITES_ENABLED=False,
    FINANCIAL_CORE_REPORTING_ACTIVE=False,
)
class FinancialReadinessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ceo = get_user_model().objects.create_superuser(
            username="readiness-ceo", email="ceo@example.com", password="test-pass"
        )
        bootstrap_chart_of_accounts(actor=cls.ceo)
        cls.ca_customer = Customer.objects.create(customer_code="RD-CA", account_brand="Canada Customer")
        cls.bd_customer = Customer.objects.create(customer_code="RD-BD", account_brand="Bangladesh Customer")
        cls.ca_invoice = Invoice.objects.create(
            customer=cls.ca_customer,
            invoice_number="RD-CA-INV",
            issue_date=date(2026, 1, 10),
            due_date=date(2026, 2, 10),
            currency="CAD",
            invoice_region="CA",
            subtotal=Decimal("100"),
            total_amount=Decimal("100"),
            status="sent",
            invoice_status="APPROVED",
        )
        cls.bd_invoice = Invoice.objects.create(
            customer=cls.bd_customer,
            invoice_number="RD-BD-INV",
            issue_date=date(2026, 1, 10),
            due_date=date(2026, 2, 10),
            currency="BDT",
            invoice_region="BD",
            subtotal=Decimal("100"),
            total_amount=Decimal("100"),
            status="sent",
            invoice_status="APPROVED",
        )
        cls.department_a = Department.objects.create(code="ready-a", name="Readiness A")
        cls.department_b = Department.objects.create(code="ready-b", name="Readiness B")
        cls.category = ExpenseCategory.objects.get(code="OFFICE_RENT")

    def user(self, username, role, *, side="CA", ca=True, bd=False):
        user = get_user_model().objects.create_user(username=username, password="test-pass")
        Group.objects.get_or_create(name=role)[0].user_set.add(user)
        access = user.access
        access.role = side
        access.can_accounting_ca = ca
        access.can_accounting_bd = bd
        access.save()
        return user

    def exception(self, identifier="FEX-READY-1", side="CA", severity="CRITICAL"):
        return FinancialExceptionReview.objects.create(
            exception_id=identifier,
            source_key=f"source-{identifier}",
            severity=severity,
            area="Cash and Bank Opening",
            exception_type="BANK_OPENING",
            record_type="BankAccount",
            record_number=identifier,
            transaction_date=date(2026, 1, 1),
            side=side,
            currency="CAD" if side == "CA" else "BDT",
            current_value="No approved opening balance",
            expected_value="Statement-supported opening balance",
            difference="Unknown",
            reason="Evidence is incomplete.",
            evidence_needed="Bank statement",
            recommended_action="Attach evidence and submit an opening balance proposal.",
            review_status=FinancialExceptionReview.STATUS_EVIDENCE_REQUIRED,
            source_snapshot={"record": identifier},
            created_by=self.ceo,
            modified_by=self.ceo,
        )

    def evidence(self, exception):
        document = FinancialDocument(
            source_record=exception,
            document_type="HISTORICAL_EVIDENCE",
            description="Approved statement",
            created_by=self.ceo,
            modified_by=self.ceo,
        )
        document.file.save("statement.txt", ContentFile(b"statement evidence"), save=True)
        return document

    def test_finance_side_scope_blocks_cross_country_invoice_and_dashboard_request(self):
        finance = self.user("finance-ca", "Finance")
        self.assertEqual(list(scope_invoices_for_user(Invoice.objects.all(), finance)), [self.ca_invoice])
        client = Client()
        client.force_login(finance)
        response = client.get(reverse("financial_core_dashboard"), {"side": "BD"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["side"], "CA")
        self.assertEqual(client.get(reverse("invoice_pdf", args=[self.bd_invoice.pk])).status_code, 404)

    def test_salesperson_invoice_scope_only_includes_assigned_opportunities(self):
        sales = self.user("sales-owner", "Sales", ca=False, bd=False)
        owned = Opportunity.objects.create(customer=self.ca_customer, assigned_to=sales, opportunity_id="RD-OWNED")
        other = Opportunity.objects.create(customer=self.ca_customer, opportunity_id="RD-OTHER")
        self.ca_invoice.opportunity = owned
        self.ca_invoice.save(update_fields=("opportunity",))
        self.bd_invoice.opportunity = other
        self.bd_invoice.save(update_fields=("opportunity",))
        self.assertEqual(list(scope_invoices_for_user(Invoice.objects.all(), sales)), [self.ca_invoice])

    def test_manager_expense_scope_is_limited_to_department_and_side(self):
        manager = self.user("expense-manager", "Manager")
        manager.employee_profile.department_ref = self.department_a
        manager.employee_profile.save(update_fields=("department_ref",))
        visible = ExpenseRecord.objects.create(
            expense_number="EXP-A", expense_date=date(2026, 1, 2), vendor_name="A", category=self.category,
            department=self.department_a, side="CA", currency="CAD", amount_before_tax=Decimal("10"),
            tax_amount=Decimal("0"), total_amount=Decimal("10"), rate_to_cad=Decimal("1"),
            rate_to_bdt=Decimal("100"), amount_cad=Decimal("10"), amount_bdt=Decimal("1000"),
            business_purpose="Office", created_by=self.ceo,
        )
        ExpenseRecord.objects.create(
            expense_number="EXP-B", expense_date=date(2026, 1, 2), vendor_name="B", category=self.category,
            department=self.department_b, side="CA", currency="CAD", amount_before_tax=Decimal("20"),
            tax_amount=Decimal("0"), total_amount=Decimal("20"), rate_to_cad=Decimal("1"),
            rate_to_bdt=Decimal("100"), amount_cad=Decimal("20"), amount_bdt=Decimal("2000"),
            business_purpose="Office", created_by=self.ceo,
        )
        self.assertEqual(list(scope_expenses_for_user(ExpenseRecord.objects.all(), manager)), [visible])

    def test_payroll_and_bank_privacy_by_role(self):
        accounts = self.user("accounts-private", "Accounts")
        hr = self.user("hr-private", "HR", ca=False, bd=False)
        sales = self.user("sales-private", "Sales", ca=False, bd=False)
        self.assertFalse(can_view_payroll_detail(accounts))
        self.assertTrue(can_view_payroll_detail(hr))
        self.assertFalse(can_view_bank_details(hr))
        self.assertFalse(can_view_bank_details(sales))
        client = Client()
        client.force_login(accounts)
        self.assertEqual(client.get(reverse("financial_payroll")).status_code, 403)
        client.force_login(sales)
        self.assertEqual(client.get(reverse("financial_currency_center")).status_code, 403)

    def test_exception_center_scopes_side_and_paginates_with_bounded_queries(self):
        finance = self.user("exception-finance", "Finance")
        for index in range(55):
            self.exception(f"FEX-CA-{index:03d}", side="CA", severity="HIGH")
        self.exception("FEX-BD-HIDDEN", side="BD")
        client = Client()
        client.force_login(finance)
        client.get(reverse("financial_exception_center"))
        with CaptureQueriesContext(connection) as queries:
            response = client.get(reverse("financial_exception_center"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page"].object_list), 50)
        self.assertEqual(response.context["page"].paginator.count, 55)
        self.assertNotContains(response, "FEX-BD-HIDDEN")
        self.assertLessEqual(len(queries), 10)

    def test_exception_decision_requires_separate_confirmation(self):
        exception = self.exception()
        client = Client()
        client.force_login(self.ceo)
        response = client.post(
            reverse("financial_exception_decide", args=[exception.pk]),
            {"action": "DEFERRED", "notes": "Awaiting the original bank statement."},
        )
        self.assertEqual(response.status_code, 200)
        exception.refresh_from_db()
        self.assertEqual(exception.review_status, FinancialExceptionReview.STATUS_EVIDENCE_REQUIRED)
        response = client.post(
            reverse("financial_exception_decide", args=[exception.pk]),
            {"confirmation_token": response.context["confirmation_token"]},
        )
        self.assertRedirects(response, reverse("financial_exception_detail", args=[exception.pk]))
        exception.refresh_from_db()
        self.assertEqual(exception.review_status, FinancialExceptionReview.STATUS_DEFERRED)

    def test_evidence_upload_and_document_url_tampering_are_authorized(self):
        ca_entry = AccountingEntry.objects.create(
            date=date(2026, 1, 1), side="CA", direction="IN", currency="CAD", amount_original=Decimal("1"),
            rate_to_cad=Decimal("1"), rate_to_bdt=Decimal("100"),
        )
        bd_entry = AccountingEntry.objects.create(
            date=date(2026, 1, 1), side="BD", direction="IN", currency="BDT", amount_original=Decimal("1"),
            rate_to_cad=Decimal("100"), rate_to_bdt=Decimal("1"),
        )
        ca_file = AccountingAttachment(entry=ca_entry, original_name="ca.txt", uploaded_by=self.ceo)
        ca_file.file.save("ca.txt", ContentFile(b"ca"), save=True)
        bd_file = AccountingAttachment(entry=bd_entry, original_name="bd.txt", uploaded_by=self.ceo)
        bd_file.file.save("bd.txt", ContentFile(b"bd"), save=True)
        finance = self.user("document-finance", "Finance")
        client = Client()
        client.force_login(finance)
        self.assertEqual(client.get(reverse("accounting_attachment_download", args=[ca_file.pk])).status_code, 200)
        self.assertEqual(client.get(reverse("accounting_attachment_download", args=[bd_file.pk])).status_code, 404)

        exception = self.exception("FEX-UPLOAD")
        client.force_login(self.ceo)
        response = client.post(
            reverse("financial_exception_evidence", args=[exception.pk]),
            {"description": "Statement page", "evidence": SimpleUploadedFile("page.txt", b"evidence")},
        )
        self.assertRedirects(response, reverse("financial_exception_detail", args=[exception.pk]))
        self.assertEqual(exception.documents.count(), 1)

    def test_adjustment_requires_evidence_and_independent_approval_without_posting(self):
        exception = self.exception("FEX-ADJUST")
        evidence = self.evidence(exception)
        proposer = self.user("adjust-proposer", "Finance")
        approver = self.user("adjust-approver", "Director")
        values = {
            "request_id": "FADJ-TEST-1",
            "exception": exception,
            "adjustment_type": FinancialAdjustmentRequest.TYPE_OPENING,
            "journal_date": date(2026, 1, 1),
            "side": "CA",
            "currency": "CAD",
            "native_amount": Decimal("100"),
            "rate_to_cad": Decimal("1"),
            "rate_to_bdt": Decimal("100"),
            "debit_account": account_by_key("CANADIAN_BANK"),
            "credit_account": account_by_key("OWNER_INVESTMENT"),
            "reason": "Opening balance supported by the attached statement.",
            "before_values": {"balance": "0.00"},
            "after_values": {"balance": "100.00"},
        }
        with self.assertRaises(FinancialAdjustmentError):
            submit_adjustment_request(actor=proposer, evidence_documents=(), **values)
        proposal = submit_adjustment_request(actor=proposer, evidence_documents=[evidence], **values)
        with self.assertRaises(FinancialAdjustmentError):
            decide_adjustment_request(proposal, actor=proposer, approve=True, notes="Self approval attempt.")
        proposal = decide_adjustment_request(
            proposal, actor=approver, approve=True, notes="Evidence matches the proposed opening balance."
        )
        self.assertEqual(proposal.state, FinancialAdjustmentRequest.STATE_APPROVED)
        self.assertIsNone(proposal.journal_id)
        with self.assertRaises(FinancialAdjustmentError):
            post_approved_adjustment(proposal, actor=approver)

    def test_imported_exception_source_fields_are_immutable(self):
        exception = self.exception("FEX-IMMUTABLE")
        exception.current_value = "Silently changed"
        with self.assertRaises(Exception):
            exception.save()
