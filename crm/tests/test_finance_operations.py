from datetime import date
from decimal import Decimal
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.forms_finance_operations import (
    CustomerPaymentOperationForm,
    MoneyTransferOperationForm,
    TRANSFER_TYPE_BD_TO_CA,
    TRANSFER_TYPE_CA_TO_BD,
    TRANSFER_TYPE_INTERNAL,
)
from crm.models import (
    CashBankAccount,
    Customer,
    Department,
    ExpenseCategory,
    ExpenseRecord,
    FactoryRunningCostDefault,
    FinanceOperation,
    FinancialAccount,
    FinancialDocument,
    FinancialPeriod,
    Invoice,
    InventoryItem,
    InventoryMovement,
    JournalEntry,
    JournalLine,
    PayrollBatch,
    ProductionOrder,
    QuickCosting,
    ReceivableEvent,
    Supplier,
    SupplierBill,
)
from crm.services.chart_of_accounts import account_by_key, bootstrap_chart_of_accounts
from crm.services.finance_operations import (
    FinanceOperationError,
    FinanceOperationWritesDisabled,
    WORKFLOWS,
    build_posting_preview,
    post_operation,
    review_operation,
    submit_operation,
    workflow_definition,
)
from crm.services.financial_permissions import (
    can_submit_finance_operation,
    scope_finance_operations_for_user,
)
from crm.services.factory_timeline import save_estimated_factory_timeline
from crm.services.receivable_accounting import issue_invoice_to_financial_core


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="iconic-finance-operations-")


