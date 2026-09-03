from calendar import monthrange
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django import forms
from django.db.models import F, Q
from django.forms.models import ModelChoiceIterator

from crm.models import (
    CashBankAccount,
    Customer,
    Department,
    EmployeeProfile,
    ExpenseCategory,
    FactoryRunningCostDefault,
    FinanceOperation,
    FinancialAccount,
    Invoice,
    InventoryItem,
    ProductionOrder,
    QuickCosting,
    Supplier,
    SupplierBill,
)
from crm.services.finance_operations import UTILITY_ACCOUNT_KEYS
from crm.services.financial_currency import MissingExchangeRate, money, resolve_currency_snapshot
from crm.services.financial_permissions import (
    accessible_financial_sides,
    scope_bank_accounts_for_user,
    scope_invoices_for_user,
)
from crm.services.payment_reconciliation import customer_payment_reference_exists
from crm.services.receivable_accounting import (
    CUSTOMER_PAYMENT_METHOD_CHOICES,
    ReceivableAccountingError,
    validate_customer_payment_account,
)


CURRENCIES = (("CAD", "CAD"), ("USD", "USD"), ("BDT", "BDT"))
PAYMENT_METHODS = (
    ("bank", "Bank transfer"),
    ("cash", "Cash"),
    ("cheque", "Cheque"),
    ("card", "Card"),
    ("mobile", "Mobile payment"),
    ("other", "Other"),
)

TRANSFER_TYPE_INTERNAL = "INTERNAL"
TRANSFER_TYPE_CA_TO_BD = "CA_TO_BD"
TRANSFER_TYPE_BD_TO_CA = "BD_TO_CA"
TRANSFER_TYPE_CHOICES = (
    (TRANSFER_TYPE_INTERNAL, "Internal Account Transfer"),
    (TRANSFER_TYPE_CA_TO_BD, "Canada to Bangladesh"),
    (TRANSFER_TYPE_BD_TO_CA, "Bangladesh to Canada"),
)
TRANSFER_SERVICE_CHOICES = (
    ("", "Select transfer service"),
    ("TAPTAP_SEND", "TapTap Send"),
    ("WISE", "Wise"),
    ("BANK_WIRE", "Bank Wire"),
    ("WESTERN_UNION", "Western Union"),
    ("REMITLY", "Remitly"),
    ("OTHER", "Other"),
)
TRANSFER_PURPOSE_CHOICES = (
    ("", "Select purpose (optional)"),
    ("FACTORY_FUNDING", "Factory Funding"),
    ("PAYROLL_FUNDING", "Payroll Funding"),
    ("PRODUCTION_FUNDING", "Production Funding"),
    ("SUPPLIER_FUNDING", "Supplier Funding"),
    ("OPERATING_EXPENSES", "Operating Expenses"),
    ("OWNER_TRANSFER", "Owner Transfer"),
    ("OTHER", "Other"),
)


def _json_value(value):
    if isinstance(value, (date, Decimal)):
        return str(value)
    return value


def _rate_text(value):
    return format(Decimal(value), ".10f").rstrip("0").rstrip(".")


def _production_orders_for_sides(sides):
    side_query = Q(pk__in=[])
    if "BD" in sides:
        side_query |= Q(factory_location__iexact="bd")
    if "CA" in sides:
        side_query |= ~Q(factory_location__iexact="bd")
    return ProductionOrder.objects.filter(side_query)


def _invoice_side_query(sides):
    query = Q(pk__in=[])
    if "CA" in sides:
        query |= Q(invoice_region="CA") | Q(invoice_region="", currency__in=("CAD", "USD"))
    if "BD" in sides:
        query |= Q(invoice_region="BD") | Q(invoice_region="", currency="BDT")
    return query


def _customers_for_sides(sides):
    query = Q(pk__in=[])
    if "CA" in sides:
        query |= (
            Q(market__iexact="CA")
            | Q(country__iexact="CA")
            | Q(country__icontains="canada")
            | Q(invoice__invoice_region="CA")
            | Q(invoice__invoice_region="", invoice__currency__in=("CAD", "USD"))
            | Q(production_orders__factory_location__iexact="ca")
        )
    if "BD" in sides:
        query |= (
            Q(market__iexact="BD")
            | Q(country__iexact="BD")
            | Q(country__icontains="bangladesh")
            | Q(invoice__invoice_region="BD")
            | Q(invoice__invoice_region="", invoice__currency="BDT")
            | Q(production_orders__factory_location__iexact="bd")
        )
    return Customer.objects.filter(query, is_active=True, is_archived=False).distinct()


def _eligible_invoices(user, sides):
    issued = Q(financial_state__document_status="ISSUED") | Q(status__in=("sent", "partial"))
    return (
        scope_invoices_for_user(
            Invoice.objects.filter(
                issued,
                is_archived=False,
                total_amount__gt=F("paid_amount"),
            ).select_related("customer", "order", "financial_state"),
            user,
        )
        .filter(_invoice_side_query(sides))
        .exclude(status="cancelled")
        .distinct()
        .order_by("due_date", "id")
    )


def _departments_for_sides(sides):
    return Department.objects.filter(
        is_active=True,
        employees__user__access__role__in=sides,
    ).distinct()


class InvoiceBalanceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, invoice):
        date_label = invoice.effective_invoice_date.isoformat() if invoice.effective_invoice_date else "No date"
        return (
            f"{invoice.invoice_number} - {date_label} - {invoice.currency} {invoice.total_amount:,.2f} total - "
            f"{invoice.paid_amount:,.2f} paid - {invoice.balance:,.2f} outstanding - "
            f"{invoice.payment_status_label}"
        )


class SupplierBillBalanceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, bill):
        return (
            f"{bill.bill_number} - {bill.currency} {bill.total_amount:,.2f} total; "
            f"{bill.remaining_amount:,.2f} outstanding"
        )


def _country_context(side):
    return {"CA": ("Canada", "CAD"), "BD": ("Bangladesh", "BDT")}.get(side, ("", ""))


def _customer_side(customer):
    market = (customer.market or "").upper().strip()
    country = (customer.country or "").lower()
    if market == "BD" or market == "BANGLADESH" or "bangladesh" in country:
        return "BD"
    if market == "CA" or market == "CANADA" or "canada" in country:
        return "CA"
    return ""


def _category_group(category):
    text = f"{category.code} {category.name} {category.subcategory}".upper()
    groups = (
        ("Payroll", ("SALARY", "WAGES", "BONUS", "OVERTIME", "PAYROLL")),
        ("Utilities", ("ELECTRIC", "HYDRO", "WATER", "GAS", "INTERNET", "PHONE", "UTILITY")),
        ("Marketing", ("MARKETING", "ADVERT", "PROMOTION")),
        ("Travel", ("TRAVEL", "VEHICLE", "TRANSPORT")),
        ("Maintenance", ("MAINTENANCE", "REPAIR", "MACHINE_SERVICE", "CLEANING")),
        ("Professional Fees", ("PROFESSIONAL", "LEGAL", "CONSULT", "AUDIT")),
        ("Taxes", ("TAX", "DUTY")),
        ("Factory", ("FACTORY", "PRODUCTION", "SECURITY", "FUEL")),
        ("Office", ("OFFICE", "SOFTWARE", "INSURANCE", "BANK_FEE")),
    )
    for group, markers in groups:
        if any(marker in text for marker in markers):
            return group
    return "Other"


class GroupedExpenseCategoryIterator(ModelChoiceIterator):
    def __iter__(self):
        if self.field.empty_label is not None:
            yield ("", self.field.empty_label)
        groups = OrderedDict()
        queryset = self.queryset
        if not queryset._prefetch_related_lookups:
            queryset = queryset.iterator()
        for category in queryset:
            groups.setdefault(_category_group(category), []).append(self.choice(category))
        for group, choices in groups.items():
            yield (group, choices)


class ExpenseCategoryChoiceField(forms.ModelChoiceField):
    iterator = GroupedExpenseCategoryIterator


