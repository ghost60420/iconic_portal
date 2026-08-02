from decimal import Decimal

from django import forms
from django.db.models import Q

from crm.models import (
    BankReconciliation,
    BankStatementLine,
    CashBankAccount,
    Customer,
    ExpenseCategory,
    ExpenseRecord,
    FactoryRunningCostDefault,
    FinancialAccount,
    FinancialAdjustmentRequest,
    FinancialDocument,
    FinancialBudget,
    HistoricalExchangeRate,
    Invoice,
    JournalEntry,
    PayrollBatch,
    PayrollLine,
    ProductionCostRecord,
    RecurringExpenseTemplate,
    Supplier,
    SupplierBill,
)
from crm.services.financial_permissions import accessible_financial_sides


class CoreModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        sides = accessible_financial_sides(self.user) if self.user else set()
        if sides and "side" in self.fields:
            self.fields["side"].choices = [
                choice for choice in self.fields["side"].choices if not choice[0] or choice[0] in sides
            ]
        for name in ("payment_account", "account"):
            if sides and name in self.fields and hasattr(self.fields[name], "queryset"):
                self.fields[name].queryset = self.fields[name].queryset.filter(side__in=sides)
        for name in ("supplier", "vendor"):
            if sides and name in self.fields and hasattr(self.fields[name], "queryset"):
                self.fields[name].queryset = self.fields[name].queryset.filter(side__in=sides)
        if sides and "supplier_bill" in self.fields:
            self.fields["supplier_bill"].queryset = self.fields["supplier_bill"].queryset.filter(side__in=sides)
        if sides and "expense" in self.fields:
            self.fields["expense"].queryset = self.fields["expense"].queryset.filter(side__in=sides)
        if sides and "production_order" in self.fields:
            side_query = Q(pk__in=[])
            if "BD" in sides:
                side_query |= Q(factory_location__iexact="bd")
            if "CA" in sides:
                side_query |= ~Q(factory_location__iexact="bd")
            self.fields["production_order"].queryset = self.fields["production_order"].queryset.filter(side_query)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")
            if isinstance(field.widget, forms.DateInput):
                field.widget.input_type = "date"


class SupplierForm(CoreModelForm):
    class Meta:
        model = Supplier
        fields = ("code", "name", "side", "default_currency", "contact_name", "email", "phone", "tax_identifier")