@override_settings(
    MEDIA_ROOT=TEST_MEDIA_ROOT,
    FINANCIAL_CORE_WRITES_ENABLED=False,
    FINANCIAL_CORE_REPORTING_ACTIVE=False,
)
class FinanceOperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.submitter = get_user_model().objects.create_superuser(
            username="ops-submitter", email="submitter@example.com", password="test-pass"
        )
        cls.approver = get_user_model().objects.create_superuser(
            username="ops-approver", email="approver@example.com", password="test-pass"
        )
        bootstrap_chart_of_accounts(actor=cls.submitter)
        cls.period = FinancialPeriod.objects.create(
            name="Finance operations 2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            side="",
            created_by=cls.submitter,
        )
        cls.department = Department.objects.create(code="ops-finance", name="Operations Finance")
        cls.customer = Customer.objects.create(customer_code="OPS-CUST", account_brand="Operations Customer")
        cls.supplier = Supplier.objects.create(
            code="OPS-SUP",
            name="Operations Supplier",
            side="CA",
            default_currency="CAD",
            created_by=cls.submitter,
        )
        cls.bank = CashBankAccount.objects.create(
            name="Operations Bank",
            kind=CashBankAccount.KIND_BANK,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("CANADIAN_BANK"),
            masked_reference="****9001",
            created_by=cls.submitter,
        )
        cls.cash = CashBankAccount.objects.create(
            name="Operations Cash",
            kind=CashBankAccount.KIND_CASH,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("CASH_IN_HAND"),
            masked_reference="Cash drawer",
            created_by=cls.submitter,
        )
        cls.bd_bank = CashBankAccount.objects.create(
            name="Bangladesh Operations Bank",
            kind=CashBankAccount.KIND_BANK,
            side="BD",
            currency="BDT",
            gl_account=account_by_key("BANGLADESH_BANK"),
            masked_reference="****9002",
            created_by=cls.submitter,
        )
        cls.office_rent = ExpenseCategory.objects.get(code="OFFICE_RENT")
        cls.hydro = ExpenseCategory.objects.get(code="HYDRO")
        cls.inventory_item = InventoryItem.objects.create(
            name="Operations Fabric",
            category="fabric_roll",
            quantity=Decimal("100"),
            unit_cost=Decimal("10"),
        )
        cls.opportunity = cls.customer.opportunities.create(
            opportunity_id="OPS-OPP", product_category="Other", product_type="Other"
        )
        cls.production_order = ProductionOrder.objects.create(
            title="Finance Operations Production",
            customer=cls.customer,
            opportunity=cls.opportunity,
            factory_location="ca",
        )
        cls.quick_costing = QuickCosting.objects.create(
            opportunity=cls.opportunity,
            buyer_name="Operations Buyer",
            project_name="Factory Daily Cost",
            quantity=100,
            currency="BDT",
            selling_price_per_piece=Decimal("1000"),
            target_margin_percent=Decimal("20"),
            status=QuickCosting.STATUS_APPROVED,
            approved_by=cls.approver,
            approved_at=timezone.now(),
            created_by=cls.submitter,
        )
        cls.factory_default = FactoryRunningCostDefault.objects.create(
            name="Approved daily factory rate",
            side="BD",
            currency="BDT",
            daily_amount=Decimal("10000"),
            effective_from=date(2026, 1, 1),
            created_by=cls.submitter,
            approved_by=cls.approver,
            approved_at=timezone.now(),
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def invoice(self, number, total="100.00"):
        invoice = Invoice.objects.create(
            customer=self.customer,
            opportunity=self.opportunity,
            invoice_number=number,
            issue_date=date(2026, 2, 1),
            invoice_date=date(2026, 2, 1),
            due_date=date(2026, 3, 1),
            currency="CAD",
            invoice_region="CA",
            invoice_market="north_america",
            subtotal=Decimal(total),
            total_amount=Decimal(total),
            status="sent",
            invoice_status="APPROVED",
            approved_by=self.approver,
            approved_at=timezone.now(),
        )
        issue_invoice_to_financial_core(invoice, actor=self.approver, rate_to_bdt=Decimal("100"))
        return invoice

    def operation(self, operation_type, amount="100.00", reference=None, **fields):
        number = FinanceOperation.objects.count() + 1
        operation = FinanceOperation(
            operation_type=operation_type,
            transaction_date=fields.pop("transaction_date", date(2026, 2, 15)),
            side=fields.pop("side", "CA"),
            currency=fields.pop("currency", "CAD"),
            amount_before_tax=fields.pop("amount_before_tax", Decimal("0")),
            tax_amount=fields.pop("tax_amount", Decimal("0")),
            total_amount=Decimal(amount),
            rate_to_cad=fields.pop("rate_to_cad", Decimal("1")),
            rate_to_bdt=fields.pop("rate_to_bdt", Decimal("100")),
            amount_cad=Decimal("0"),
            amount_bdt=Decimal("0"),
            reference=reference or f"OPS-REF-{number}",
            business_purpose=fields.pop("business_purpose", "Verified business purpose"),
            **fields,
        )
        return submit_operation(operation, actor=self.submitter)

    def evidence(self, operation, name="evidence.txt"):
        document = FinancialDocument(
            source_record=operation,
            document_type="FINANCE_OPERATION_EVIDENCE",
            description="Verified supporting evidence",
            created_by=self.submitter,
            modified_by=self.submitter,
        )
        document.file.save(name, ContentFile(b"verified evidence"), save=True)
        return document

    def approve(self, operation):
        self.evidence(operation, f"{operation.pk}.txt")
        return review_operation(
            operation,
            actor=self.approver,
            action="APPROVE",
            notes="Evidence and posting preview verified.",
        )

    def supplier_bill(self, number="OPS-BASE-BILL", amount="100.00"):
        operation = self.operation(
            FinanceOperation.TYPE_SUPPLIER_BILL,
            amount,
            reference=number,
            amount_before_tax=Decimal(amount),
            supplier=self.supplier,
            expense_category=self.office_rent,
            department=self.department,
            details={"due_date": "2026-03-15", "cost_classification": "OPERATING"},
        )
        operation = self.approve(operation)
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            operation = post_operation(operation, actor=self.approver)
        return SupplierBill.objects.get(pk=operation.source_object_id)

    def user(self, username, role, *, side="CA", ca=True, bd=False):
        user = get_user_model().objects.create_user(username=username, password="test-pass")
        Group.objects.get_or_create(name=role)[0].user_set.add(user)
        access = user.access
        access.role = side
        access.can_accounting_ca = ca
        access.can_accounting_bd = bd
        access.save()
        return user

    def test_all_daily_workflow_routes_render_without_posting(self):
        client = Client()
        client.force_login(self.submitter)
        self.assertEqual(client.get(reverse("finance_operations_center")).status_code, 200)
        for slug, _operation_type, title, _icon, _group in WORKFLOWS:
            with self.subTest(slug=slug):
                response = client.get(reverse("finance_operation_create", args=[slug]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, title)
        self.assertEqual(JournalEntry.objects.filter(source_key__startswith="FINANCE-OPERATION:").count(), 0)

    def test_shared_optional_fields_and_draft_controls_render_for_every_workflow(self):
        client = Client()
        client.force_login(self.submitter)
        for slug, _operation_type, _title, _icon, _group in WORKFLOWS:
            with self.subTest(slug=slug):
                response = client.get(reverse("finance_operation_create", args=[slug]))
                form = response.context["form"]
                self.assertFalse(form.fields["supporting_document"].required)
                self.assertFalse(form.fields["reference"].required)
                self.assertFalse(form.fields["business_purpose"].required)
                self.assertFalse(form.fields["notes"].required)
                self.assertContains(response, "Save Draft")
                self.assertContains(response, "* Required before submission")
                self.assertContains(response, "Receipt and supporting documents are optional.")

    def test_requested_workflows_save_draft_without_receipt_and_do_not_post(self):
        self.submitter.access.role = "CA"
        self.submitter.access.save(update_fields=("role",))
        self.submitter.employee_profile.department_ref = self.department
        self.submitter.employee_profile.save(update_fields=("department_ref",))
        invoice = self.invoice("OPS-DRAFT-PAYMENT")
        supplier_bill = self.supplier_bill("OPS-DRAFT-SUPPLIER-PAYMENT", "100")
        common = {
            "transaction_date": "2026-02-20",
            "side": "CA",
            "currency": "CAD",
            "rate_to_cad": "1",
            "rate_to_bdt": "100",
            "action": "draft",
        }
        workflows = (
            (
                "customer-payment",
                FinanceOperation.TYPE_CUSTOMER_PAYMENT,
                {
                    "customer": self.customer.pk,
                    "invoice": invoice.pk,
                    "amount": "25.00",
                    "payment_method": "bank",
                    "payment_account": self.bank.pk,
                },
            ),
            (
                "supplier-bill",
                FinanceOperation.TYPE_SUPPLIER_BILL,
                {
                    "supplier": self.supplier.pk,
                    "bill_date": "2026-02-20",
                    "due_date": "2026-03-20",
                    "amount_before_tax": "80.00",
                    "tax": "0",
                    "total": "80.00",
                    "category": self.office_rent.pk,
                },
            ),
            (
                "supplier-payment",
                FinanceOperation.TYPE_SUPPLIER_PAYMENT,
                {
                    "supplier": self.supplier.pk,
                    "supplier_bill": supplier_bill.pk,
                    "amount": "20.00",
                    "payment_method": "bank",
                    "payment_account": self.bank.pk,
                },
            ),
            (
                "expense",
                FinanceOperation.TYPE_COMPANY_EXPENSE,
                {
                    "vendor": self.supplier.pk,
                    "category": self.office_rent.pk,
                    "amount_before_tax": "60.00",
                    "tax": "0",
                    "total": "60.00",
                    "payment_status": "PAID",
                    "payment_method": "bank",
                    "payment_account": self.bank.pk,
                },
            ),
            (
                "utility",
                FinanceOperation.TYPE_UTILITY_BILL,
                {
                    "utility_type": "HYDRO",
                    "vendor": self.supplier.pk,
                    "bill_date": "2026-02-20",
                    "due_date": "2026-03-20",
                    "amount": "50.00",
                    "location": "OFFICE",
                    "payment_status": "UNPAID",
                },
            ),
            (
                "payroll",
                FinanceOperation.TYPE_PAYROLL,
                {
                    "payroll_month": "2026-02",
                    "department": self.department.pk,
                    "payroll_type": "BASE",
                    "gross_amount": "100.00",
                    "net_paid": "100.00",
                    "payment_account": self.bank.pk,
                },
            ),
            (
                "production-cost",
                FinanceOperation.TYPE_PRODUCTION_COST,
                {
                    "production_order": self.production_order.pk,
                    "cost_category": "FABRIC",
                    "supplier": self.supplier.pk,
                    "estimated_cost": "75.00",
                    "actual_cost": "80.00",
                    "payment_status": "UNPAID",
                },
            ),
        )
        protected_counts = {
            "journals": JournalEntry.objects.count(),
            "receivables": ReceivableEvent.objects.count(),
            "supplier_bills": SupplierBill.objects.count(),
            "expenses": ExpenseRecord.objects.count(),
            "payroll": PayrollBatch.objects.count(),
            "inventory": InventoryMovement.objects.count(),
        }
        client = Client()
        client.force_login(self.submitter)
        created = []
        for slug, operation_type, values in workflows:
            with self.subTest(slug=slug):
                response = client.post(
                    f"{reverse('finance_operation_create', args=[slug])}?side=CA",
                    {**common, **values},
                    follow=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Draft saved successfully.")
                operation = FinanceOperation.objects.filter(operation_type=operation_type).latest("pk")
                created.append(operation.pk)
                self.assertEqual(operation.state, FinanceOperation.STATE_DRAFT)
                self.assertEqual(operation.documents.count(), 0)
                self.assertEqual(operation.reference, "")
                self.assertEqual(operation.business_purpose, "")
        self.assertEqual(len(created), 7)
        self.assertEqual(JournalEntry.objects.count(), protected_counts["journals"])
        self.assertEqual(ReceivableEvent.objects.count(), protected_counts["receivables"])
        self.assertEqual(SupplierBill.objects.count(), protected_counts["supplier_bills"])
        self.assertEqual(ExpenseRecord.objects.count(), protected_counts["expenses"])
        self.assertEqual(PayrollBatch.objects.count(), protected_counts["payroll"])
        self.assertEqual(InventoryMovement.objects.count(), protected_counts["inventory"])

    def test_customer_payment_without_receipt_requires_confirmation_then_submits(self):
        invoice = self.invoice("OPS-NO-DOCUMENT-PAYMENT")
        payload = {
            "transaction_date": "2026-02-21",
            "side": "CA",
            "currency": "CAD",
            "rate_to_cad": "1",
            "rate_to_bdt": "100",
            "customer": self.customer.pk,
            "invoice": invoice.pk,
            "amount": "30.00",
            "payment_method": "bank",
            "payment_account": self.bank.pk,
            "action": "submit",
        }
        client = Client()
        client.force_login(self.submitter)
        before = FinanceOperation.objects.count()
        warning = client.post(
            f"{reverse('finance_operation_create', args=['customer-payment'])}?side=CA",
            payload,
        )
        self.assertEqual(warning.status_code, 200)
        self.assertContains(warning, "No supporting document attached.")
        self.assertContains(warning, "Continue with approval?")
        self.assertEqual(FinanceOperation.objects.count(), before)

        submitted = client.post(
            f"{reverse('finance_operation_create', args=['customer-payment'])}?side=CA",
            {**payload, "confirm_missing_document": "yes"},
        )
        self.assertEqual(submitted.status_code, 302)
        operation = FinanceOperation.objects.latest("pk")
        self.assertEqual(operation.state, FinanceOperation.STATE_PENDING)
        self.assertEqual(operation.documents.count(), 0)
        self.assertFalse(JournalEntry.objects.filter(source_key=f"FINANCE-OPERATION:{operation.pk}").exists())

        client.force_login(self.approver)
        review = client.get(reverse("finance_operation_review", args=[operation.pk, "approve"]))
        self.assertContains(review, "Supporting Document: Missing")
        approved = review_operation(
            operation,
            actor=self.approver,
            action="APPROVE",
            notes="Approved without evidence after review.",
        )
        self.assertEqual(approved.state, FinanceOperation.STATE_APPROVED)
        self.assertFalse(JournalEntry.objects.filter(source_key=f"FINANCE-OPERATION:{operation.pk}").exists())

    def test_draft_can_be_edited_and_returned_for_more_information(self):
        invoice = self.invoice("OPS-EDIT-DRAFT")
        payload = {
            "transaction_date": "2026-02-22",
            "side": "CA",
            "currency": "CAD",
            "rate_to_cad": "1",
            "rate_to_bdt": "100",
            "customer": self.customer.pk,
            "invoice": invoice.pk,
            "amount": "10.00",
            "payment_method": "bank",
            "payment_account": self.bank.pk,
            "action": "draft",
        }
        client = Client()
        client.force_login(self.submitter)
        saved = client.post(
            f"{reverse('finance_operation_create', args=['customer-payment'])}?side=CA",
            payload,
        )
        self.assertEqual(saved.status_code, 302)
        draft = FinanceOperation.objects.latest("pk")
        edit_url = (
            f"{reverse('finance_operation_create', args=['customer-payment'])}"
            f"?side=CA&draft={draft.pk}"
        )
        edit = client.get(edit_url)
        self.assertEqual(edit.context["form"]["amount"].value(), "10.00")
        updated = client.post(
            edit_url,
            {**payload, "draft_id": draft.pk, "amount": "15.00"},
        )
        self.assertEqual(updated.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.total_amount, Decimal("15.00"))
        self.assertEqual(FinanceOperation.objects.filter(pk=draft.pk).count(), 1)

        submitted = client.post(
            edit_url,
            {
                **payload,
                "draft_id": draft.pk,
                "amount": "15.00",
                "action": "submit",
                "confirm_missing_document": "yes",
            },
        )
        self.assertEqual(submitted.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.state, FinanceOperation.STATE_PENDING)
        returned = review_operation(
            draft,
            actor=self.approver,
            action="EVIDENCE_REQUIRED",
            notes="Please provide more information about this payment.",
        )
        self.assertEqual(returned.state, FinanceOperation.STATE_EVIDENCE_REQUIRED)

    def test_country_centers_use_the_shared_workflows_with_locked_links(self):
        client = Client()
        client.force_login(self.submitter)
        canada = client.get(f"{reverse('finance_operations_center')}?side=CA")
        self.assertEqual(canada.status_code, 200)
        self.assertContains(canada, "Canada Daily Transactions")
        self.assertEqual(len(canada.context["cards"]), 15)
        self.assertContains(
            canada,
            f"{reverse('finance_operation_create', args=['utility'])}?side=CA",
        )
        self.assertNotContains(canada, "Record Production Cost")
        self.assertNotContains(canada, "Record Inventory Adjustment")

        bangladesh = client.get(f"{reverse('finance_operations_center')}?side=BD")
        self.assertEqual(bangladesh.status_code, 200)
        self.assertContains(bangladesh, "Bangladesh Daily Transactions")
        self.assertContains(bangladesh, "Add Production Cost")
        self.assertContains(bangladesh, "Add Factory Daily Cost")
        self.assertContains(bangladesh, "Record Inventory Adjustment")

    def test_country_forms_filter_suppliers_accounts_invoices_and_default_currency(self):
        cad_factory_costing = QuickCosting.objects.create(
            buyer_name="CAD Factory Buyer",
            project_name="CAD Bangladesh Factory Cost",
            quantity=100,
            currency="CAD",
            selling_price_per_piece=Decimal("10"),
            status=QuickCosting.STATUS_APPROVED,
            approved_by=self.approver,
            approved_at=timezone.now(),
            created_by=self.submitter,
        )
        bd_supplier = Supplier.objects.create(
            code="OPS-BD-SUP",
            name="Bangladesh Operations Supplier",
            side="BD",
            default_currency="BDT",
            created_by=self.submitter,
        )
        ca_invoice = self.invoice("OPS-CA-FILTER")
        bd_customer = Customer.objects.create(
            customer_code="OPS-BD-CUST", account_brand="Bangladesh Customer", market="BD"
        )
        bd_invoice = Invoice.objects.create(
            customer=bd_customer,
            invoice_number="OPS-BD-FILTER",
            issue_date=date(2026, 2, 1),
            invoice_date=date(2026, 2, 1),
            due_date=date(2026, 3, 1),
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            subtotal=Decimal("100"),
            total_amount=Decimal("100"),
            status="sent",
            invoice_status="APPROVED",
            approved_by=self.approver,
            approved_at=timezone.now(),
        )
        issue_invoice_to_financial_core(
            bd_invoice,
            actor=self.approver,
            rate_to_cad=Decimal("100"),
            rate_to_bdt=Decimal("1"),
        )
        client = Client()
        client.force_login(self.submitter)

        ca_utility = client.get(f"{reverse('finance_operation_create', args=['utility'])}?side=CA").context["form"]
        self.assertEqual(ca_utility.locked_side, "CA")
        self.assertEqual(ca_utility.fields["side"].widget.input_type, "hidden")
        self.assertEqual(ca_utility.fields["currency"].initial, "CAD")
        self.assertQuerySetEqual(ca_utility.fields["vendor"].queryset, [self.supplier])
        self.assertQuerySetEqual(ca_utility.fields["payment_account"].queryset, [self.bank, self.cash])

        bd_bill = client.get(f"{reverse('finance_operation_create', args=['supplier-bill'])}?side=BD").context["form"]
        self.assertEqual(bd_bill.fields["currency"].initial, "BDT")
        self.assertQuerySetEqual(bd_bill.fields["supplier"].queryset, [bd_supplier])

        bd_factory_response = client.get(
            f"{reverse('finance_operation_create', args=['factory-daily-cost'])}?side=BD"
        )
        self.assertEqual(bd_factory_response.status_code, 200)
        self.assertQuerySetEqual(
            bd_factory_response.context["form"].fields["quick_costing"].queryset,
            [cad_factory_costing, self.quick_costing],
        )

        ca_payment = client.get(
            f"{reverse('finance_operation_create', args=['customer-payment'])}?side=CA"
        ).context["form"]
        self.assertQuerySetEqual(ca_payment.fields["invoice"].queryset, [ca_invoice])
        bd_payment = client.get(
            f"{reverse('finance_operation_create', args=['customer-payment'])}?side=BD"
        ).context["form"]
        self.assertQuerySetEqual(bd_payment.fields["invoice"].queryset, [bd_invoice])

    def test_country_query_overwrites_tampered_side_before_submission(self):
        client = Client()
        client.force_login(self.submitter)
        response = client.post(
            f"{reverse('finance_operation_create', args=['utility'])}?side=CA",
            {
                "transaction_date": "2026-02-10",
                "side": "BD",
                "currency": "CAD",
                "rate_to_cad": "1",
                "rate_to_bdt": "100",
                "reference": "LOCKED-UTILITY",
                "business_purpose": "Canada office electricity",
                "supporting_document": SimpleUploadedFile("utility.txt", b"approved bill"),
                "utility_type": "HYDRO",
                "vendor": self.supplier.pk,
                "billing_period_start": "2026-01-01",
                "billing_period_end": "2026-01-31",
                "bill_date": "2026-02-01",
                "due_date": "2026-02-20",
                "amount": "100.00",
                "location": "OFFICE",
                "payment_status": "UNPAID",
            },
        )
        self.assertEqual(response.status_code, 302)
        operation = FinanceOperation.objects.get(reference="LOCKED-UTILITY")
        self.assertEqual(operation.side, "CA")
        self.assertEqual(operation.currency, "CAD")

    def test_legacy_entry_routes_redirect_without_creating_legacy_records(self):
        from crm.models import AccountingEntry

        client = Client()
        client.force_login(self.submitter)
        before = AccountingEntry.objects.count()
        for route, side in (("accounting_entry_add_ca", "CA"), ("accounting_entry_add_bd", "BD")):
            with self.subTest(route=route):
                response = client.post(reverse(route), {"amount_original": "999"})
                self.assertRedirects(
                    response,
                    f"{reverse('finance_operations_center')}?side={side}",
                    fetch_redirect_response=False,
                )
        self.assertEqual(AccountingEntry.objects.count(), before)

    def test_customer_payment_form_warns_for_duplicate_and_overpayment(self):
        invoice = self.invoice("OPS-FORM-INV")
        existing = self.operation(
            FinanceOperation.TYPE_CUSTOMER_PAYMENT,
            "10",
            reference="DUPLICATE-REF",
            customer=self.customer,
            invoice=invoice,
            to_account=self.bank,
        )
        self.assertEqual(existing.state, FinanceOperation.STATE_PENDING)
        data = {
            "transaction_date": "2026-02-16",
            "side": "CA",
            "currency": "CAD",
            "rate_to_cad": "1",
            "rate_to_bdt": "100",
            "reference": "DUPLICATE-REF",
            "business_purpose": "Customer invoice payment",
            "customer": self.customer.pk,
            "invoice": invoice.pk,
            "amount": "125.00",
            "payment_method": "bank",
            "payment_account": self.bank.pk,
            "supporting_document": SimpleUploadedFile("payment.txt", b"payment"),
        }
        form = CustomerPaymentOperationForm(data=data, files=data, user=self.submitter, workflow={"operation_type": FinanceOperation.TYPE_CUSTOMER_PAYMENT})
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)
        self.assertIn("duplicate_override", form.errors)
        data.update(
            allow_customer_credit="on",
            duplicate_override="on",
            duplicate_reason="Separate bank settlement verified.",
        )
        form = CustomerPaymentOperationForm(data=data, files=data, user=self.submitter, workflow={"operation_type": FinanceOperation.TYPE_CUSTOMER_PAYMENT})
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertTrue(form.build_operation().duplicate_warning)

    def test_customer_partial_full_and_overpayment_credit_use_protected_service(self):
        invoice = self.invoice("OPS-PAY-INV")
        partial = self.operation(
            FinanceOperation.TYPE_CUSTOMER_PAYMENT,
            "40",
            customer=self.customer,
            invoice=invoice,
            to_account=self.bank,
            payment_method="bank",
        )
        partial = self.approve(partial)
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            partial = post_operation(partial, actor=self.approver)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("40.00"))
        self.assertEqual(invoice.status, "partial")

        final = self.operation(
            FinanceOperation.TYPE_CUSTOMER_PAYMENT,
            "60",
            customer=self.customer,
            invoice=invoice,
            to_account=self.bank,
            payment_method="bank",
        )
        final = self.approve(final)
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            final = post_operation(final, actor=self.approver)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("100.00"))
        self.assertEqual(invoice.status, "paid")

        over_invoice = self.invoice("OPS-OVER-INV")
        overpayment = self.operation(
            FinanceOperation.TYPE_CUSTOMER_PAYMENT,
            "125",
            customer=self.customer,
            invoice=over_invoice,
            to_account=self.bank,
            payment_method="bank",
            details={"allow_customer_credit": True},
        )
        overpayment = self.approve(overpayment)
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            overpayment = post_operation(overpayment, actor=self.approver)
        over_invoice.refresh_from_db()
        self.assertEqual(over_invoice.paid_amount, Decimal("100.00"))
        self.assertTrue(
            JournalLine.objects.filter(
                journal__source_key__contains="-CREDIT",
                account__system_key="CUSTOMER_DEPOSITS",
                native_credit=Decimal("25.00"),
            ).exists()
        )

    def test_customer_balance_change_cannot_create_unapproved_credit(self):
        invoice = self.invoice("OPS-CONCURRENT-INV")
        operations = [
            self.approve(self.operation(
                FinanceOperation.TYPE_CUSTOMER_PAYMENT,
                "80",
                customer=self.customer,
                invoice=invoice,
                to_account=self.bank,
            ))
            for _index in range(2)
        ]
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            post_operation(operations[0], actor=self.approver)
            with self.assertRaises(FinanceOperationError):
                post_operation(operations[1], actor=self.approver)
        operations[1].refresh_from_db()
        self.assertEqual(operations[1].state, FinanceOperation.STATE_APPROVED)

    def test_customer_refund_credit_note_and_unapplied_credit_post_linked_events(self):
        invoice = self.invoice("OPS-ADJ-INV", "200")
        receipt = self.operation(
            FinanceOperation.TYPE_CUSTOMER_PAYMENT,
            "100",
            customer=self.customer,
            invoice=invoice,
            to_account=self.bank,
        )
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            post_operation(self.approve(receipt), actor=self.approver)
        refund = self.operation(
            FinanceOperation.TYPE_CUSTOMER_REFUND,
            "25",
            customer=self.customer,
            invoice=invoice,
            from_account=self.bank,
            reason="Approved return",
        )
        credit_note = self.operation(
            FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE,
            "20",
            customer=self.customer,
            invoice=invoice,
            reason="Approved price adjustment",
        )
        customer_credit = self.operation(
            FinanceOperation.TYPE_CUSTOMER_CREDIT,
            "30",
            customer=self.customer,
            to_account=self.bank,
        )
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            for operation in (refund, credit_note, customer_credit):
                post_operation(self.approve(operation), actor=self.approver)
        self.assertTrue(ReceivableEvent.objects.filter(kind=ReceivableEvent.KIND_REFUND).exists())
        self.assertTrue(ReceivableEvent.objects.filter(kind=ReceivableEvent.KIND_CREDIT_NOTE).exists())

    def test_supplier_bill_duplicate_and_partial_full_payment(self):
        bill = self.supplier_bill("OPS-SUP-BILL", "100")
        self.assertEqual(bill.payment_status, SupplierBill.PAYMENT_UNPAID)
        duplicate_data = {
            "transaction_date": "2026-02-15", "bill_date": "2026-02-15", "due_date": "2026-03-15",
            "side": "CA", "currency": "CAD", "rate_to_cad": "1", "rate_to_bdt": "100",
            "reference": "OPS-SUP-BILL", "business_purpose": "Duplicate bill check", "supplier": self.supplier.pk,
            "amount_before_tax": "100", "tax": "0", "total": "100", "category": self.office_rent.pk,
            "supporting_document": SimpleUploadedFile("bill.txt", b"bill"),
        }
        from crm.forms_finance_operations import SupplierBillOperationForm

        form = SupplierBillOperationForm(data=duplicate_data, files=duplicate_data, user=self.submitter, workflow={"operation_type": FinanceOperation.TYPE_SUPPLIER_BILL})
        self.assertFalse(form.is_valid())
        self.assertIn("reference", form.errors)

        for index, amount in enumerate(("40", "60"), start=1):
            bill.refresh_from_db()
            payment = self.operation(
                FinanceOperation.TYPE_SUPPLIER_PAYMENT,
                amount,
                reference=f"OPS-SPAY-{index}",
                supplier=self.supplier,
                supplier_bill=bill,
                from_account=self.bank,
                payment_method="bank",
                details={"bill_total": "100", "already_paid": str(bill.paid_amount), "outstanding_at_submission": str(bill.remaining_amount)},
            )
            with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
                post_operation(self.approve(payment), actor=self.approver)
        bill.refresh_from_db()
        self.assertEqual(bill.remaining_amount, Decimal("0.00"))
        self.assertEqual(bill.payment_status, SupplierBill.PAYMENT_PAID)

    def test_expense_utility_and_payroll_workflows_post_canonical_sources(self):
        expense = self.operation(
            FinanceOperation.TYPE_COMPANY_EXPENSE,
            "105",
            amount_before_tax=Decimal("100"),
            tax_amount=Decimal("5"),
            supplier=self.supplier,
            expense_category=self.office_rent,
            department=self.department,
            from_account=self.bank,
            payment_method="bank",
            details={"payment_status": "PAID", "is_recurring": False},
        )
        utility = self.operation(
            FinanceOperation.TYPE_UTILITY_BILL,
            "75",
            amount_before_tax=Decimal("75"),
            supplier=self.supplier,
            expense_category=self.hydro,
            details={
                "utility_type": "HYDRO", "billing_period_start": "2026-01-01", "billing_period_end": "2026-01-31",
                "bill_date": "2026-02-15", "due_date": "2026-03-01", "location": "FACTORY", "payment_status": "UNPAID",
            },
        )
        payroll = self.operation(
            FinanceOperation.TYPE_PAYROLL,
            "105",
            department=self.department,
            from_account=self.bank,
            details={
                "period_start": "2026-02-01", "period_end": "2026-02-28", "payroll_type": "BASE",
                "gross_amount": "100", "deductions": "10", "employer_cost": "5", "net_paid": "90",
            },
        )
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            for operation in (expense, utility, payroll):
                post_operation(self.approve(operation), actor=self.approver)
        self.assertTrue(ExpenseRecord.objects.filter(expense_number=expense.operation_number).exists())
        self.assertTrue(SupplierBill.objects.filter(bill_number=utility.operation_number).exists())
        self.assertEqual(PayrollBatch.objects.get(reference=payroll.operation_number).state, PayrollBatch.STATE_PAID)

    def test_production_cost_and_factory_daily_snapshot_preserve_rate(self):
        locked_timeline = save_estimated_factory_timeline(
            self.quick_costing,
            estimated_days=5,
            daily_default=self.factory_default,
            estimated_revenue=Decimal("100000"),
            other_estimated_cost=Decimal("30000"),
            daily_amount_snapshot=Decimal("10000"),
            target_margin_percent=Decimal("20"),
            approved_minimum_margin_percent=Decimal("10"),
            actor=self.approver,
        )
        self.assertIsNotNone(locked_timeline.locked_at)
        production = self.operation(
            FinanceOperation.TYPE_PRODUCTION_COST,
            "120",
            supplier=self.supplier,
            production_order=self.production_order,
            customer=self.customer,
            opportunity=self.opportunity,
            details={"cost_category": "FABRIC", "estimated_amount": "100", "actual_amount": "120", "payment_status": "UNPAID"},
        )
        factory = self.operation(
            FinanceOperation.TYPE_FACTORY_DAILY_COST,
            "50000",
            side="BD",
            currency="BDT",
            rate_to_cad=Decimal("0.01"),
            rate_to_bdt=Decimal("1"),
            reference="FACTORY-TIMELINE",
            details={
                "daily_default_id": self.factory_default.pk, "pricing_type": "fob", "estimated_days": 5,
                "actual_days": 7, "daily_factory_cost": "10000", "estimated_timeline_cost": "50000",
                "actual_timeline_cost": "70000", "estimated_revenue": "100000", "actual_revenue": "90000",
                "other_estimated_cost": "30000", "other_actual_cost": "30000", "target_margin_percent": "20",
                "approved_minimum_margin_percent": "10", "delay_reason": "Approved production delay",
            },
        )
        factory.source_record = self.quick_costing
        FinanceOperation.objects.filter(pk=factory.pk).update(
            source_content_type=factory.source_content_type, source_object_id=self.quick_costing.pk
        )
        factory.refresh_from_db()
        self.factory_default.daily_amount = Decimal("20000")
        self.factory_default.save()
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            production = post_operation(self.approve(production), actor=self.approver)
            factory = post_operation(review_operation(factory, actor=self.approver, action="APPROVE", notes="Approved daily rate snapshot verified."), actor=self.approver)
        production.source_record.refresh_from_db()
        timeline = self.quick_costing.factory_timeline
        self.assertEqual(production.source_record.variance, Decimal("20.00"))
        self.assertEqual(timeline.estimated_production_days, 5)
        self.assertEqual(timeline.daily_factory_cost, Decimal("10000.00"))
        timeline.refresh_from_db()
        self.assertEqual(timeline.actual_timeline_cost, Decimal("70000.00"))
        self.assertEqual(timeline.actual_profit, Decimal("-10000.00"))

    def test_bank_cash_owner_loan_asset_inventory_previews_cover_every_mapping(self):
        types_and_fields = (
            (FinanceOperation.TYPE_BANK_DEPOSIT, {"from_account": self.cash, "to_account": self.bank}),
            (FinanceOperation.TYPE_BANK_WITHDRAWAL, {"from_account": self.bank, "to_account": self.cash}),
            (FinanceOperation.TYPE_CASH_DEPOSIT, {"from_account": self.bank, "to_account": self.cash}),
            (FinanceOperation.TYPE_CASH_WITHDRAWAL, {"from_account": self.cash, "to_account": self.bank}),
            (FinanceOperation.TYPE_ACCOUNT_TRANSFER, {"from_account": self.bank, "to_account": self.cash}),
            (FinanceOperation.TYPE_BANK_FEE, {"from_account": self.bank}),
            (FinanceOperation.TYPE_PROCESSOR_FEE, {"from_account": self.bank}),
            (FinanceOperation.TYPE_OWNER_INVESTMENT, {"to_account": self.bank}),
            (FinanceOperation.TYPE_OWNER_WITHDRAWAL, {"from_account": self.bank}),
            (FinanceOperation.TYPE_LOAN_RECEIVED, {"to_account": self.bank}),
            (FinanceOperation.TYPE_LOAN_PRINCIPAL, {"from_account": self.bank}),
            (FinanceOperation.TYPE_LOAN_INTEREST, {"from_account": self.bank}),
            (FinanceOperation.TYPE_SHAREHOLDER_ADVANCE, {"to_account": self.bank}),
            (FinanceOperation.TYPE_SHAREHOLDER_REPAYMENT, {"from_account": self.bank}),
            (FinanceOperation.TYPE_ASSET_PURCHASE, {"from_account": self.bank, "details": {"asset_type": "SEWING_MACHINE"}}),
            (FinanceOperation.TYPE_INVENTORY_ADJUSTMENT, {"details": {"adjustment_type": "WASTE", "direction": "DECREASE", "inventory_item_id": self.inventory_item.pk, "quantity": "1", "unit_cost": "10"}}),
        )
        for operation_type, fields in types_and_fields:
            with self.subTest(operation_type=operation_type):
                operation = self.operation(operation_type, **fields)
                preview = build_posting_preview(operation)
                self.assertFalse(preview["missing_accounts"])
                self.assertTrue(preview["entries"])
                for entry in preview["entries"]:
                    self.assertEqual(
                        sum(Decimal(line["debit"]) for line in entry["lines"]),
                        sum(Decimal(line["credit"]) for line in entry["lines"]),
                    )

    def test_transfer_and_financing_posts_do_not_create_revenue_or_operating_expense(self):
        operations = (
            self.operation(FinanceOperation.TYPE_ACCOUNT_TRANSFER, from_account=self.bank, to_account=self.cash),
            self.operation(FinanceOperation.TYPE_OWNER_INVESTMENT, to_account=self.bank),
            self.operation(FinanceOperation.TYPE_LOAN_RECEIVED, to_account=self.bank),
        )
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            for operation in operations:
                post_operation(self.approve(operation), actor=self.approver)
        lines = JournalLine.objects.filter(journal__source_key__startswith="FINANCE-OPERATION:")
        self.assertFalse(
            lines.filter(
                account__account_type__in=(FinancialAccount.TYPE_REVENUE, FinancialAccount.TYPE_OPERATING_EXPENSE)
            ).exists()
        )

    def transfer_form(self, *, direction, source, destination, amount_sent, amount_received, fee="0"):
        return MoneyTransferOperationForm(
            data={
                "transaction_date": "2026-02-15",
                "side": source.side,
                "currency": source.currency,
                "reference": f"TRANSFER-{direction}",
                "business_purpose": "FACTORY_FUNDING",
                "notes": "Verified cross-country transfer",
                "transfer_type": direction,
                "transfer_service": "TAPTAP_SEND",
                "other_transfer_service": "",
                "from_account": source.pk,
                "amount": amount_sent,
                "to_account": destination.pk,
                "destination_amount": amount_received,
                "receiving_currency": destination.currency,
                "provider_exchange_rate": "88",
                "transfer_fee": fee,
                "fee_currency": source.currency,
                "rate_to_cad": "",
                "rate_to_bdt": "",
                "destination_rate_to_cad": "",
                "destination_rate_to_bdt": "",
            },
            user=self.submitter,
            workflow=workflow_definition("money-transfer"),
            locked_side=source.side,
        )

    def test_canada_to_bangladesh_actual_amount_and_fee_post_once(self):
        form = self.transfer_form(
            direction=TRANSFER_TYPE_CA_TO_BD,
            source=self.bank,
            destination=self.bd_bank,
            amount_sent="1000",
            amount_received="88000",
            fee="5",
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        operation = submit_operation(form.build_operation(), actor=self.submitter)

        self.assertEqual(operation.total_amount, Decimal("1000.00"))
        self.assertEqual(operation.amount_cad, Decimal("1000.00"))
        self.assertEqual(operation.amount_bdt, Decimal("88000.00"))
        self.assertEqual(operation.details["destination_amount"], "88000")
        self.assertEqual(operation.details["effective_rate_display"], "1 CAD = 88 BDT")
        self.assertEqual(operation.details["transfer_service_display"], "TapTap Send")
        self.assertEqual(operation.details["transfer_fee"], "5")

        preview = build_posting_preview(operation)
        self.assertEqual(len(preview["entries"]), 3)
        self.assertEqual([entry["side"] for entry in preview["entries"]], ["CA", "BD", "CA"])
        self.assertEqual(
            {line["account_key"] for entry in preview["entries"] for line in entry["lines"]}
            & {"PRODUCT_SALES", "OTHER_REVENUE", "OTHER_EXPENSE"},
            set(),
        )

        operation = review_operation(
            operation,
            actor=self.approver,
            action="APPROVE",
            notes="Cross-country amounts and transfer fee verified.",
        )
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            operation = post_operation(operation, actor=self.approver)

        journals = JournalEntry.objects.filter(source_key__startswith=f"FINANCE-OPERATION:{operation.pk}")
        self.assertEqual(journals.count(), 3)
        self.assertEqual(set(journals.values_list("side", flat=True)), {"CA", "BD"})
        lines = JournalLine.objects.filter(journal__in=journals)
        self.assertEqual(
            sum(lines.filter(account=self.bank.gl_account).values_list("native_credit", flat=True)),
            Decimal("1005.00"),
        )
        self.assertEqual(
            sum(lines.filter(account=self.bd_bank.gl_account).values_list("native_debit", flat=True)),
            Decimal("88000.00"),
        )
        self.assertEqual(
            sum(lines.filter(account__system_key="BANK_FEES").values_list("cad_debit", flat=True)),
            Decimal("5.00"),
        )
        self.assertFalse(lines.filter(account__account_type=FinancialAccount.TYPE_REVENUE).exists())
        self.assertEqual(
            lines.filter(account__account_type=FinancialAccount.TYPE_OPERATING_EXPENSE)
            .exclude(account__system_key="BANK_FEES")
            .count(),
            0,
        )
        clearing = lines.filter(account__system_key="FX_CLEARING")
        self.assertEqual(
            sum(clearing.values_list("cad_debit", flat=True)),
            sum(clearing.values_list("cad_credit", flat=True)),
        )
        self.assertEqual(
            sum(clearing.values_list("bdt_debit", flat=True)),
            sum(clearing.values_list("bdt_credit", flat=True)),
        )
        client = Client()
        client.force_login(self.approver)
        detail = client.get(reverse("finance_operation_detail", args=[operation.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Canada to Bangladesh")
        self.assertContains(detail, "1 CAD = 88 BDT")
        self.assertContains(detail, "CAD 5.00")

    def test_bangladesh_to_canada_preserves_reverse_effective_rate(self):
        form = self.transfer_form(
            direction=TRANSFER_TYPE_BD_TO_CA,
            source=self.bd_bank,
            destination=self.bank,
            amount_sent="88000",
            amount_received="1000",
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        operation = submit_operation(form.build_operation(), actor=self.submitter)

        self.assertEqual(operation.amount_cad, Decimal("1000.00"))
        self.assertEqual(operation.amount_bdt, Decimal("88000.00"))
        self.assertEqual(operation.details["effective_rate_display"], "1 CAD = 88 BDT")
        self.assertEqual(operation.details["destination_rate_to_cad"], "1.0000000000")
        self.assertEqual(operation.details["destination_rate_to_bdt"], "88.0000000000")

    def test_cross_country_transfer_is_visible_from_destination_country_scope(self):
        form = self.transfer_form(
            direction=TRANSFER_TYPE_CA_TO_BD,
            source=self.bank,
            destination=self.bd_bank,
            amount_sent="1000",
            amount_received="88000",
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        operation = submit_operation(form.build_operation(), actor=self.submitter)
        bangladesh_finance = self.user("bd-transfer-view", "Finance", side="BD", ca=False, bd=True)

        self.assertTrue(
            scope_finance_operations_for_user(
                FinanceOperation.objects.filter(pk=operation.pk), bangladesh_finance
            ).exists()
        )
        client = Client()
        client.force_login(self.submitter)
        response = client.get(
            f"{reverse('finance_operation_create', args=['money-transfer'])}?side=BD"
        )
        self.assertContains(response, operation.operation_number)

    def test_internal_transfer_preview_remains_single_balanced_entry(self):
        form = self.transfer_form(
            direction=TRANSFER_TYPE_INTERNAL,
            source=self.bank,
            destination=self.cash,
            amount_sent="250",
            amount_received="250",
        )
        form.data = form.data.copy()
        form.data["transfer_service"] = ""
        form.data["provider_exchange_rate"] = ""
        form.data["rate_to_cad"] = "1"
        form.data["rate_to_bdt"] = "100"
        self.assertTrue(form.is_valid(), form.errors.as_json())
        preview = build_posting_preview(submit_operation(form.build_operation(), actor=self.submitter))
        self.assertEqual(len(preview["entries"]), 1)
        self.assertEqual(preview["entries"][0]["side"], "CA")

    def test_inventory_purchase_creates_payable_subledger_source(self):
        operation = self.operation(
            FinanceOperation.TYPE_INVENTORY_ADJUSTMENT,
            supplier=self.supplier,
            details={"adjustment_type": "PURCHASE", "direction": "INCREASE", "inventory_item_id": self.inventory_item.pk, "inventory_item": "Operations Fabric", "quantity": "10", "unit_cost": "10"},
        )
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=True):
            operation = post_operation(self.approve(operation), actor=self.approver)
        bill = SupplierBill.objects.get(pk=operation.source_object_id)
        self.assertEqual(bill.expense_account.system_key, "INVENTORY")
        self.assertEqual(bill.remaining_amount, Decimal("100.00"))
        self.inventory_item.refresh_from_db()
        self.assertEqual(self.inventory_item.quantity, Decimal("110.00"))
        self.assertTrue(
            InventoryMovement.objects.filter(
                inventory_item=self.inventory_item,
                notes__contains=operation.operation_number,
            ).exists()
        )

    def test_high_risk_self_approval_is_rejected_and_flag_off_blocks_posting(self):
        operation = self.operation(
            FinanceOperation.TYPE_OWNER_WITHDRAWAL,
            from_account=self.bank,
            reason="Approved owner distribution",
        )
        self.evidence(operation)
        with self.assertRaises(FinanceOperationError):
            review_operation(
                operation,
                actor=self.submitter,
                action="APPROVE",
                notes="Attempted self approval.",
            )
        operation = review_operation(
            operation,
            actor=self.approver,
            action="APPROVE",
            notes="Independent approval with evidence.",
        )
        with self.assertRaises(FinanceOperationWritesDisabled):
            post_operation(operation, actor=self.approver)
        operation.refresh_from_db()
        self.assertEqual(operation.state, FinanceOperation.STATE_APPROVED)
        self.assertIn("disabled", operation.posting_error.lower())
        self.assertFalse(JournalEntry.objects.filter(source_key=f"FINANCE-OPERATION:{operation.pk}").exists())

    def test_role_permissions_and_operation_url_tampering(self):
        finance = self.user("ops-finance", "Finance")
        finance_both = self.user("ops-finance-both", "Finance", ca=True, bd=True)
        accounts_both = self.user("ops-accounts-both", "Accounts", ca=True, bd=True)
        production = self.user("ops-production", "Production", ca=False, bd=True, side="BD")
        hr = self.user("ops-hr", "HR", ca=False, bd=True, side="BD")
        sales = self.user("ops-sales", "Sales", ca=False, bd=False, side="CA")
        normal = self.user("ops-normal", "Staff", ca=False, bd=False, side="CA")
        self.assertTrue(can_submit_finance_operation(finance, FinanceOperation.TYPE_OWNER_INVESTMENT))
        self.assertTrue(can_submit_finance_operation(production, FinanceOperation.TYPE_PRODUCTION_COST))
        self.assertFalse(can_submit_finance_operation(production, FinanceOperation.TYPE_PAYROLL))
        self.assertTrue(can_submit_finance_operation(hr, FinanceOperation.TYPE_PAYROLL))
        self.assertFalse(can_submit_finance_operation(hr, FinanceOperation.TYPE_OWNER_INVESTMENT))
        self.assertFalse(can_submit_finance_operation(sales, FinanceOperation.TYPE_CUSTOMER_PAYMENT))
        self.assertFalse(can_submit_finance_operation(normal, FinanceOperation.TYPE_COMPANY_EXPENSE))

        ca_operation = self.operation(FinanceOperation.TYPE_COMPANY_EXPENSE, amount_before_tax=Decimal("100"), expense_category=self.office_rent, supplier=self.supplier, details={"payment_status": "UNPAID", "due_date": "2026-03-01"})
        client = Client()
        client.force_login(production)
        self.assertEqual(client.get(reverse("finance_operation_detail", args=[ca_operation.pk])).status_code, 404)
        self.assertEqual(client.get(f"{reverse('finance_operations_center')}?side=CA").status_code, 403)
        bd_form = client.get(reverse("finance_operation_create", args=["production-cost"])).context["form"]
        self.assertEqual(bd_form.locked_side, "BD")
        self.assertEqual(bd_form.fields["side"].widget.input_type, "hidden")
        client.force_login(accounts_both)
        accounts_form = client.get(
            f"{reverse('finance_operation_create', args=['utility'])}?side=CA"
        )
        self.assertFalse(accounts_form.context["can_switch_side"])
        self.assertFalse(accounts_form.context["can_view_form_advanced"])
        self.assertNotContains(accounts_form, "Switch country with warning")
        client.force_login(finance_both)
        finance_form = client.get(
            f"{reverse('finance_operation_create', args=['utility'])}?side=CA"
        )
        self.assertTrue(finance_form.context["can_switch_side"])
        self.assertTrue(finance_form.context["can_view_form_advanced"])
        self.assertContains(finance_form, "Switch country with warning")
        client.force_login(normal)
        self.assertEqual(client.get(reverse("finance_operations_center")).status_code, 403)

    def test_document_download_rechecks_operation_scope(self):
        operation = self.operation(FinanceOperation.TYPE_OWNER_INVESTMENT, to_account=self.bank)
        document = self.evidence(operation)
        client = Client()
        client.force_login(self.submitter)
        self.assertEqual(client.get(reverse("financial_document_download", args=[document.pk])).status_code, 200)
        outsider = self.user("ops-document-outsider", "Production", ca=False, bd=True, side="BD")
        client.force_login(outsider)
        self.assertEqual(client.get(reverse("financial_document_download", args=[document.pk])).status_code, 404)

    def test_approval_and_activity_pages_paginate_with_bounded_queries(self):
        for index in range(55):
            self.operation(
                FinanceOperation.TYPE_COMPANY_EXPENSE,
                "10",
                reference=f"PAGING-{index}",
                amount_before_tax=Decimal("10"),
                supplier=self.supplier,
                expense_category=self.office_rent,
                department=self.department,
                details={"payment_status": "UNPAID", "due_date": "2026-03-01"},
            )
        client = Client()
        client.force_login(self.approver)
        client.get(reverse("finance_approval_center"))
        with CaptureQueriesContext(connection) as approval_queries:
            response = client.get(reverse("finance_approval_center"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page"].object_list), 50)
        self.assertLessEqual(len(approval_queries), 8)
        client.get(reverse("finance_operations_center"))
        with CaptureQueriesContext(connection) as center_queries:
            response = client.get(reverse("finance_operations_center"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(center_queries), 8)
        with CaptureQueriesContext(connection) as country_center_queries:
            response = client.get(f"{reverse('finance_operations_center')}?side=BD")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(country_center_queries), 8)

    def test_scoped_queryset_does_not_expose_private_payroll_to_sales(self):
        sales = self.user("ops-payroll-sales", "Sales", ca=False, bd=False, side="CA")
        payroll = self.operation(
            FinanceOperation.TYPE_PAYROLL,
            "105",
            department=self.department,
            from_account=self.bank,
            details={
                "period_start": "2026-02-01", "period_end": "2026-02-28", "payroll_type": "BASE",
                "gross_amount": "100", "deductions": "10", "employer_cost": "5", "net_paid": "90",
            },
        )
        self.assertFalse(scope_finance_operations_for_user(FinanceOperation.objects.filter(pk=payroll.pk), sales).exists())