def _option_metadata(kind, instance, side=""):
    country, currency = _country_context(side)
    if kind == "customer":
        customer_side = side or _customer_side(instance)
        country, currency = _country_context(customer_side)
        primary = instance.account_brand or instance.contact_name or "Customer"
        secondary = " | ".join(
            value for value in (instance.customer_code, country or instance.country, currency, instance.phone, instance.email)
            if value
        )
        search = " ".join(
            str(value or "")
            for value in (primary, instance.customer_code, instance.contact_name, instance.phone, instance.email, instance.country)
        )
        return {"primary": primary, "secondary": secondary, "search": search, "side": customer_side}
    if kind == "invoice":
        customer = instance.customer
        invoice_side = instance.invoice_region or ("BD" if instance.currency == "BDT" else "CA")
        customer_name = customer.account_brand or customer.contact_name if customer else "Customer unavailable"
        order_reference = ""
        if instance.order:
            order_reference = instance.order.purchase_order_number or instance.order.title or str(instance.order.pk)
        reference = (instance.notes or "").strip()
        secondary = (
            f"{instance.effective_invoice_date:%b %d, %Y} | {customer_name} | "
            f"{instance.currency} {instance.total_amount:,.2f} total | {instance.paid_amount:,.2f} paid | "
            f"{instance.balance:,.2f} outstanding | {instance.payment_status_label}"
        )
        if order_reference:
            secondary += f" | PO {order_reference}"
        search = " ".join(
            str(value or "")
            for value in (
                instance.invoice_number, customer_name, reference, instance.total_amount,
                instance.paid_amount, instance.balance, order_reference,
            )
        )
        return {
            "primary": instance.invoice_number,
            "secondary": secondary,
            "search": search,
            "customer-id": instance.customer_id or "",
            "side": invoice_side,
            "currency": instance.currency,
            "total": instance.total_amount,
            "paid": instance.paid_amount,
            "outstanding": instance.balance,
            "status": instance.payment_status_label,
        }
    if kind == "supplier":
        secondary = " | ".join(
            value for value in (
                instance.code, instance.get_side_display(), instance.default_currency,
                instance.contact_name, instance.phone, instance.email,
            ) if value
        )
        return {
            "primary": instance.name,
            "secondary": secondary,
            "search": f"{instance.name} {instance.code} {instance.contact_name} {instance.phone} {instance.email}",
            "side": instance.side,
            "currency": instance.default_currency,
        }
    if kind == "account":
        return {
            "primary": f"{instance.name} | {instance.currency}",
            "secondary": f"{instance.get_side_display()} | {instance.get_kind_display()}",
            "search": f"{instance.name} {instance.currency} {instance.get_side_display()} {instance.get_kind_display()}",
            "side": instance.side,
            "currency": instance.currency,
        }
    if kind == "category":
        return {
            "primary": instance.name,
            "secondary": " | ".join(value for value in (instance.subcategory, instance.code) if value),
            "search": f"{instance.name} {instance.subcategory} {instance.code} {_category_group(instance)}",
            "group": _category_group(instance),
        }
    if kind == "supplier_bill":
        return {
            "primary": instance.bill_number,
            "secondary": (
                f"{instance.supplier.name} | {instance.currency} {instance.total_amount:,.2f} total | "
                f"{instance.remaining_amount:,.2f} outstanding"
            ),
            "search": f"{instance.bill_number} {instance.supplier.name} {instance.total_amount} {instance.remaining_amount}",
            "supplier-id": instance.supplier_id,
            "currency": instance.currency,
            "outstanding": instance.remaining_amount,
        }
    return {"primary": str(instance), "secondary": "", "search": str(instance)}


class FinanceSearchSelect(forms.Select):
    def __init__(self, *args, finance_kind="record", search_placeholder="Search...", empty_message="No results.", **kwargs):
        self.finance_kind = finance_kind
        attrs = dict(kwargs.pop("attrs", {}) or {})
        attrs.update(
            {
                "class": "form-select finance-native-select finance-searchable-select",
                "data-finance-search": finance_kind,
                "data-search-placeholder": search_placeholder,
                "data-empty-message": empty_message,
            }
        )
        super().__init__(*args, attrs=attrs, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            metadata = _option_metadata(self.finance_kind, instance, self.attrs.get("data-finance-side", ""))
            for key, item in metadata.items():
                option["attrs"][f"data-{key}"] = str(item)
        return option


class FinanceOperationForm(forms.Form):
    transaction_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    side = forms.ChoiceField(choices=(("CA", "Canada"), ("BD", "Bangladesh")))
    currency = forms.ChoiceField(choices=CURRENCIES)
    rate_to_cad = forms.DecimalField(
        max_digits=20,
        decimal_places=10,
        required=False,
        help_text="Optional when an approved rate exists for the transaction date.",
    )
    rate_to_bdt = forms.DecimalField(
        max_digits=20,
        decimal_places=10,
        required=False,
        help_text="Optional when an approved rate exists for the transaction date.",
    )
    reference = forms.CharField(max_length=120, required=False)
    business_purpose = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    supporting_document = forms.FileField(
        required=False,
        help_text="Optional. You can attach a receipt or supporting document now or add it later.",
    )

    def __init__(self, *args, user=None, workflow=None, locked_side="", **kwargs):
        self.user = user
        self.workflow = workflow
        self.locked_side = (locked_side or "").upper().strip()
        super().__init__(*args, **kwargs)
        sides = accessible_financial_sides(user)
        if self.locked_side and self.locked_side not in sides:
            raise forms.ValidationError("You do not have access to this business side.")
        self.allowed_sides = {self.locked_side} if self.locked_side else sides
        self.fields["side"].choices = [choice for choice in self.fields["side"].choices if choice[0] in sides]
        if self.locked_side:
            self.fields["side"].initial = self.locked_side
            self.fields["side"].widget = forms.HiddenInput()
            self.fields["currency"].initial = "BDT" if self.locked_side == "BD" else "CAD"
        elif len(sides) == 1:
            self.fields["side"].initial = next(iter(sides))
        self.fields["transaction_date"].initial = date.today
        self.fields["transaction_date"].label = "Date"
        self.fields["reference"].label = "Reference"
        self.fields["business_purpose"].label = "Purpose"
        self.fields["supporting_document"].label = "Receipt"
        self._configure_searchable_fields()
        for name, field in self.fields.items():
            if field.required:
                field.error_messages["required"] = self._required_message(name, field)
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")

    @staticmethod
    def _required_message(name, field):
        messages = {
            "transaction_date": "Please enter the transaction date.",
            "customer": "Please select a customer.",
            "invoice": "Please select an invoice.",
            "supplier": "Please select a supplier.",
            "supplier_bill": "Please select a supplier bill.",
            "amount": "Please enter the transaction amount.",
            "total": "Please enter the total amount.",
            "payment_account": "Please select the bank or cash account.",
        }
        if name in messages:
            return messages[name]
        label = (field.label or name.replace("_", " ")).lower()
        if isinstance(field, (forms.ModelChoiceField, forms.ChoiceField)):
            return f"Please select {label}."
        return f"Please enter {label}."

    def draft_form_values(self):
        values = {}
        for name, field in self.fields.items():
            if name == "supporting_document":
                continue
            value = self.cleaned_data.get(name)
            if isinstance(field, forms.ModelMultipleChoiceField):
                value = [item.pk for item in value]
            elif isinstance(field, forms.ModelChoiceField):
                value = value.pk if value is not None else ""
            values[name] = _json_value(value) if value is not None else ""
        return values

    def _configure_searchable_fields(self):
        country_name, _currency = _country_context(self.locked_side)
        kind_by_model = {
            Customer: "customer",
            Invoice: "invoice",
            Supplier: "supplier",
            CashBankAccount: "account",
            ExpenseCategory: "category",
            SupplierBill: "supplier_bill",
            ProductionOrder: "production_order",
            Department: "department",
            EmployeeProfile: "staff",
            QuickCosting: "costing",
            FactoryRunningCostDefault: "factory_rate",
            InventoryItem: "inventory",
        }
        placeholders = {
            "customer": "Search customer...",
            "invoice": "Search invoice...",
            "supplier": "Search supplier...",
            "account": "Search payment account...",
            "category": "Search expense category...",
            "supplier_bill": "Search supplier bill...",
            "production_order": "Search production order...",
            "department": "Search department...",
            "staff": "Search staff...",
            "costing": "Search Quick Costing...",
            "factory_rate": "Search factory rate...",
            "inventory": "Search inventory...",
        }
        empty_messages = {
            "customer": f"No {country_name or 'eligible'} customers found.",
            "invoice": "Select a customer to view eligible invoices.",
            "supplier": f"No {country_name or 'eligible'} suppliers configured.",
            "account": f"No {country_name or 'eligible'} payment accounts configured.",
            "category": "No active expense categories configured.",
            "supplier_bill": "Select a supplier to view approved outstanding bills.",
        }
        for field in self.fields.values():
            if not isinstance(field, forms.ModelChoiceField):
                continue
            kind = kind_by_model.get(field.queryset.model, "record")
            widget = FinanceSearchSelect(
                finance_kind=kind,
                search_placeholder=placeholders.get(kind, "Search records..."),
                empty_message=empty_messages.get(kind, "No matching records."),
            )
            if self.locked_side:
                widget.attrs["data-finance-side"] = self.locked_side
            if kind == "invoice":
                widget.attrs["data-parent-field"] = "customer"
            elif kind == "supplier_bill":
                widget.attrs["data-parent-field"] = "supplier"
            field.widget = widget
            field.widget.choices = field.choices

    def clean_side(self):
        side = self.cleaned_data["side"]
        if self.locked_side and side != self.locked_side:
            raise forms.ValidationError("The business side is locked for this transaction.")
        return side

    def _operation(self, *, operation_type=None, total_amount, amount_before_tax=0, tax_amount=0, details=None, **fields):
        cleaned = self.cleaned_data
        transaction_date = fields.pop("transaction_date", cleaned["transaction_date"])
        reference = fields.pop("reference", (cleaned.get("reference") or "").strip())
        return FinanceOperation(
            operation_type=operation_type or self.workflow["operation_type"],
            transaction_date=transaction_date,
            side=cleaned["side"],
            currency=cleaned["currency"],
            amount_before_tax=money(amount_before_tax),
            tax_amount=money(tax_amount),
            total_amount=money(total_amount),
            rate_to_cad=cleaned.get("rate_to_cad") or Decimal("0"),
            rate_to_bdt=cleaned.get("rate_to_bdt") or Decimal("0"),
            amount_cad=Decimal("0"),
            amount_bdt=Decimal("0"),
            reference=reference,
            business_purpose=cleaned["business_purpose"].strip(),
            notes=(cleaned.get("notes") or "").strip(),
            details={key: _json_value(value) for key, value in (details or {}).items()},
            **fields,
        )

    def _accounts(self):
        return scope_bank_accounts_for_user(
            CashBankAccount.objects.filter(is_active=True).select_related("gl_account"), self.user
        ).filter(side__in=self.allowed_sides).order_by("side", "name")


class CustomerPaymentOperationForm(FinanceOperationForm):
    customer = forms.ModelChoiceField(queryset=Customer.objects.none())
    invoice = InvoiceBalanceChoiceField(queryset=Invoice.objects.none())
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    payment_method = forms.ChoiceField(choices=CUSTOMER_PAYMENT_METHOD_CHOICES)
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())
    allow_customer_credit = forms.BooleanField(
        required=False, label="Approve excess as unapplied customer credit"
    )
    duplicate_override = forms.BooleanField(required=False, label="I reviewed the duplicate reference warning")
    duplicate_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        invoices = _eligible_invoices(self.user, self.allowed_sides)
        self.fields["invoice"].queryset = invoices
        self.fields["customer"].queryset = _customers_for_sides(self.allowed_sides).order_by(
            "account_brand", "contact_name"
        )
        self.fields["payment_account"].queryset = self._accounts().filter(
            kind__in=(
                CashBankAccount.KIND_BANK,
                CashBankAccount.KIND_PAYPAL,
                CashBankAccount.KIND_CASH,
            ),
            gl_account__account_type=FinancialAccount.TYPE_ASSET,
            gl_account__is_active=True,
            gl_account__system_key__isnull=False,
        )
        self.fields["customer"].label = "Customer"
        self.fields["invoice"].label = "Invoice"
        self.fields["amount"].label = "Amount"
        self.fields["payment_account"].label = "Payment Account"

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        invoice = cleaned.get("invoice")
        amount = cleaned.get("amount")
        if customer and invoice and invoice.customer_id != customer.pk:
            self.add_error("invoice", "This invoice does not belong to the selected customer.")
        if invoice and cleaned.get("currency") != invoice.currency:
            self.add_error("currency", "Payment currency must match the invoice currency.")
        if invoice and cleaned.get("side") != (invoice.invoice_region or ("BD" if invoice.currency == "BDT" else "CA")):
            self.add_error("side", "The business side must match the invoice.")
        if invoice and amount and amount > invoice.balance and not cleaned.get("allow_customer_credit"):
            self.add_error(
                "amount",
                "Amount exceeds the remaining balance. Confirm that the excess is an approved customer credit.",
            )
        if invoice and invoice.balance <= 0:
            self.add_error("invoice", "This invoice has no remaining balance; use Unapplied Customer Credit instead.")
        account = cleaned.get("payment_account")
        if account and account.currency != cleaned.get("currency"):
            self.add_error("payment_account", "Payment account currency must match the payment currency.")
        if account and cleaned.get("side") and cleaned.get("currency") and cleaned.get("payment_method"):
            try:
                validate_customer_payment_account(
                    payment_account=account,
                    side=cleaned["side"],
                    currency=cleaned["currency"],
                    payment_method=cleaned["payment_method"],
                )
            except ReceivableAccountingError as exc:
                self.add_error("payment_account", str(exc))
        duplicate = False
        reference = (cleaned.get("reference") or "").strip()
        if customer and reference:
            duplicate = customer_payment_reference_exists(customer, reference)
        if duplicate and not cleaned.get("duplicate_override"):
            self.add_error("duplicate_override", "A matching payment reference already exists. Review it before continuing.")
        if duplicate and len((cleaned.get("duplicate_reason") or "").strip()) < 5:
            self.add_error("duplicate_reason", "Explain why this reference is valid before submitting it again.")
        cleaned["duplicate_detected"] = duplicate
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        operation = self._operation(
            total_amount=cleaned["amount"],
            customer=cleaned["customer"],
            invoice=cleaned["invoice"],
            to_account=cleaned["payment_account"],
            payment_method=cleaned["payment_method"],
            reason=cleaned.get("duplicate_reason", ""),
            details={
                "remaining_balance_at_submission": cleaned["invoice"].balance,
                "allow_customer_credit": cleaned.get("allow_customer_credit", False),
            },
        )
        operation.duplicate_warning = cleaned.get("duplicate_detected", False)
        operation.duplicate_warning_text = (
            f"Duplicate reference approved for review: {cleaned.get('duplicate_reason', '')}"
            if operation.duplicate_warning else ""
        )
        return operation