class SupplierBillForm(CoreModelForm):
    supporting_document = forms.FileField(required=False)

    class Meta:
        model = SupplierBill
        fields = (
            "supplier", "bill_number", "bill_date", "due_date", "currency", "amount_before_tax", "tax_amount",
            "total_amount", "rate_to_cad", "rate_to_bdt", "expense_account", "production_order", "department", "side",
            "description", "change_reason",
        )
        widgets = {"bill_date": forms.DateInput(), "due_date": forms.DateInput(), "description": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        cleaned = super().clean()
        before_tax = cleaned.get("amount_before_tax") or Decimal("0")
        tax = cleaned.get("tax_amount") or Decimal("0")
        total = cleaned.get("total_amount") or Decimal("0")
        if total != before_tax + tax:
            self.add_error("total_amount", "Total must equal amount before tax plus tax.")
        return cleaned


class ExpenseRecordForm(CoreModelForm):
    supporting_document = forms.FileField(required=False)

    class Meta:
        model = ExpenseRecord
        fields = (
            "expense_number", "expense_date", "vendor", "vendor_name", "category", "department", "side", "currency",
            "amount_before_tax", "tax_amount", "total_amount", "rate_to_cad", "rate_to_bdt", "payment_method",
            "payment_account", "payment_status", "due_date", "description", "business_purpose", "production_order",
            "change_reason",
        )
        widgets = {
            "expense_date": forms.DateInput(), "due_date": forms.DateInput(), "description": forms.Textarea(attrs={"rows": 2}),
            "business_purpose": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        cleaned = super().clean()
        before_tax = cleaned.get("amount_before_tax") or Decimal("0")
        tax = cleaned.get("tax_amount") or Decimal("0")
        if cleaned.get("total_amount") != before_tax + tax:
            self.add_error("total_amount", "Total must equal amount before tax plus tax.")
        return cleaned


class ExpenseCategoryForm(CoreModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ("code", "name", "subcategory", "default_account", "is_production_cost", "is_active")


class RecurringExpenseTemplateForm(CoreModelForm):
    class Meta:
        model = RecurringExpenseTemplate
        fields = (
            "name", "frequency", "start_date", "end_date", "expected_amount", "currency", "vendor", "vendor_name",
            "category", "department", "side", "reminder_days_before", "due_day", "description", "is_active",
        )
        widgets = {"start_date": forms.DateInput(), "end_date": forms.DateInput(), "description": forms.Textarea(attrs={"rows": 2})}


class HistoricalExchangeRateForm(CoreModelForm):
    class Meta:
        model = HistoricalExchangeRate
        fields = ("rate_date", "source_currency", "target_currency", "rate", "source_name", "evidence_reference")
        widgets = {"rate_date": forms.DateInput()}


class CashBankAccountForm(CoreModelForm):
    class Meta:
        model = CashBankAccount
        fields = ("name", "kind", "side", "currency", "gl_account", "institution_name", "masked_reference", "is_active")


class FinancialBudgetForm(CoreModelForm):
    class Meta:
        model = FinancialBudget
        fields = ("year", "month", "side", "department", "account", "currency", "amount", "change_reason")


class BankReconciliationForm(CoreModelForm):
    class Meta:
        model = BankReconciliation
        fields = (
            "account", "statement_start_date", "statement_end_date", "statement_opening_balance", "statement_closing_balance",
            "change_reason",
        )
        widgets = {"statement_start_date": forms.DateInput(), "statement_end_date": forms.DateInput()}


class BankStatementLineForm(CoreModelForm):
    class Meta:
        model = BankStatementLine
        fields = ("transaction_date", "reference", "description", "amount", "is_imported")
        widgets = {"transaction_date": forms.DateInput()}


class ProductionCostRecordForm(CoreModelForm):
    class Meta:
        model = ProductionCostRecord
        fields = (
            "production_order", "customer", "opportunity", "category", "estimated_amount", "actual_amount", "currency",
            "supplier", "supplier_bill", "expense", "payment_status", "cost_date", "description", "change_reason",
        )
        widgets = {"cost_date": forms.DateInput(), "description": forms.Textarea(attrs={"rows": 2})}


class PayrollBatchForm(CoreModelForm):
    class Meta:
        model = PayrollBatch
        fields = ("reference", "period_start", "period_end", "side", "currency", "department", "change_reason")
        widgets = {"period_start": forms.DateInput(), "period_end": forms.DateInput()}


class PayrollLineForm(CoreModelForm):
    class Meta:
        model = PayrollLine
        fields = (
            "employee", "employee_reference", "base_salary", "overtime", "bonus", "commission", "deductions",
            "employer_cost", "net_pay", "department", "change_reason",
        )


class PayrollPaymentForm(forms.Form):
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())
    rate_to_cad = forms.DecimalField(max_digits=20, decimal_places=10, required=False)
    rate_to_bdt = forms.DecimalField(max_digits=20, decimal_places=10, required=False)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["payment_account"].queryset = CashBankAccount.objects.filter(
            is_active=True, side__in=accessible_financial_sides(user)
        ).order_by("side", "name")
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                "class", "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class CustomerReceiptForm(forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.objects.none())
    invoices = forms.ModelMultipleChoiceField(queryset=Invoice.objects.none(), required=False)
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    currency = forms.ChoiceField(choices=(("CAD", "CAD"), ("USD", "USD"), ("BDT", "BDT")))
    receipt_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())
    payment_method = forms.ChoiceField(choices=(("bank", "Bank"), ("cash", "Cash"), ("card", "Card"), ("mobile", "Mobile"), ("other", "Other")))
    reference = forms.CharField(max_length=100)
    rate_to_cad = forms.DecimalField(max_digits=20, decimal_places=10, required=False)
    rate_to_bdt = forms.DecimalField(max_digits=20, decimal_places=10, required=False)
    evidence_reference = forms.CharField(max_length=255)
    supporting_document = forms.FileField(required=False)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        from crm.services.financial_permissions import scope_invoices_for_user

        invoices = scope_invoices_for_user(
            Invoice.objects.filter(financial_state__document_status="ISSUED"), user
        ).order_by("due_date", "id")
        self.fields["invoices"].queryset = invoices
        self.fields["customer"].queryset = Customer.objects.filter(invoice__in=invoices).distinct().order_by(
            "account_brand", "contact_name"
        )
        self.fields["payment_account"].queryset = CashBankAccount.objects.filter(
            is_active=True, side__in=accessible_financial_sides(user)
        ).order_by("side", "name")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) else "form-control")


class SupplierPaymentForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    bills = forms.ModelMultipleChoiceField(queryset=SupplierBill.objects.none(), required=False)
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    currency = forms.ChoiceField(choices=(("CAD", "CAD"), ("USD", "USD"), ("BDT", "BDT")))
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    payment_account = forms.ModelChoiceField(queryset=CashBankAccount.objects.none())
    payment_method = forms.CharField(max_length=30, initial="bank")
    reference = forms.CharField(max_length=100)
    rate_to_cad = forms.DecimalField(max_digits=20, decimal_places=10, required=False)
    rate_to_bdt = forms.DecimalField(max_digits=20, decimal_places=10, required=False)
    evidence_reference = forms.CharField(max_length=255)
    supporting_document = forms.FileField(required=False)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        sides = accessible_financial_sides(user)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True, side__in=sides).order_by("name")
        self.fields["bills"].queryset = SupplierBill.objects.filter(
            approval_status=SupplierBill.APPROVAL_APPROVED, side__in=sides
        ).exclude(payment_status=SupplierBill.PAYMENT_PAID).order_by("due_date", "id")
        self.fields["payment_account"].queryset = CashBankAccount.objects.filter(
            is_active=True, side__in=sides
        ).order_by("side", "name")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) else "form-control")


class FactoryTimelineEstimateForm(forms.Form):
    daily_default = forms.ModelChoiceField(queryset=FactoryRunningCostDefault.objects.none())
    estimated_days = forms.IntegerField(min_value=1)
    estimated_revenue = forms.DecimalField(max_digits=18, decimal_places=2)
    other_estimated_cost = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    target_margin_percent = forms.DecimalField(max_digits=8, decimal_places=4, required=False)
    approved_minimum_margin_percent = forms.DecimalField(max_digits=8, decimal_places=4, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["daily_default"].queryset = FactoryRunningCostDefault.objects.filter(is_active=True).order_by(
            "side", "-effective_from"
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")


class FactoryTimelineActualForm(forms.Form):
    actual_days = forms.IntegerField(min_value=1)
    actual_revenue = forms.DecimalField(max_digits=18, decimal_places=2)
    other_actual_cost = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class FinancialExceptionDecisionForm(forms.Form):
    ACTION_CHOICES = (
        ("EVIDENCE_REQUIRED", "Mark evidence required"),
        ("REJECTED", "Reject proposed correction"),
        ("INCOMPLETE", "Mark historical data incomplete"),
        ("DEFERRED", "Defer for later review"),
    )

    action = forms.ChoiceField(choices=ACTION_CHOICES)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), min_length=5)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].widget.attrs["class"] = "form-select"
        self.fields["notes"].widget.attrs["class"] = "form-control"


class FinancialEvidenceUploadForm(forms.Form):
    evidence = forms.FileField()
    description = forms.CharField(max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class FinancialAdjustmentProposalForm(CoreModelForm):
    evidence_documents = forms.ModelMultipleChoiceField(
        queryset=FinancialDocument.objects.none(),
        required=True,
        help_text="Select at least one reviewed evidence document.",
    )

    class Meta:
        model = FinancialAdjustmentRequest
        fields = (
            "adjustment_type", "journal_date", "side", "currency", "native_amount", "rate_to_cad",
            "rate_to_bdt", "debit_account", "credit_account", "source_journal", "reason", "before_values",
            "after_values", "evidence_documents",
        )
        widgets = {
            "journal_date": forms.DateInput(),
            "reason": forms.Textarea(attrs={"rows": 3}),
            "before_values": forms.Textarea(attrs={"rows": 3}),
            "after_values": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, exception=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.exception = exception
        self.fields["debit_account"].queryset = FinancialAccount.objects.filter(
            is_active=True, system_key__isnull=False
        ).order_by("code")
        self.fields["credit_account"].queryset = FinancialAccount.objects.filter(
            is_active=True, system_key__isnull=False
        ).order_by("code")
        sides = accessible_financial_sides(user) if user else set()
        self.fields["source_journal"].queryset = JournalEntry.objects.filter(
            state=JournalEntry.STATE_POSTED, side__in=sides
        ).order_by("-journal_date", "-id")
        self.fields["evidence_documents"].queryset = (
            exception.documents.order_by("created_at") if exception else FinancialDocument.objects.none()
        )

    def clean(self):
        cleaned = super().clean()
        adjustment_type = cleaned.get("adjustment_type")
        source_journal = cleaned.get("source_journal")
        if adjustment_type == FinancialAdjustmentRequest.TYPE_REVERSAL and not source_journal:
            self.add_error("source_journal", "Select the posted journal to reverse.")
        if adjustment_type != FinancialAdjustmentRequest.TYPE_REVERSAL and source_journal:
            self.add_error("source_journal", "An original journal is only valid for a reversal.")
        for field_name in ("rate_to_cad", "rate_to_bdt"):
            if (cleaned.get(field_name) or Decimal("0")) <= 0:
                self.add_error(field_name, "An evidenced positive exchange-rate snapshot is required.")
        if cleaned.get("debit_account") == cleaned.get("credit_account"):
            self.add_error("credit_account", "Debit and credit accounts must be different.")
        return cleaned
