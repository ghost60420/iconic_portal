from datetime import date
from decimal import Decimal

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
    Invoice,
    Supplier,
)
from crm.services.chart_of_accounts import account_by_key, bootstrap_chart_of_accounts
from crm.services.finance_operations import workflow_definition


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