class CustomerAdjustmentOperationForm(FinanceOperationForm):
    KIND_CHOICES = (
        (FinanceOperation.TYPE_CUSTOMER_REFUND, "Customer refund"),
        (FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE, "Credit note"),
        (FinanceOperation.TYPE_CUSTOMER_CREDIT, "Unapplied customer credit"),
    )
    adjustment_kind = forms.ChoiceField(choices=KIND_CHOICES)
    customer = forms.ModelChoiceField(queryset=Customer.objects.none())
    invoice = InvoiceBalanceChoiceField(queryset=Invoice.objects.none(), required=False)
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    refund_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none(), required=False)
    deposit_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none(), required=False)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, initial_kind=None, **kwargs):
        super().__init__(*args, **kwargs)
        invoices = _eligible_invoices(self.user, self.allowed_sides)
        self.fields["invoice"].queryset = invoices
        self.fields["customer"].queryset = _customers_for_sides(self.allowed_sides).order_by(
            "account_brand", "contact_name"
        )
        self.fields["refund_account"].queryset = self._accounts()
        self.fields["deposit_account"].queryset = self._accounts()
        if initial_kind:
            self.fields["adjustment_kind"].initial = initial_kind

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("adjustment_kind")
        customer = cleaned.get("customer")
        invoice = cleaned.get("invoice")
        amount = cleaned.get("amount") or Decimal("0")
        if invoice and customer and invoice.customer_id != customer.pk:
            self.add_error("invoice", "This invoice does not belong to the selected customer.")
        if kind == FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE and not invoice:
            self.add_error("invoice", "A credit note must reference an invoice.")
        if kind == FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE and invoice and amount > invoice.balance:
            self.add_error("amount", "A credit note cannot exceed the invoice outstanding balance.")
        if kind == FinanceOperation.TYPE_CUSTOMER_REFUND and not cleaned.get("refund_account"):
            self.add_error("refund_account", "Select the account funding the refund.")
        if kind == FinanceOperation.TYPE_CUSTOMER_CREDIT and not cleaned.get("deposit_account"):
            self.add_error("deposit_account", "Select the account receiving the customer credit.")
        expected_currency = invoice.currency if invoice else cleaned.get("currency")
        if invoice and cleaned.get("currency") != expected_currency:
            self.add_error("currency", "Currency must match the referenced invoice.")
        account = cleaned.get("refund_account") or cleaned.get("deposit_account")
        if account and account.currency != cleaned.get("currency"):
            self.add_error("currency", "Currency must match the selected account.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        return self._operation(
            operation_type=cleaned["adjustment_kind"],
            total_amount=cleaned["amount"],
            customer=cleaned["customer"],
            invoice=cleaned.get("invoice"),
            from_account=cleaned.get("refund_account"),
            to_account=cleaned.get("deposit_account"),
            reason=cleaned["reason"],
        )


