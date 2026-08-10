from datetime import date
from decimal import Decimal
from time import perf_counter

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from crm.forms_finance_operations import (
    CustomerPaymentOperationForm,
    ExpenseOperationForm,
    SupplierBillOperationForm,
)
from crm.models import (
    CashBankAccount,
    Customer,
    ExpenseCategory,
    FactoryRunningCostDefault,
    FinanceOperation,
    Invoice,
    JournalEntry,
    Supplier,
)
from crm.services.chart_of_accounts import account_by_key, bootstrap_chart_of_accounts
from crm.services.finance_operations import WORKFLOWS, workflow_definition


@override_settings(
    FINANCIAL_CORE_WRITES_ENABLED=True,
    FINANCIAL_CORE_REPORTING_ACTIVE=True,
    SECURE_SSL_REDIRECT=False,
)
class FinanceFormUsabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="finance-form-admin",
            email="finance-form@example.com",
            password="test-pass",
        )
        bootstrap_chart_of_accounts(actor=cls.user)
        cls.ca_customer = Customer.objects.create(
            customer_code="CA-CUSTOMER",
            account_brand="Canada Customer",
            contact_name="Canada Contact",
            email="canada@example.com",
            phone="604-555-0101",
            market="CA",
            country="Canada",
        )
        cls.bd_customer = Customer.objects.create(
            customer_code="BD-CUSTOMER",
            account_brand="Bangladesh Customer",
            contact_name="Bangladesh Contact",
            email="bangladesh@example.com",
            phone="01700-000000",
            market="BD",
            country="Bangladesh",
        )
        cls.ca_invoice = Invoice.objects.create(
            customer=cls.ca_customer,
            invoice_number="CA-OPEN-100",
            issue_date=date(2026, 7, 1),
            invoice_date=date(2026, 7, 1),
            currency="CAD",
            invoice_region="CA",
            total_amount=Decimal("8000"),
            paid_amount=Decimal("5000"),
            status="partial",
            notes="Customer reference CA-REF-100",
        )
        cls.bd_invoice = Invoice.objects.create(
            customer=cls.bd_customer,
            invoice_number="BD-OPEN-200",
            issue_date=date(2026, 7, 2),
            invoice_date=date(2026, 7, 2),
            currency="BDT",
            invoice_region="BD",
            total_amount=Decimal("90000"),
            paid_amount=Decimal("10000"),
            status="sent",
        )
        cls.draft_invoice = Invoice.objects.create(
            customer=cls.ca_customer,
            invoice_number="CA-DRAFT-300",
            issue_date=date(2026, 7, 3),
            currency="CAD",
            invoice_region="CA",
            total_amount=Decimal("1000"),
            status="draft",
        )
        cls.ca_supplier = Supplier.objects.create(
            code="CA-SUPPLIER",
            name="Canada Supplier",
            side="CA",
            default_currency="CAD",
            contact_name="Supplier Contact",
            phone="604-555-0110",
            email="supplier@example.com",
            created_by=cls.user,
        )
        cls.bd_supplier = Supplier.objects.create(
            code="BD-SUPPLIER",
            name="Bangladesh Supplier",
            side="BD",
            default_currency="BDT",
            created_by=cls.user,
        )
        cls.ca_account = CashBankAccount.objects.create(
            name="Canada Operating Bank",
            kind=CashBankAccount.KIND_BANK,
            side="CA",
            currency="CAD",
            gl_account=account_by_key("CANADIAN_BANK"),
            created_by=cls.user,
        )
        cls.bd_account = CashBankAccount.objects.create(
            name="Bangladesh Operating Bank",
            kind=CashBankAccount.KIND_BANK,
            side="BD",
            currency="BDT",
            gl_account=account_by_key("BANGLADESH_BANK"),
            created_by=cls.user,
        )
        cls.factory_default = FactoryRunningCostDefault.objects.create(
            name="Approved factory rate",
            side="BD",
            currency="BDT",
            daily_amount=Decimal("10000"),
            effective_from=date(2026, 1, 1),
            created_by=cls.user,
        )

    def payment_form(self, side):
        return CustomerPaymentOperationForm(
            user=self.user,
            workflow=workflow_definition("customer-payment"),
            locked_side=side,
        )

    def test_customer_and_invoice_choices_are_country_scoped(self):
        ca_form = self.payment_form("CA")
        bd_form = self.payment_form("BD")

        self.assertQuerySetEqual(ca_form.fields["customer"].queryset, [self.ca_customer])
        self.assertQuerySetEqual(bd_form.fields["customer"].queryset, [self.bd_customer])
        self.assertQuerySetEqual(ca_form.fields["invoice"].queryset, [self.ca_invoice])
        self.assertQuerySetEqual(bd_form.fields["invoice"].queryset, [self.bd_invoice])
        self.assertNotIn(self.draft_invoice, ca_form.fields["invoice"].queryset)

    def test_invoice_selector_contains_dependency_and_balance_metadata(self):
        html = str(self.payment_form("CA")["invoice"])

        self.assertIn(f'data-customer-id="{self.ca_customer.pk}"', html)
        self.assertIn('data-side="CA"', html)
        self.assertIn('data-primary="CA-OPEN-100"', html)
        self.assertIn('data-total="8000.00"', html)
        self.assertIn('data-paid="5000.00"', html)
        self.assertIn('data-outstanding="3000.00"', html)
        self.assertIn("Customer reference CA-REF-100", html)

    def test_account_and_supplier_selectors_use_safe_search_metadata(self):
        payment_html = str(self.payment_form("CA")["payment_account"])
        supplier_form = SupplierBillOperationForm(
            user=self.user,
            workflow=workflow_definition("supplier-bill"),
            locked_side="CA",
        )
        supplier_html = str(supplier_form["supplier"])

        self.assertIn('data-primary="Canada Operating Bank | CAD"', payment_html)
        self.assertNotIn("Bangladesh Operating Bank", payment_html)
        self.assertIn('data-primary="Canada Supplier"', supplier_html)
        self.assertIn("Supplier Contact", supplier_html)
        self.assertIn("604-555-0110", supplier_html)
        self.assertIn("supplier@example.com", supplier_html)
        self.assertNotIn("Bangladesh Supplier", supplier_html)

    def test_expense_categories_have_real_labels_and_visual_groups(self):
        form = ExpenseOperationForm(
            user=self.user,
            workflow=workflow_definition("expense"),
            locked_side="CA",
        )
        html = str(form["category"])

        self.assertNotIn("ExpenseCategory object", html)
        self.assertIn('<optgroup label="Office">', html)
        self.assertIn("Office rent (OFFICE_RENT)", html)
        self.assertIn('data-group="Office"', html)

    def test_all_finance_master_labels_are_human_readable(self):
        self.assertNotIn("object (", str(ExpenseCategory.objects.get(code="OFFICE_RENT")))
        self.assertNotIn("object (", str(self.factory_default))
        for slug in ("customer-payment", "supplier-bill", "utility", "expense", "factory-daily-cost"):
            response = self.client_for_user().get(
                f"{reverse('finance_operation_create', args=[slug])}?side={'BD' if slug == 'factory-daily-cost' else 'CA'}"
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, " object (")

    def client_for_user(self):
        client = Client()
        client.force_login(self.user)
        return client

    def test_customer_payment_page_uses_sections_summary_and_search_controls(self):
        response = self.client_for_user().get(
            f"{reverse('finance_operation_create', args=['customer-payment'])}?side=CA"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Transaction Details")
        self.assertContains(response, "Customer and Invoice")
        self.assertContains(response, "Payment Summary")
        self.assertContains(response, "Canada Finance")
        self.assertContains(response, "finance_form.js")
        self.assertContains(response, 'data-parent-field="customer"')

    def test_major_finance_forms_render_one_distinct_page_guide(self):
        cases = (
            ("customer-payment", "CA", "Record money received from a customer.", "Creating invoices, supplier payments, or refunds."),
            ("supplier-bill", "CA", "Record a bill received from a supplier.", "Recording the actual payment."),
            ("supplier-payment", "CA", "Record money paid to a supplier.", "Creating a supplier bill."),
            ("expense", "CA", "Record company operating expenses.", "Supplier bills that should be tracked through Accounts Payable"),
            ("utility", "BD", "Record electricity, hydro, water, gas, internet, telephone, or other utility bills.", "General supplier purchases or production materials."),
            ("payroll", "BD", "Record approved payroll costs.", "General employee reimbursements"),
            ("production-cost", "BD", "Record actual production costs for an order.", "Office expenses or general company expenses."),
            ("factory-daily-cost", "BD", "Track production days used and the factory operating cost", "Estimated Quick Costing days"),
        )
        client = self.client_for_user()

        for slug, side, purpose, prohibited_use in cases:
            with self.subTest(slug=slug, side=side):
                response = client.get(f"{reverse('finance_operation_create', args=[slug])}?side={side}")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, purpose)
                self.assertContains(response, prohibited_use)
                self.assertEqual(response.content.count(b'data-finance-guidance='), 1)

    def test_every_daily_workflow_has_specific_guidance(self):
        client = self.client_for_user()

        for slug, operation_type, _title, _icon, _group in WORKFLOWS:
            with self.subTest(slug=slug):
                side = "BD" if slug in {"production-cost", "factory-daily-cost", "inventory-adjustment"} else "CA"
                response = client.get(f"{reverse('finance_operation_create', args=[slug])}?side={side}")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-finance-guidance="{operation_type}"')
                self.assertNotContains(response, "Complete this approved Finance workflow")

    def test_supplier_payment_page_has_live_balance_summary(self):
        response = self.client_for_user().get(
            f"{reverse('finance_operation_create', args=['supplier-payment'])}?side=CA"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-payment-summary="supplier"')
        self.assertContains(response, "Supplier Payment Summary")
        self.assertContains(response, "Bill Total")
        self.assertContains(response, "Already Paid")
        self.assertContains(response, "Remaining After Payment")

    def test_money_transfer_page_restores_cross_country_fields_and_summary(self):
        response = self.client_for_user().get(
            f"{reverse('finance_operation_create', args=['money-transfer'])}?side=CA"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Canada to Bangladesh")
        self.assertContains(response, "TapTap Send")
        self.assertContains(response, "Amount Sent")
        self.assertContains(response, "Amount Received")
        self.assertContains(response, "Transfer Fee")
        self.assertContains(response, "Transfer Summary")
        self.assertContains(response, "The principal is not revenue or operating expense")
        self.assertContains(response, 'data-side="CA"')
        self.assertContains(response, 'data-side="BD"')
        self.assertNotContains(response, "Bangladesh to Canada")

    def test_canada_to_bangladesh_draft_persists_actual_amount_without_receipt(self):
        client = self.client_for_user()
        url = f"{reverse('finance_operation_create', args=['money-transfer'])}?side=CA"
        response = client.post(
            url,
            {
                "action": "draft",
                "transaction_date": "2026-08-09",
                "side": "CA",
                "transfer_type": "CA_TO_BD",
                "transfer_service": "TAPTAP_SEND",
                "other_transfer_service": "",
                "reference": "LIVE-EXAMPLE-CA-BD",
                "from_account": self.ca_account.pk,
                "amount": "1000",
                "currency": "CAD",
                "to_account": self.bd_account.pk,
                "destination_amount": "88000",
                "receiving_currency": "BDT",
                "provider_exchange_rate": "88",
                "transfer_fee": "5",
                "fee_currency": "CAD",
                "business_purpose": "FACTORY_FUNDING",
                "notes": "Exact transfer workflow regression",
                "rate_to_cad": "",
                "rate_to_bdt": "",
                "destination_rate_to_cad": "",
                "destination_rate_to_bdt": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        operation = FinanceOperation.objects.get(reference="LIVE-EXAMPLE-CA-BD")
        self.assertEqual(operation.state, FinanceOperation.STATE_DRAFT)
        self.assertEqual(operation.total_amount, Decimal("1000.00"))
        self.assertEqual(operation.details["destination_amount"], "88000")
        self.assertEqual(operation.details["transfer_fee"], "5")
        self.assertEqual(operation.details["effective_rate_display"], "1 CAD = 88 BDT")
        self.assertFalse(JournalEntry.objects.filter(source_object_id=operation.pk).exists())

        refreshed = client.get(f"{url}&draft={operation.pk}")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.context["form"].initial["amount"], "1000")
        self.assertEqual(refreshed.context["form"].initial["destination_amount"], "88000")
        self.assertEqual(refreshed.context["form"].initial["transfer_fee"], "5")
        self.assertContains(refreshed, "Draft saved successfully.")

    def test_country_guidance_remains_locked_and_compact(self):
        client = self.client_for_user()
        canada = client.get(f"{reverse('finance_operation_create', args=['customer-payment'])}?side=CA")
        bangladesh = client.get(f"{reverse('finance_operation_create', args=['utility'])}?side=BD")

        self.assertContains(canada, "Canada Finance")
        self.assertContains(canada, "Canada office and Canada business transactions only")
        self.assertContains(canada, "Do not enter Bangladesh factory transactions here")
        self.assertContains(bangladesh, "Bangladesh Finance")
        self.assertContains(bangladesh, "Bangladesh factory and Bangladesh business transactions only")
        self.assertContains(bangladesh, "Do not enter Canada transactions here")

        report = client.get(f"{reverse('financial_general_ledger')}?side=BD")
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "Bangladesh Finance")
        self.assertContains(report, "BDT")
        self.assertContains(report, "Do not enter Canada transactions here")

        country_pages = (
            ("accounting_ca_master", "Canada Finance", "CANADA_TRANSFER"),
            ("accounting_ca_grid", "Canada Finance", "CANADA_ENTRIES"),
            ("accounting_bd_daily", "Bangladesh Finance", "BANGLADESH_DAILY"),
            ("accounting_bd_grid", "Bangladesh Finance", "BANGLADESH_ENTRIES"),
        )
        for url_name, country_label, guidance_key in country_pages:
            with self.subTest(url_name=url_name):
                response = client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, country_label)
                self.assertContains(response, f'data-finance-guidance="{guidance_key}"')
                self.assertEqual(response.content.count(b'data-finance-guidance='), 1)

    def test_major_finance_reports_render_read_only_guidance(self):
        cases = (
            ("profit_loss_dashboard", "Show how much the company earned and spent"),
            ("balance_sheet_dashboard", "Show company assets, liabilities, and equity"),
            ("cash_flow_dashboard", "Show cash entering and leaving the company"),
            ("accounts_receivable_dashboard", "Show customer invoices that are still unpaid"),
            ("accounts_payable_dashboard", "Show approved supplier bills that are still unpaid"),
            ("executive_financial_dashboard", "Show an executive view of revenue, profit, cash"),
            ("kpi_scorecard_dashboard", "Show an executive view of revenue, profit, cash"),
            ("budget_vs_actual_dashboard", "Compare the approved budget with posted actual results"),
            ("financial_forecast_dashboard", "Show a read-only cash forecast"),
            ("production_profit_report", "Compare order revenue with linked production costs"),
        )
        client = self.client_for_user()

        for url_name, purpose in cases:
            with self.subTest(url_name=url_name):
                response = client.get(reverse(url_name), follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, purpose)
                self.assertContains(response, "reporting only")
                self.assertEqual(response.content.count(b'data-finance-guidance='), 1)

    def test_country_lock_validation_is_unchanged(self):
        form = CustomerPaymentOperationForm(
            data={"side": "BD"},
            user=self.user,
            workflow=workflow_definition("customer-payment"),
            locked_side="CA",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("business side is locked", form.errors["side"][0].lower())

    def test_warm_customer_payment_page_stays_within_query_budget(self):
        client = self.client_for_user()
        url = f"{reverse('finance_operation_create', args=['customer-payment'])}?side=CA"
        client.get(url).content
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url)
            response.content
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 8)

    def test_money_transfer_page_performance_profile(self):
        client = self.client_for_user()
        url = f"{reverse('finance_operation_create', args=['money-transfer'])}?side=CA"
        cold_start = perf_counter()
        with CaptureQueriesContext(connection) as cold_queries:
            cold_response = client.get(url)
            cold_response.content
        cold_ms = (perf_counter() - cold_start) * 1000
        warm_start = perf_counter()
        with CaptureQueriesContext(connection) as warm_queries:
            warm_response = client.get(url)
            warm_response.content
        warm_ms = (perf_counter() - warm_start) * 1000
        print(
            "TRANSFER_PAGE_PERF "
            f"cold_queries={len(cold_queries)} cold_ms={cold_ms:.2f} "
            f"warm_queries={len(warm_queries)} warm_ms={warm_ms:.2f}"
        )
        self.assertEqual(cold_response.status_code, 200)
        self.assertEqual(warm_response.status_code, 200)
        self.assertLessEqual(len(warm_queries), 8)