class SupplierBillOperationForm(FinanceOperationForm):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    bill_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount_before_tax = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    tax = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        initial=Decimal("0"),
    )
    total = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    category = ExpenseCategoryChoiceField(queryset=ExpenseCategory.objects.none())
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    production_order = forms.ModelChoiceField(queryset=ProductionOrder.objects.none(), required=False)
    customer = forms.ModelChoiceField(queryset=Customer.objects.all(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sides = self.allowed_sides
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True, side__in=sides).order_by("name")
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True).select_related("default_account")
        self.fields["department"].queryset = _departments_for_sides(sides)
        self.fields["production_order"].queryset = _production_orders_for_sides(sides).order_by("-created_at")
        self.fields["customer"].queryset = _customers_for_sides(sides).order_by("account_brand", "contact_name")
        self.fields["reference"].label = "Bill Number"
        self.fields["amount_before_tax"].label = "Subtotal"
        self.fields["tax"].label = "Tax"
        self.fields["total"].label = "Total"

    def clean(self):
        cleaned = super().clean()
        before = cleaned.get("amount_before_tax") or Decimal("0")
        tax = cleaned.get("tax") or Decimal("0")
        if cleaned.get("total") != before + tax:
            self.add_error("total", "Total must equal amount before tax plus tax.")
        if cleaned.get("due_date") and cleaned.get("bill_date") and cleaned["due_date"] < cleaned["bill_date"]:
            self.add_error("due_date", "Due date cannot precede the bill date.")
        supplier = cleaned.get("supplier")
        reference = (cleaned.get("reference") or "").strip()
        if supplier and reference and (
            SupplierBill.objects.filter(supplier=supplier, bill_number__iexact=reference).exists()
            or FinanceOperation.objects.filter(
                operation_type=FinanceOperation.TYPE_SUPPLIER_BILL,
                supplier=supplier,
                reference__iexact=reference,
            ).exclude(state=FinanceOperation.STATE_REJECTED).exists()
        ):
            self.add_error("reference", "This supplier already has a bill with the same number.")
        if supplier and cleaned.get("side") != supplier.side:
            self.add_error("side", "Business side must match the supplier.")
        if supplier and cleaned.get("currency") != supplier.default_currency:
            self.add_error("currency", "Currency must match the supplier unless its approved default is changed.")
        order = cleaned.get("production_order")
        if order:
            expected_side = "BD" if (order.factory_location or "").lower() == "bd" else "CA"
            if cleaned.get("side") != expected_side:
                self.add_error("production_order", "Production order and bill business sides must match.")
            if cleaned.get("customer") and order.customer_id != cleaned["customer"].pk:
                self.add_error("customer", "The selected customer does not own this production order.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        category = cleaned["category"]
        return self._operation(
            transaction_date=cleaned["bill_date"],
            total_amount=cleaned["total"],
            amount_before_tax=cleaned["amount_before_tax"],
            tax_amount=cleaned["tax"],
            supplier=cleaned["supplier"],
            customer=cleaned.get("customer"),
            expense_category=category,
            department=cleaned.get("department"),
            production_order=cleaned.get("production_order"),
            details={
                "bill_date": cleaned["bill_date"],
                "due_date": cleaned["due_date"],
                "cost_classification": "PRODUCTION" if category.is_production_cost else "OPERATING",
            },
        )


class SupplierPaymentOperationForm(FinanceOperationForm):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    supplier_bill = SupplierBillBalanceChoiceField(queryset=SupplierBill.objects.none())
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    payment_method = forms.ChoiceField(choices=PAYMENT_METHODS)
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sides = self.allowed_sides
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True, side__in=sides).order_by("name")
        self.fields["supplier_bill"].queryset = SupplierBill.objects.filter(
            side__in=sides,
            approval_status=SupplierBill.APPROVAL_APPROVED,
        ).exclude(payment_status=SupplierBill.PAYMENT_PAID).select_related("supplier")
        self.fields["payment_account"].queryset = self._accounts()

    def clean(self):
        cleaned = super().clean()
        supplier = cleaned.get("supplier")
        bill = cleaned.get("supplier_bill")
        amount = cleaned.get("amount") or Decimal("0")
        if supplier and bill and bill.supplier_id != supplier.pk:
            self.add_error("supplier_bill", "This bill does not belong to the selected supplier.")
        if bill and amount > bill.remaining_amount:
            self.add_error("amount", "Payment cannot exceed the supplier bill outstanding balance.")
        if bill and cleaned.get("currency") != bill.currency:
            self.add_error("currency", "Payment currency must match the supplier bill.")
        if bill and cleaned.get("side") != bill.side:
            self.add_error("side", "Payment and supplier bill business sides must match.")
        account = cleaned.get("payment_account")
        if account and account.currency != cleaned.get("currency"):
            self.add_error("payment_account", "Payment account currency must match the bill.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        bill = cleaned["supplier_bill"]
        return self._operation(
            total_amount=cleaned["amount"],
            supplier=cleaned["supplier"],
            supplier_bill=bill,
            from_account=cleaned["payment_account"],
            payment_method=cleaned["payment_method"],
            details={
                "bill_total": bill.total_amount,
                "already_paid": bill.paid_amount,
                "outstanding_at_submission": bill.remaining_amount,
            },
        )


class ExpenseOperationForm(FinanceOperationForm):
    vendor = forms.ModelChoiceField(queryset=Supplier.objects.none(), required=False)
    vendor_name = forms.CharField(max_length=200, required=False)
    category = ExpenseCategoryChoiceField(queryset=ExpenseCategory.objects.none())
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    amount_before_tax = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    tax = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    total = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    payment_status = forms.ChoiceField(choices=(("UNPAID", "Unpaid"), ("PAID", "Paid")))
    payment_method = forms.ChoiceField(choices=PAYMENT_METHODS, required=False)
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none(), required=False)
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    recurring = forms.BooleanField(required=False)
    production_order = forms.ModelChoiceField(queryset=ProductionOrder.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sides = self.allowed_sides
        self.fields["vendor"].queryset = Supplier.objects.filter(is_active=True, side__in=sides).order_by("name")
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True).select_related("default_account")
        self.fields["department"].queryset = _departments_for_sides(sides)
        self.fields["production_order"].queryset = _production_orders_for_sides(sides).order_by("-created_at")
        self.fields["payment_account"].queryset = self._accounts()
        self.fields["amount_before_tax"].label = "Subtotal"
        self.fields["tax"].label = "Tax"
        self.fields["total"].label = "Total"
        self.fields["payment_account"].label = "Payment Account"

    def clean(self):
        cleaned = super().clean()
        before = cleaned.get("amount_before_tax") or Decimal("0")
        tax = cleaned.get("tax") or Decimal("0")
        if cleaned.get("total") != before + tax:
            self.add_error("total", "Total must equal amount before tax plus tax.")
        if not cleaned.get("vendor") and not (cleaned.get("vendor_name") or "").strip():
            self.add_error("vendor_name", "Select a vendor or enter the vendor name.")
        if cleaned.get("payment_status") == "PAID" and not cleaned.get("payment_account"):
            self.add_error("payment_account", "Paid expenses require a payment account.")
        if cleaned.get("payment_status") == "UNPAID" and not cleaned.get("vendor"):
            self.add_error("vendor", "Unpaid expenses require a configured supplier so Accounts Payable can track them.")
        if cleaned.get("payment_status") == "UNPAID" and not cleaned.get("due_date"):
            self.add_error("due_date", "Unpaid expenses require a due date.")
        vendor = cleaned.get("vendor")
        if vendor and vendor.side != cleaned.get("side"):
            self.add_error("vendor", "Vendor and expense business sides must match.")
        order = cleaned.get("production_order")
        expected_side = "BD" if order and (order.factory_location or "").lower() == "bd" else "CA"
        if order and expected_side != cleaned.get("side"):
            self.add_error("production_order", "Production order and expense business sides must match.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        return self._operation(
            total_amount=cleaned["total"],
            amount_before_tax=cleaned["amount_before_tax"],
            tax_amount=cleaned["tax"],
            supplier=cleaned.get("vendor"),
            party_name=cleaned.get("vendor_name", ""),
            expense_category=cleaned["category"],
            department=cleaned.get("department"),
            production_order=cleaned.get("production_order"),
            from_account=cleaned.get("payment_account"),
            payment_method=cleaned.get("payment_method", ""),
            details={
                "payment_status": cleaned["payment_status"],
                "due_date": cleaned.get("due_date"),
                "is_recurring": cleaned.get("recurring", False),
            },
        )


class UtilityOperationForm(FinanceOperationForm):
    UTILITY_CHOICES = (
        ("ELECTRICITY", "Electricity"),
        ("HYDRO", "Hydro"),
        ("WATER", "Water"),
        ("GAS", "Gas"),
        ("INTERNET", "Internet"),
        ("TELEPHONE", "Telephone"),
        ("GENERATOR_FUEL", "Generator fuel"),
        ("OTHER_UTILITY", "Other utility"),
    )
    utility_type = forms.ChoiceField(choices=UTILITY_CHOICES)
    vendor = forms.ModelChoiceField(queryset=Supplier.objects.none())
    billing_period_start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    billing_period_end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    bill_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    location = forms.ChoiceField(choices=(("OFFICE", "Office"), ("FACTORY", "Factory")))
    meter_or_account = forms.CharField(max_length=120, required=False)
    payment_status = forms.ChoiceField(choices=(("UNPAID", "Unpaid"), ("PAID", "Paid")))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sides = self.allowed_sides
        self.fields["vendor"].queryset = Supplier.objects.filter(is_active=True, side__in=sides).order_by("name")
        self.fields["payment_account"].queryset = self._accounts()
        self.fields["meter_or_account"].label = "Account or Meter Number"
        self.fields["payment_account"].label = "Payment Account"

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("billing_period_start")
        end = cleaned.get("billing_period_end")
        if start and end and end < start:
            self.add_error("billing_period_end", "Billing period end cannot precede the start.")
        if cleaned.get("due_date") and cleaned.get("bill_date") and cleaned["due_date"] < cleaned["bill_date"]:
            self.add_error("due_date", "Due date cannot precede the bill date.")
        if cleaned.get("payment_status") == "PAID" and not cleaned.get("payment_account"):
            self.add_error("payment_account", "Paid utilities require a payment account.")
        vendor = cleaned.get("vendor")
        if vendor and vendor.side != cleaned.get("side"):
            self.add_error("vendor", "Utility vendor and business side must match.")
        key = UTILITY_ACCOUNT_KEYS.get(cleaned.get("utility_type"))
        category = ExpenseCategory.objects.filter(default_account__system_key=key, is_active=True).first() if key else None
        if not category:
            self.add_error("utility_type", "This utility category is not configured in the Chart of Accounts.")
        cleaned["expense_category"] = category
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        return self._operation(
            transaction_date=cleaned["bill_date"],
            total_amount=cleaned["amount"],
            amount_before_tax=cleaned["amount"],
            supplier=cleaned["vendor"],
            expense_category=cleaned["expense_category"],
            from_account=cleaned.get("payment_account"),
            details={
                "utility_type": cleaned["utility_type"],
                "billing_period_start": cleaned["billing_period_start"],
                "billing_period_end": cleaned["billing_period_end"],
                "bill_date": cleaned["bill_date"],
                "due_date": cleaned["due_date"],
                "location": cleaned["location"],
                "meter_or_account": cleaned.get("meter_or_account", ""),
                "payment_status": cleaned["payment_status"],
            },
        )


class PayrollOperationForm(FinanceOperationForm):
    payroll_month = forms.CharField(widget=forms.TextInput(attrs={"type": "month"}))
    employee = forms.ModelChoiceField(queryset=EmployeeProfile.objects.none(), required=False)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    payroll_type = forms.ChoiceField(
        choices=(("BASE", "Base salary"), ("OVERTIME", "Overtime"), ("BONUS", "Bonus"), ("COMMISSION", "Commission"))
    )
    gross_amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    deductions = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        initial=Decimal("0"),
    )
    employer_cost = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        initial=Decimal("0"),
    )
    net_paid = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        employees = EmployeeProfile.objects.filter(
            status__in=EmployeeProfile.MENTIONABLE_STATUSES, is_archived=False
        )
        sides = self.allowed_sides
        employees = employees.filter(user__access__role__in=sides)
        self.fields["employee"].queryset = employees.select_related("user", "department_ref")
        self.fields["department"].queryset = _departments_for_sides(sides)
        self.fields["payment_account"].queryset = self._accounts()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("employee") and not cleaned.get("department"):
            self.add_error("department", "Select an employee or department.")
        if cleaned.get("employee") and cleaned.get("department"):
            self.add_error("employee", "Select either an employee or a department total, not both.")
        month = (cleaned.get("payroll_month") or "").strip()
        try:
            year, month_number = (int(part) for part in month.split("-"))
            period_start = date(year, month_number, 1)
            period_end = date(year, month_number, monthrange(year, month_number)[1])
        except (TypeError, ValueError):
            self.add_error("payroll_month", "Enter a valid payroll month.")
            period_start = period_end = None
        cleaned["period_start"] = period_start
        cleaned["period_end"] = period_end
        gross = cleaned.get("gross_amount") or Decimal("0")
        deductions = cleaned.get("deductions") or Decimal("0")
        cleaned["deductions"] = deductions
        cleaned["employer_cost"] = cleaned.get("employer_cost") or Decimal("0")
        net = cleaned.get("net_paid") or Decimal("0")
        if gross != deductions + net:
            self.add_error("net_paid", "Gross amount must equal deductions plus net paid.")
        account = cleaned.get("payment_account")
        if account and account.currency != cleaned.get("currency"):
            self.add_error("payment_account", "Payroll and payment account currencies must match.")
        employee = cleaned.get("employee")
        if employee and employee.user.access.role != cleaned.get("side"):
            self.add_error("employee", "Employee and payroll business sides must match.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        gross = cleaned["gross_amount"]
        employer = cleaned["employer_cost"]
        return self._operation(
            total_amount=gross + employer,
            employee=cleaned.get("employee"),
            department=cleaned.get("department") or getattr(cleaned.get("employee"), "department_ref", None),
            from_account=cleaned["payment_account"],
            details={
                "period_start": cleaned["period_start"],
                "period_end": cleaned["period_end"],
                "payroll_type": cleaned["payroll_type"],
                "gross_amount": gross,
                "deductions": cleaned["deductions"],
                "employer_cost": employer,
                "net_paid": cleaned["net_paid"],
            },
        )


class ProductionCostOperationForm(FinanceOperationForm):
    COST_CHOICES = ProductionCostRecordChoices = (
        ("FABRIC", "Fabric"), ("TRIMS", "Trims"), ("CUTTING", "Cutting"), ("SEWING", "Sewing"),
        ("PRINTING", "Printing"), ("EMBROIDERY", "Embroidery"), ("WASHING", "Washing"),
        ("PACKING", "Packing"), ("QUALITY_CONTROL", "Quality control"),
        ("PRODUCTION_LABOR", "Production labor"), ("SHIPPING", "Shipping"), ("DUTY", "Duty"),
        ("REWORK", "Rework"), ("WASTE", "Waste"), ("OTHER_DIRECT", "Other direct cost"),
    )
    production_order = forms.ModelChoiceField(queryset=ProductionOrder.objects.none())
    cost_category = forms.ChoiceField(choices=COST_CHOICES)
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    estimated_cost = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    actual_cost = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    payment_status = forms.ChoiceField(choices=(("UNPAID", "Unpaid"), ("PAID", "Paid")))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none(), required=False)
    payment_method = forms.ChoiceField(choices=PAYMENT_METHODS, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sides = self.allowed_sides
        self.fields["production_order"].queryset = _production_orders_for_sides(sides).select_related("customer", "opportunity")
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True, side__in=sides)
        self.fields["payment_account"].queryset = self._accounts()

    def clean(self):
        cleaned = super().clean()
        order = cleaned.get("production_order")
        expected_side = "BD" if order and (order.factory_location or "").lower() == "bd" else "CA"
        if order and cleaned.get("side") != expected_side:
            self.add_error("side", "Business side must match the production order factory.")
        if cleaned.get("payment_status") == "PAID" and not cleaned.get("payment_account"):
            self.add_error("payment_account", "Paid production costs require a payment account.")
        supplier = cleaned.get("supplier")
        if supplier and supplier.side != cleaned.get("side"):
            self.add_error("supplier", "Supplier and production cost business sides must match.")
        if supplier and supplier.default_currency != cleaned.get("currency"):
            self.add_error("currency", "Production cost currency must match the supplier currency.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        order = cleaned["production_order"]
        return self._operation(
            total_amount=cleaned["actual_cost"],
            production_order=order,
            customer=order.customer,
            opportunity=order.opportunity,
            supplier=cleaned["supplier"],
            from_account=cleaned.get("payment_account"),
            payment_method=cleaned.get("payment_method", ""),
            details={
                "cost_category": cleaned["cost_category"],
                "estimated_amount": cleaned["estimated_cost"],
                "actual_amount": cleaned["actual_cost"],
                "variance": cleaned["actual_cost"] - cleaned["estimated_cost"],
                "payment_status": cleaned["payment_status"],
            },
        )


class FactoryDailyCostOperationForm(FinanceOperationForm):
    quick_costing = forms.ModelChoiceField(queryset=QuickCosting.objects.none())
    daily_default = forms.ModelChoiceField(queryset=FactoryRunningCostDefault.objects.none())
    estimated_days = forms.IntegerField(min_value=1)
    actual_days = forms.IntegerField(min_value=1, required=False)
    estimated_revenue = forms.DecimalField(max_digits=18, decimal_places=2)
    actual_revenue = forms.DecimalField(max_digits=18, decimal_places=2, required=False)
    other_estimated_cost = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    other_actual_cost = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"), required=False)
    target_margin_percent = forms.DecimalField(max_digits=8, decimal_places=4, required=False)
    approved_minimum_margin_percent = forms.DecimalField(max_digits=8, decimal_places=4, required=False)
    delay_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supporting_document"].required = False
        self.fields["reference"].required = False
        sides = self.allowed_sides
        quick_costings = QuickCosting.objects.exclude(
            status__in=QuickCosting.INACTIVE_REPORTING_STATUSES
        ).filter(
            Q(production_order__factory_location__iexact="bd") | Q(production_order__isnull=True)
        )
        if "BD" not in sides:
            quick_costings = quick_costings.none()
        self.fields["quick_costing"].queryset = quick_costings.select_related("opportunity").distinct()
        self.fields["daily_default"].queryset = FactoryRunningCostDefault.objects.filter(
            is_active=True,
            side="BD",
            currency="BDT",
        )

    def clean(self):
        cleaned = super().clean()
        default = cleaned.get("daily_default")
        if default and cleaned.get("currency") != default.currency:
            self.add_error("currency", "Operation currency must match the saved daily rate.")
        if default and cleaned.get("side") != default.side:
            self.add_error("side", "Business side must match the daily rate.")
        actual_revenue = cleaned.get("actual_revenue")
        other_actual_cost = cleaned.get("other_actual_cost")
        if (actual_revenue is None) != (other_actual_cost is None):
            self.add_error(
                "actual_revenue" if actual_revenue is None else "other_actual_cost",
                "Actual revenue and other actual costs must both be entered to calculate actual profit.",
            )
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        default = cleaned["daily_default"]
        estimated_cost = money(Decimal(cleaned["estimated_days"]) * default.daily_amount)
        actual_cost = money(Decimal(cleaned["actual_days"]) * default.daily_amount) if cleaned.get("actual_days") else None
        operation = self._operation(
            total_amount=estimated_cost,
            reference=cleaned["quick_costing"].pk,
            details={
                "daily_default_id": default.pk,
                "pricing_type": cleaned["quick_costing"].effective_pricing_type,
                "estimated_days": cleaned["estimated_days"],
                "actual_days": cleaned.get("actual_days"),
                "daily_factory_cost": default.daily_amount,
                "estimated_timeline_cost": estimated_cost,
                "actual_timeline_cost": actual_cost,
                "estimated_revenue": cleaned["estimated_revenue"],
                "actual_revenue": cleaned.get("actual_revenue"),
                "other_estimated_cost": cleaned["other_estimated_cost"],
                "other_actual_cost": cleaned.get("other_actual_cost"),
                "target_margin_percent": cleaned.get("target_margin_percent"),
                "approved_minimum_margin_percent": cleaned.get("approved_minimum_margin_percent"),
                "delay_reason": cleaned.get("delay_reason", ""),
            },
        )
        operation.source_record = cleaned["quick_costing"]
        return operation


class BankCashOperationForm(FinanceOperationForm):
    from_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none(), required=False)
    to_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none(), required=False)
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    destination_amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"), required=False)
    destination_rate_to_cad = forms.DecimalField(max_digits=20, decimal_places=10, required=False)
    destination_rate_to_bdt = forms.DecimalField(max_digits=20, decimal_places=10, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = self._accounts()
        self.fields["from_account"].queryset = accounts
        self.fields["to_account"].queryset = accounts

    def clean(self):
        cleaned = super().clean()
        operation_type = self.workflow["operation_type"]
        source = cleaned.get("from_account")
        destination = cleaned.get("to_account")
        fee_types = {FinanceOperation.TYPE_BANK_FEE, FinanceOperation.TYPE_PROCESSOR_FEE}
        if operation_type in fee_types:
            if not source:
                self.add_error("from_account", "Select the account charged.")
        elif not source or not destination:
            self.add_error("to_account", "This movement requires both from and to accounts.")
        if source and destination and source.pk == destination.pk:
            self.add_error("to_account", "From and to accounts must be different.")
        if source and source.currency != cleaned.get("currency"):
            self.add_error("currency", "Currency must match the source account.")
        if source and cleaned.get("side") != source.side:
            self.add_error("side", "Business side must match the source account.")
        expected_kinds = {
            FinanceOperation.TYPE_BANK_DEPOSIT: (None, CashBankAccount.KIND_BANK),
            FinanceOperation.TYPE_BANK_WITHDRAWAL: (CashBankAccount.KIND_BANK, CashBankAccount.KIND_CASH),
            FinanceOperation.TYPE_CASH_DEPOSIT: (None, CashBankAccount.KIND_CASH),
            FinanceOperation.TYPE_CASH_WITHDRAWAL: (CashBankAccount.KIND_CASH, None),
        }
        expected_source, expected_destination = expected_kinds.get(operation_type, (None, None))
        if source and expected_source and source.kind != expected_source:
            self.add_error("from_account", "The selected source account does not match this workflow.")
        if destination and expected_destination and destination.kind != expected_destination:
            self.add_error("to_account", "The selected destination account does not match this workflow.")
        is_actual_cross_country = (
            operation_type == FinanceOperation.TYPE_ACCOUNT_TRANSFER
            and cleaned.get("transfer_type") in {TRANSFER_TYPE_CA_TO_BD, TRANSFER_TYPE_BD_TO_CA}
        )
        if destination and source and destination.currency != source.currency and not is_actual_cross_country:
            if not cleaned.get("destination_amount"):
                self.add_error("destination_amount", "Cross-currency transfers require the evidenced destination amount.")
            elif cleaned.get("amount") and cleaned.get("transaction_date"):
                try:
                    source_snapshot = resolve_currency_snapshot(
                        native_amount=cleaned["amount"],
                        currency=source.currency,
                        transaction_date=cleaned["transaction_date"],
                        rate_to_cad=cleaned.get("rate_to_cad"),
                        rate_to_bdt=cleaned.get("rate_to_bdt"),
                        create_review=False,
                    )
                    destination_snapshot = resolve_currency_snapshot(
                        native_amount=cleaned["destination_amount"],
                        currency=destination.currency,
                        transaction_date=cleaned["transaction_date"],
                        rate_to_cad=cleaned.get("destination_rate_to_cad"),
                        rate_to_bdt=cleaned.get("destination_rate_to_bdt"),
                        create_review=False,
                    )
                    if abs(source_snapshot.amount_cad - destination_snapshot.amount_cad) > Decimal("0.01"):
                        self.add_error(
                            "destination_amount",
                            "Source and destination CAD equivalents must reconcile; record any fee separately.",
                        )
                except MissingExchangeRate as exc:
                    self.add_error("destination_rate_to_cad", str(exc))
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        destination = cleaned.get("to_account")
        return self._operation(
            total_amount=cleaned["amount"],
            from_account=cleaned.get("from_account"),
            to_account=destination,
            details={
                "destination_amount": cleaned.get("destination_amount") or cleaned["amount"],
                "destination_currency": destination.currency if destination else cleaned["currency"],
                "destination_rate_to_cad": cleaned.get("destination_rate_to_cad"),
                "destination_rate_to_bdt": cleaned.get("destination_rate_to_bdt"),
            },
        )


class MoneyTransferOperationForm(BankCashOperationForm):
    transfer_type = forms.ChoiceField(choices=TRANSFER_TYPE_CHOICES)
    transfer_service = forms.ChoiceField(choices=TRANSFER_SERVICE_CHOICES, required=False)
    other_transfer_service = forms.CharField(max_length=120, required=False)
    receiving_currency = forms.ChoiceField(choices=CURRENCIES)
    provider_exchange_rate = forms.DecimalField(
        max_digits=20,
        decimal_places=10,
        min_value=Decimal("0.0000000001"),
        required=False,
        help_text="Optional provider-approved quoted rate. The effective rate is calculated from the actual amounts.",
    )
    transfer_fee = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
    )
    fee_currency = forms.ChoiceField(choices=CURRENCIES, required=False)
    business_purpose = forms.ChoiceField(choices=TRANSFER_PURPOSE_CHOICES, required=False, label="Purpose")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        accessible_sides = accessible_financial_sides(self.user)
        accounts = scope_bank_accounts_for_user(
            CashBankAccount.objects.filter(is_active=True).select_related("gl_account"), self.user
        ).filter(side__in=accessible_sides).order_by("side", "name")
        self.fields["from_account"].queryset = accounts
        self.fields["to_account"].queryset = accounts
        self.fields["transaction_date"].label = "Transaction Date"
        self.fields["from_account"].label = "From Account"
        self.fields["to_account"].label = "Destination Account"
        self.fields["amount"].label = "Amount Sent"
        self.fields["currency"].label = "Sending Currency"
        self.fields["destination_amount"].label = "Amount Received"
        self.fields["provider_exchange_rate"].label = "Exchange Rate (Provider)"
        self.fields["transfer_fee"].label = "Transfer Fee"
        self.fields["fee_currency"].label = "Transfer Fee Currency"
        self.fields["other_transfer_service"].label = "Other Service Name"
        self.fields["destination_amount"].help_text = "Enter the actual amount delivered by the transfer provider."
        self.fields["currency"].help_text = "Defaults by direction and must match the selected source account."
        self.fields["receiving_currency"].help_text = "Defaults by direction and must match the destination account."
        if self.locked_side == "CA":
            self.fields["transfer_type"].choices = (
                TRANSFER_TYPE_CHOICES[0], TRANSFER_TYPE_CHOICES[1]
            )
        elif self.locked_side == "BD":
            self.fields["transfer_type"].choices = (
                TRANSFER_TYPE_CHOICES[0], TRANSFER_TYPE_CHOICES[2]
            )
        if not self.is_bound:
            transfer_type = self.initial.get("transfer_type") or TRANSFER_TYPE_INTERNAL
            if self.locked_side == "CA":
                default_sending, default_receiving = "CAD", "CAD"
            elif self.locked_side == "BD":
                default_sending, default_receiving = "BDT", "BDT"
            else:
                default_sending, default_receiving = "CAD", "CAD"
            if transfer_type == TRANSFER_TYPE_CA_TO_BD:
                default_sending, default_receiving = "CAD", "BDT"
            elif transfer_type == TRANSFER_TYPE_BD_TO_CA:
                default_sending, default_receiving = "BDT", "CAD"
            self.fields["transfer_type"].initial = transfer_type
            self.fields["currency"].initial = self.initial.get("currency") or default_sending
            self.fields["receiving_currency"].initial = (
                self.initial.get("receiving_currency") or default_receiving
            )
            self.fields["fee_currency"].initial = self.initial.get("fee_currency") or default_sending

    def _derive_cross_country_snapshots(self, cleaned, source, destination):
        amount_sent = cleaned["amount"]
        amount_received = cleaned["destination_amount"]
        source_currency = source.currency
        destination_currency = destination.currency
        if source_currency == destination_currency:
            if money(amount_sent) != money(amount_received):
                self.add_error(
                    "destination_amount",
                    "Same-currency transfers must record the same principal amount on both sides.",
                )
                return
        else:
            if source_currency == "CAD":
                cleaned["destination_rate_to_cad"] = amount_sent / amount_received
            elif destination_currency == "CAD":
                cleaned["rate_to_cad"] = amount_received / amount_sent
            if source_currency == "BDT":
                cleaned["destination_rate_to_bdt"] = amount_sent / amount_received
            elif destination_currency == "BDT":
                cleaned["rate_to_bdt"] = amount_received / amount_sent
        try:
            source_snapshot = resolve_currency_snapshot(
                native_amount=amount_sent,
                currency=source_currency,
                transaction_date=cleaned["transaction_date"],
                rate_to_cad=cleaned.get("rate_to_cad"),
                rate_to_bdt=cleaned.get("rate_to_bdt"),
                create_review=False,
            )
            destination_snapshot = resolve_currency_snapshot(
                native_amount=amount_received,
                currency=destination_currency,
                transaction_date=cleaned["transaction_date"],
                rate_to_cad=cleaned.get("destination_rate_to_cad"),
                rate_to_bdt=cleaned.get("destination_rate_to_bdt"),
                create_review=False,
            )
            if (
                abs(source_snapshot.amount_cad - destination_snapshot.amount_cad) > Decimal("0.01")
                or abs(source_snapshot.amount_bdt - destination_snapshot.amount_bdt) > Decimal("0.01")
            ):
                self.add_error(
                    "destination_amount",
                    "The saved transfer snapshots do not reconcile. Review the actual amounts and exchange rate.",
                )
                return
            cleaned["rate_to_cad"] = source_snapshot.rate_to_cad
            cleaned["rate_to_bdt"] = source_snapshot.rate_to_bdt
            cleaned["destination_rate_to_cad"] = destination_snapshot.rate_to_cad
            cleaned["destination_rate_to_bdt"] = destination_snapshot.rate_to_bdt
        except MissingExchangeRate as exc:
            self.add_error("provider_exchange_rate", str(exc))

    def clean(self):
        cleaned = super().clean()
        transfer_type = cleaned.get("transfer_type")
        source = cleaned.get("from_account")
        destination = cleaned.get("to_account")
        if not source or not destination or not transfer_type:
            return cleaned

        expected_sides = {
            TRANSFER_TYPE_CA_TO_BD: ("CA", "BD"),
            TRANSFER_TYPE_BD_TO_CA: ("BD", "CA"),
        }
        cross_country = transfer_type in expected_sides
        if cross_country:
            source_side, destination_side = expected_sides[transfer_type]
            if not {source_side, destination_side}.issubset(accessible_financial_sides(self.user)):
                self.add_error("transfer_type", "You need access to both business sides for this transfer.")
            if source.side != source_side:
                source_country = "Canada" if source_side == "CA" else "Bangladesh"
                self.add_error("from_account", f"Select a {source_country} source account for this direction.")
            if destination.side != destination_side:
                destination_country = "Canada" if destination_side == "CA" else "Bangladesh"
                self.add_error(
                    "to_account", f"Select a {destination_country} destination account."
                )
            if self.locked_side and self.locked_side != source_side:
                self.add_error("transfer_type", "This direction must start from the selected country Finance page.")
            if not cleaned.get("destination_amount"):
                self.add_error("destination_amount", "Please enter the actual amount received.")
            if not cleaned.get("transfer_service"):
                self.add_error("transfer_service", "Please select the transfer service.")
            if (
                cleaned.get("transfer_service") == "OTHER"
                and not (cleaned.get("other_transfer_service") or "").strip()
            ):
                self.add_error("other_transfer_service", "Please enter the approved transfer service name.")
        elif source.side != destination.side:
            self.add_error("transfer_type", "Use a cross-country transfer type when accounts belong to different countries.")

        if cleaned.get("currency") != source.currency:
            self.add_error("currency", "Sending currency must match the source account.")
        if cleaned.get("receiving_currency") != destination.currency:
            self.add_error("receiving_currency", "Receiving currency must match the destination account.")

        fee = cleaned.get("transfer_fee") or Decimal("0")
        fee_currency = cleaned.get("fee_currency") or source.currency
        cleaned["fee_currency"] = fee_currency
        if fee and fee_currency != source.currency:
            self.add_error("fee_currency", "Transfer fee currency must match the charged source account.")

        if (
            cross_country
            and cleaned.get("amount")
            and cleaned.get("destination_amount")
            and cleaned.get("transaction_date")
        ):
            self._derive_cross_country_snapshots(cleaned, source, destination)
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        source = cleaned["from_account"]
        destination = cleaned["to_account"]
        transfer_type = cleaned["transfer_type"]
        destination_amount = cleaned.get("destination_amount") or cleaned["amount"]
        effective_rate = destination_amount / cleaned["amount"]
        transfer_type_display = dict(TRANSFER_TYPE_CHOICES)[transfer_type]
        service_code = cleaned.get("transfer_service") or ""
        service_display = dict(TRANSFER_SERVICE_CHOICES).get(service_code, "")
        if service_code == "OTHER":
            service_display = (cleaned.get("other_transfer_service") or "").strip()
        purpose_code = cleaned.get("business_purpose") or ""
        purpose_display = dict(TRANSFER_PURPOSE_CHOICES).get(purpose_code, "")
        if source.currency == "CAD" and destination.currency == "BDT":
            effective_rate_display = f"1 CAD = {_rate_text(effective_rate)} BDT"
        elif source.currency == "BDT" and destination.currency == "CAD":
            cad_to_bdt = cleaned["amount"] / destination_amount
            effective_rate_display = f"1 CAD = {_rate_text(cad_to_bdt)} BDT"
        else:
            effective_rate_display = (
                f"1 {source.currency} = {_rate_text(effective_rate)} {destination.currency}"
            )
        operation = self._operation(
            total_amount=cleaned["amount"],
            from_account=source,
            to_account=destination,
            details={
                "transfer_type": transfer_type,
                "transfer_type_display": transfer_type_display,
                "transfer_service": service_code,
                "transfer_service_display": service_display,
                "other_transfer_service": (cleaned.get("other_transfer_service") or "").strip(),
                "destination_amount": destination_amount,
                "destination_currency": destination.currency,
                "destination_rate_to_cad": cleaned.get("destination_rate_to_cad"),
                "destination_rate_to_bdt": cleaned.get("destination_rate_to_bdt"),
                "effective_rate": effective_rate,
                "effective_rate_display": effective_rate_display,
                "provider_exchange_rate": cleaned.get("provider_exchange_rate"),
                "transfer_fee": cleaned.get("transfer_fee") or Decimal("0"),
                "fee_currency": cleaned.get("fee_currency") or source.currency,
                "fee_account_key": "BANK_FEES",
                "purpose_code": purpose_code,
                "purpose_display": purpose_display,
                "source_side": source.side,
                "destination_side": destination.side,
            },
        )
        operation.business_purpose = purpose_display
        return operation


class OwnerLoanOperationForm(FinanceOperationForm):
    party = forms.CharField(max_length=200)
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_account"].queryset = self._accounts()

    def clean(self):
        cleaned = super().clean()
        account = cleaned.get("payment_account")
        if account and account.currency != cleaned.get("currency"):
            self.add_error("currency", "Currency must match the selected account.")
        if account and account.side != cleaned.get("side"):
            self.add_error("side", "Business side must match the selected account.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        incoming = self.workflow["operation_type"] in {
            FinanceOperation.TYPE_OWNER_INVESTMENT,
            FinanceOperation.TYPE_LOAN_RECEIVED,
            FinanceOperation.TYPE_SHAREHOLDER_ADVANCE,
        }
        return self._operation(
            total_amount=cleaned["amount"],
            party_name=cleaned["party"],
            to_account=cleaned["payment_account"] if incoming else None,
            from_account=None if incoming else cleaned["payment_account"],
            reason=cleaned["reason"],
        )


class AssetPurchaseOperationForm(FinanceOperationForm):
    ASSET_CHOICES = (
        ("SEWING_MACHINE", "Sewing machine"), ("CUTTING_MACHINE", "Cutting machine"),
        ("COMPUTER", "Computer"), ("VEHICLE", "Vehicle"), ("OFFICE_FURNITURE", "Office furniture"),
        ("FACTORY_EQUIPMENT", "Factory equipment"), ("OTHER", "Other asset"),
    )
    asset_name = forms.CharField(max_length=200)
    asset_type = forms.ChoiceField(choices=ASSET_CHOICES)
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    useful_life_years = forms.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0.01"))
    serial_number = forms.CharField(max_length=120, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sides = self.allowed_sides
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True, side__in=sides)
        self.fields["payment_account"].queryset = self._accounts()
        self.fields["department"].queryset = _departments_for_sides(sides)

    def clean(self):
        cleaned = super().clean()
        supplier = cleaned.get("supplier")
        account = cleaned.get("payment_account")
        if supplier and supplier.side != cleaned.get("side"):
            self.add_error("supplier", "Supplier and asset business sides must match.")
        if account and (account.side != cleaned.get("side") or account.currency != cleaned.get("currency")):
            self.add_error("payment_account", "Payment account must match the asset side and currency.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        return self._operation(
            total_amount=cleaned["amount"],
            supplier=cleaned["supplier"],
            from_account=cleaned["payment_account"],
            department=cleaned.get("department"),
            details={
                "asset_name": cleaned["asset_name"],
                "asset_type": cleaned["asset_type"],
                "useful_life_years": cleaned["useful_life_years"],
                "serial_number": cleaned.get("serial_number", ""),
            },
        )


class InventoryAdjustmentOperationForm(FinanceOperationForm):
    ADJUSTMENT_CHOICES = (
        ("OPENING", "Opening inventory"), ("PURCHASE", "Purchase adjustment"), ("WASTE", "Waste"),
        ("DAMAGE", "Damage"), ("WRITE_OFF", "Write off"), ("COUNT", "Count correction"),
        ("PRODUCTION_USAGE", "Production usage correction"),
    )
    adjustment_type = forms.ChoiceField(choices=ADJUSTMENT_CHOICES)
    inventory_item = forms.ModelChoiceField(queryset=InventoryItem.objects.none())
    quantity = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    unit_cost = forms.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0.0001"))
    direction = forms.ChoiceField(choices=(("INCREASE", "Increase"), ("DECREASE", "Decrease")))
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none(), required=False)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(
            is_active=True, side__in=self.allowed_sides
        )
        self.fields["inventory_item"].queryset = InventoryItem.objects.filter(is_active=True).order_by("name")

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get("quantity") or Decimal("0")
        adjustment_type = cleaned.get("adjustment_type")
        if adjustment_type in {"OPENING", "PURCHASE"}:
            cleaned["direction"] = "INCREASE"
        if adjustment_type == "PURCHASE" and not cleaned.get("supplier"):
            self.add_error("supplier", "A purchase adjustment requires a supplier.")
        supplier = cleaned.get("supplier")
        if supplier and supplier.side != cleaned.get("side"):
            self.add_error("supplier", "Supplier and inventory adjustment business sides must match.")
        item = cleaned.get("inventory_item")
        if (
            item
            and cleaned.get("direction") == "DECREASE"
            and item.unit_cost is not None
            and cleaned.get("unit_cost") != item.unit_cost
        ):
            self.add_error("unit_cost", "Inventory reductions must use the item's current recorded unit cost.")
        if item and cleaned.get("direction") == "DECREASE" and quantity > item.quantity:
            self.add_error("quantity", "Inventory reduction cannot exceed current stock.")
        return cleaned

    def build_operation(self):
        cleaned = self.cleaned_data
        total = money(abs(cleaned["quantity"]) * cleaned["unit_cost"])
        return self._operation(
            total_amount=total,
            supplier=cleaned.get("supplier"),
            reason=cleaned["reason"],
            details={
                "adjustment_type": cleaned["adjustment_type"],
                "inventory_item_id": cleaned["inventory_item"].pk,
                "inventory_item": cleaned["inventory_item"].name,
                "quantity": cleaned["quantity"],
                "unit_cost": cleaned["unit_cost"],
                "direction": cleaned["direction"],
            },
        )


FORM_BY_TYPE = {
    FinanceOperation.TYPE_CUSTOMER_PAYMENT: CustomerPaymentOperationForm,
    FinanceOperation.TYPE_CUSTOMER_REFUND: CustomerAdjustmentOperationForm,
    FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE: CustomerAdjustmentOperationForm,
    FinanceOperation.TYPE_CUSTOMER_CREDIT: CustomerAdjustmentOperationForm,
    FinanceOperation.TYPE_SUPPLIER_BILL: SupplierBillOperationForm,
    FinanceOperation.TYPE_SUPPLIER_PAYMENT: SupplierPaymentOperationForm,
    FinanceOperation.TYPE_COMPANY_EXPENSE: ExpenseOperationForm,
    FinanceOperation.TYPE_UTILITY_BILL: UtilityOperationForm,
    FinanceOperation.TYPE_PAYROLL: PayrollOperationForm,
    FinanceOperation.TYPE_PRODUCTION_COST: ProductionCostOperationForm,
    FinanceOperation.TYPE_FACTORY_DAILY_COST: FactoryDailyCostOperationForm,
    FinanceOperation.TYPE_BANK_DEPOSIT: BankCashOperationForm,
    FinanceOperation.TYPE_BANK_WITHDRAWAL: BankCashOperationForm,
    FinanceOperation.TYPE_CASH_DEPOSIT: BankCashOperationForm,
    FinanceOperation.TYPE_CASH_WITHDRAWAL: BankCashOperationForm,
    FinanceOperation.TYPE_ACCOUNT_TRANSFER: MoneyTransferOperationForm,
    FinanceOperation.TYPE_BANK_FEE: BankCashOperationForm,
    FinanceOperation.TYPE_PROCESSOR_FEE: BankCashOperationForm,
    FinanceOperation.TYPE_OWNER_INVESTMENT: OwnerLoanOperationForm,
    FinanceOperation.TYPE_OWNER_WITHDRAWAL: OwnerLoanOperationForm,
    FinanceOperation.TYPE_LOAN_RECEIVED: OwnerLoanOperationForm,
    FinanceOperation.TYPE_LOAN_PRINCIPAL: OwnerLoanOperationForm,
    FinanceOperation.TYPE_LOAN_INTEREST: OwnerLoanOperationForm,
    FinanceOperation.TYPE_SHAREHOLDER_ADVANCE: OwnerLoanOperationForm,
    FinanceOperation.TYPE_SHAREHOLDER_REPAYMENT: OwnerLoanOperationForm,
    FinanceOperation.TYPE_ASSET_PURCHASE: AssetPurchaseOperationForm,
    FinanceOperation.TYPE_INVENTORY_ADJUSTMENT: InventoryAdjustmentOperationForm,
}
