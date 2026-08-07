from datetime import date, timedelta
from decimal import Decimal
from functools import wraps
import json
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST

from crm.forms_financial_core import (
    BankReconciliationForm,
    BankStatementLineForm,
    CashBankAccountForm,
    CustomerReceiptForm,
    ExpenseCategoryForm,
    ExpenseRecordForm,
    FactoryTimelineActualForm,
    FactoryTimelineEstimateForm,
    FinancialAdjustmentProposalForm,
    FinancialEvidenceUploadForm,
    FinancialExceptionDecisionForm,
    FinancialBudgetForm,
    HistoricalExchangeRateForm,
    PayrollBatchForm,
    PayrollLineForm,
    PayrollPaymentForm,
    ProductionCostRecordForm,
    RecurringExpenseTemplateForm,
    SupplierBillForm,
    SupplierForm,
    SupplierPaymentForm,
)
from crm.models import (
    BankReconciliation,
    CashBankAccount,
    CurrencyReviewItem,
    ExpenseCategory,
    ExpenseRecord,
    FinancialAccount,
    FinancialDocument,
    FinancialBudget,
    FinancialAdjustmentRequest,
    FinancialAuditEvent,
    FinancialExceptionReview,
    FinancialPeriod,
    HistoricalExchangeRate,
    JournalLine,
    PayrollBatch,
    ProductionCostRecord,
    RecurringExpenseTemplate,
    Supplier,
    SupplierBill,
    QuickCosting,
)
from crm.services.bank_reconciliation import (
    BankReconciliationError,
    approve_bank_reconciliation,
    match_statement_transaction,
    submit_bank_reconciliation,
)
from crm.services.expense_management import (
    ExpenseManagementError,
    approve_expense,
    approve_payroll_batch,
    generate_recurring_expense_drafts,
    pay_payroll_batch,
    submit_expense,
)
from crm.services.factory_timeline import (
    configured_factory_default,
    current_estimated_inputs,
    FactoryTimelineError,
    record_actual_factory_timeline,
    save_estimated_factory_timeline,
)
from crm.services.financial_permissions import (
    accessible_financial_sides,
    can_access_financial_object,
    can_approve_financial_transactions,
    can_approve_financial_adjustment,
    can_create_financial_adjustment,
    can_enter_production_cost,
    can_manage_financial_transactions,
    can_manage_payroll,
    can_pay_payroll,
    can_review_financial_exceptions,
    can_view_payroll_detail,
    requested_financial_side,
    require_financial_permission,
    scope_bank_accounts_for_user,
    scope_by_financial_side,
    scope_expenses_for_user,
    scope_payroll_for_user,
    scope_production_costs_for_user,
    scope_supplier_bills_for_user,
)
from crm.services.operations_permissions import (
    ROLE_ADMIN,
    ROLE_CEO,
    ROLE_DIRECTOR,
    ROLE_FINANCE,
    ROLE_MANAGER,
    operations_role_names,
)
from crm.permissions import can_view_internal_costing
from crm.services.financial_adjustments import (
    FinancialAdjustmentError,
    decide_adjustment_request,
    submit_adjustment_request,
)
from crm.services.financial_exception_review import EVIDENCE_BY_AREA
from crm.services.financial_reporting import (
    ReportFilters,
    accounts_payable_aging,
    accounts_receivable_aging,
    balance_sheet,
    budget_vs_actual,
    cash_flow,
    executive_financial_summary,
    general_ledger,
    profit_and_loss_comparison,
    trial_balance,
)
from crm.services.payables_ledger import PayablesLedgerError, approve_supplier_bill, record_supplier_payment
from crm.services.production_costs import ProductionCostError, approve_production_cost
from crm.services.receivable_accounting import ReceivableAccountingError, record_customer_receipt


def _attach_document(record, uploaded_file, *, actor, document_type, description=""):
    if not uploaded_file:
        return None
    return FinancialDocument.objects.create(
        source_record=record,
        file=uploaded_file,
        document_type=document_type,
        description=description,
        created_by=actor,
        modified_by=actor,
    )


def require_core_writes(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.method not in ("GET", "HEAD") and not getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False):
            messages.error(request, "Finance posting is disabled.")
            return redirect("financial_core_dashboard")
        return view_func(request, *args, **kwargs)

    return wrapped


def _dates(request):
    today = timezone.localdate()
    start = parse_date(request.GET.get("start", "")) or today.replace(month=1, day=1)
    end = parse_date(request.GET.get("end", "")) or today
    if start > end:
        start, end = end, start
    return start, end


def _as_of(request):
    return parse_date(request.GET.get("as_of", "")) or timezone.localdate()


def _side(request):
    return requested_financial_side(request.user, request.GET.get("side"))


def _common(request, **context):
    context.update(
        {
            "financial_core_reporting_active": bool(getattr(settings, "FINANCIAL_CORE_REPORTING_ACTIVE", False)),
            "financial_core_writes_enabled": bool(getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False)),
            "can_manage_core": can_manage_financial_transactions(request.user),
            "can_approve_core": can_approve_financial_transactions(request.user),
            "can_view_payroll_detail": can_view_payroll_detail(request.user),
            "can_manage_payroll": can_manage_payroll(request.user),
            "can_pay_payroll": can_pay_payroll(request.user),
            "can_review_exceptions": can_review_financial_exceptions(request.user),
        }
    )
    return context


@require_financial_permission("view")
def financial_core_dashboard(request):
    start, end = _dates(request)
    summary = executive_financial_summary(start_date=start, end_date=end, as_of_date=end, side=_side(request))
    return render(
        request,
        "crm/financial_core/dashboard.html",
        _common(request, summary=summary, start_date=start, end_date=end, side=_side(request)),
    )


@require_financial_permission("view")
def financial_chart(request):
    accounts = FinancialAccount.objects.select_related("parent").order_by("code")
    return render(request, "crm/financial_core/chart.html", _common(request, accounts=accounts))


@require_financial_permission("view")
def financial_general_ledger(request):
    start, end = _dates(request)
    filters = ReportFilters(start_date=start, end_date=end, side=_side(request))
    account_id = request.GET.get("account") or None
    rows = general_ledger(filters, account_id=account_id)[:500]
    return render(
        request,
        "crm/financial_core/report.html",
        _common(
            request,
            report_type="general_ledger",
            report_title="General Ledger",
            rows=rows,
            accounts=FinancialAccount.objects.filter(is_active=True).order_by("code"),
            selected_account=str(account_id or ""),
            start_date=start,
            end_date=end,
            side=_side(request),
        ),
    )


@require_financial_permission("view")
def financial_trial_balance(request):
    start, end = _dates(request)
    report = trial_balance(start_date=start, end_date=end, side=_side(request))
    return render(
        request,
        "crm/financial_core/report.html",
        _common(request, report_type="trial_balance", report_title="Trial Balance", report=report, start_date=start, end_date=end, side=_side(request)),
    )


@require_financial_permission("sensitive")
def financial_profit_loss(request):
    start, end = _dates(request)
    report = profit_and_loss_comparison(start_date=start, end_date=end, side=_side(request))
    return render(
        request,
        "crm/financial_core/report.html",
        _common(request, report_type="profit_loss", report_title="Profit and Loss", report=report, start_date=start, end_date=end, side=_side(request)),
    )


@require_financial_permission("sensitive")
def financial_balance_sheet(request):
    as_of = _as_of(request)
    report = balance_sheet(as_of_date=as_of, side=_side(request))
    return render(
        request,
        "crm/financial_core/report.html",
        _common(request, report_type="balance_sheet", report_title="Balance Sheet", report=report, as_of_date=as_of, side=_side(request)),
    )


@require_financial_permission("sensitive")
def financial_cash_flow(request):
    start, end = _dates(request)
    report = cash_flow(start_date=start, end_date=end, side=_side(request))
    return render(
        request,
        "crm/financial_core/report.html",
        _common(request, report_type="cash_flow", report_title="Cash Flow Statement", report=report, start_date=start, end_date=end, side=_side(request)),
    )


@require_financial_permission("view")
def financial_ar_aging(request):
    as_of = _as_of(request)
    report = accounts_receivable_aging(as_of_date=as_of, side=_side(request))
    return render(
        request,
        "crm/financial_core/report.html",
        _common(request, report_type="ar_aging", report_title="Accounts Receivable Aging", report=report, as_of_date=as_of, side=_side(request)),
    )


@require_financial_permission("view")
def financial_ap_aging(request):
    as_of = _as_of(request)
    report = accounts_payable_aging(as_of_date=as_of, side=_side(request))
    return render(
        request,
        "crm/financial_core/report.html",
        _common(request, report_type="ap_aging", report_title="Accounts Payable Aging", report=report, as_of_date=as_of, side=_side(request)),
    )


@require_financial_permission("view")
def financial_budget_actual(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
    except ValueError:
        return HttpResponseBadRequest("Invalid budget period.")
    report = budget_vs_actual(year=year, month=month, side=_side(request))
    return render(
        request,
        "crm/financial_core/report.html",
        _common(
            request,
            report_type="budget_actual",
            report_title="Budget versus Actual",
            report=report,
            budget_form=FinancialBudgetForm() if can_manage_financial_transactions(request.user) else None,
            year=year,
            month=month,
            side=_side(request),
        ),
    )


@require_POST
@require_financial_permission("manage")
@require_core_writes
def financial_budget_add(request):
    form = FinancialBudgetForm(request.POST)
    if form.is_valid():
        values = form.cleaned_data
        FinancialBudget.objects.update_or_create(
            year=values["year"],
            month=values["month"],
            side=values["side"],
            department=values["department"],
            account=values["account"],
            currency=values["currency"],
            create_defaults={
                "amount": values["amount"],
                "created_by": request.user,
                "modified_by": request.user,
                "change_reason": values["change_reason"],
            },
            defaults={
                "amount": values["amount"],
                "modified_by": request.user,
                "change_reason": values["change_reason"],
            },
        )
        messages.success(request, "Budget saved.")
    else:
        messages.error(request, form.errors.as_text())
    return redirect("financial_budget_actual")


@require_http_methods(["GET", "POST"])
@require_financial_permission("expense")
@require_core_writes
def financial_expense_center(request):
    can_manage = can_manage_financial_transactions(request.user)
    if request.method == "POST" and not can_manage:
        raise Http404("Expense record not found.")
    action = request.POST.get("action") if request.method == "POST" else ""
    expense_data = request.POST if request.method == "POST" and action != "category" else None
    expense_files = request.FILES if request.method == "POST" and action != "category" else None
    form = ExpenseRecordForm(expense_data, expense_files, user=request.user) if can_manage else None
    category_form = ExpenseCategoryForm(
        request.POST if action == "category" else None, prefix="category", user=request.user
    ) if can_manage else None
    if request.method == "POST":
        if action == "category" and category_form.is_valid():
            category = category_form.save(commit=False)
            category.created_by = category.modified_by = request.user
            category.save()
            messages.success(request, f"Expense category {category.name} created.")
            return redirect("financial_expense_center")
        if action != "category" and form.is_valid():
            expense = form.save(commit=False)
            expense.created_by = request.user
            expense.modified_by = request.user
            expense.save()
            _attach_document(
                expense,
                form.cleaned_data.get("supporting_document"),
                actor=request.user,
                document_type="EXPENSE_RECEIPT",
                description=expense.expense_number,
            )
            messages.success(request, f"Draft expense {expense.expense_number} created.")
            return redirect("financial_expense_center")
    expenses = scope_expenses_for_user(
        ExpenseRecord.objects.select_related("vendor", "category", "department"), request.user
    ).order_by("-expense_date", "-id")[:300]
    return render(
        request,
        "crm/financial_core/operations.html",
        _common(
            request,
            operation="expenses",
            title="Expense Management",
            form=form,
            category_form=category_form,
            categories=ExpenseCategory.objects.select_related("default_account").order_by("name", "subcategory"),
            records=expenses,
        ),
    )


@require_POST
@require_financial_permission("manage")
@require_core_writes
def financial_expense_submit(request, pk):
    try:
        submit_expense(get_object_or_404(scope_expenses_for_user(ExpenseRecord.objects.all(), request.user), pk=pk), actor=request.user)
        messages.success(request, "Expense submitted.")
    except ExpenseManagementError as exc:
        messages.error(request, str(exc))
    return redirect("financial_expense_center")


@require_POST
@require_financial_permission("approve")
@require_core_writes
def financial_expense_approve(request, pk):
    try:
        approve_expense(get_object_or_404(scope_expenses_for_user(ExpenseRecord.objects.all(), request.user), pk=pk), actor=request.user)
        messages.success(request, "Expense approved and posted.")
    except (ExpenseManagementError, ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("financial_expense_center")


@require_http_methods(["GET", "POST"])
@require_financial_permission("supplier")
@require_core_writes
def financial_supplier_center(request):
    can_manage = can_manage_financial_transactions(request.user)
    if request.method == "POST" and not can_manage:
        raise Http404("Supplier record not found.")
    supplier_form = SupplierForm(prefix="supplier", user=request.user) if can_manage else None
    bill_form = SupplierBillForm(prefix="bill", user=request.user) if can_manage else None
    payment_form = SupplierPaymentForm(prefix="payment", user=request.user) if can_manage else None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "supplier":
            supplier_form = SupplierForm(request.POST, prefix="supplier", user=request.user)
            if supplier_form.is_valid():
                record = supplier_form.save(commit=False)
                record.created_by = record.modified_by = request.user
                record.save()
                messages.success(request, "Supplier created.")
                return redirect("financial_supplier_center")
        elif action == "bill":
            bill_form = SupplierBillForm(request.POST, request.FILES, prefix="bill", user=request.user)
            if bill_form.is_valid():
                record = bill_form.save(commit=False)
                record.remaining_amount = record.total_amount
                record.amount_cad = Decimal("0")
                record.amount_bdt = Decimal("0")
                record.created_by = record.modified_by = request.user
                record.save()
                _attach_document(
                    record,
                    bill_form.cleaned_data.get("supporting_document"),
                    actor=request.user,
                    document_type="SUPPLIER_BILL",
                    description=record.bill_number,
                )
                messages.success(request, "Draft supplier bill created.")
                return redirect("financial_supplier_center")
        elif action == "payment":
            payment_form = SupplierPaymentForm(request.POST, request.FILES, prefix="payment", user=request.user)
            if payment_form.is_valid():
                try:
                    payment_data = payment_form.cleaned_data.copy()
                    supporting_document = payment_data.pop("supporting_document", None)
                    result = record_supplier_payment(actor=request.user, **payment_data)
                    _attach_document(
                        result["event"], supporting_document, actor=request.user,
                        document_type="SUPPLIER_PAYMENT", description=result["event"].reference,
                    )
                    messages.success(request, "Supplier payment posted.")
                    return redirect("financial_supplier_center")
                except (PayablesLedgerError, ValueError) as exc:
                    messages.error(request, str(exc))
    bills = scope_supplier_bills_for_user(
        SupplierBill.objects.select_related("supplier", "expense_account"), request.user
    ).order_by("-bill_date", "-id")[:300]
    return render(
        request,
        "crm/financial_core/operations.html",
        _common(
            request,
            operation="suppliers",
            title="Supplier Bills and Payments",
            supplier_form=supplier_form,
            bill_form=bill_form,
            payment_form=payment_form,
            records=bills,
            suppliers=Supplier.objects.filter(
                is_active=True, side__in=accessible_financial_sides(request.user)
            ).order_by("name"),
        ),
    )


@require_POST
@require_financial_permission("approve")
@require_core_writes
def financial_supplier_bill_approve(request, pk):
    try:
        approve_supplier_bill(
            get_object_or_404(scope_supplier_bills_for_user(SupplierBill.objects.all(), request.user), pk=pk),
            actor=request.user,
        )
        messages.success(request, "Supplier bill approved and posted.")
    except (PayablesLedgerError, ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("financial_supplier_center")


@require_http_methods(["GET", "POST"])
@require_financial_permission("manage")
@require_core_writes
def financial_customer_receipts(request):
    form = CustomerReceiptForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            receipt_data = form.cleaned_data.copy()
            supporting_document = receipt_data.pop("supporting_document", None)
            result = record_customer_receipt(actor=request.user, **receipt_data)
            _attach_document(
                result["event"], supporting_document, actor=request.user,
                document_type="CUSTOMER_RECEIPT", description=result["event"].reference,
            )
            messages.success(
                request,
                f"Receipt posted. Allocated {result['allocated']}; customer credit {result['customer_credit']}.",
            )
            return redirect("financial_customer_receipts")
        except (ReceivableAccountingError, ValueError, ValidationError) as exc:
            messages.error(request, str(exc))
    return render(request, "crm/financial_core/operations.html", _common(request, operation="customer_receipts", title="Customer Receipts", form=form, records=[]))


@require_http_methods(["GET", "POST"])
@require_financial_permission("manage")
@require_core_writes
def financial_recurring_expenses(request):
    form = RecurringExpenseTemplateForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        template = form.save(commit=False)
        template.created_by = template.modified_by = request.user
        template.save()
        messages.success(request, "Recurring expense template saved.")
        return redirect("financial_recurring_expenses")
    records = RecurringExpenseTemplate.objects.select_related("vendor", "category", "department").filter(
        side__in=accessible_financial_sides(request.user)
    ).order_by("name")
    return render(request, "crm/financial_core/operations.html", _common(request, operation="recurring", title="Recurring Bills", form=form, records=records))


@require_POST
@require_financial_permission("manage")
@require_core_writes
def financial_recurring_generate(request):
    through = parse_date(request.POST.get("through", ""))
    if not through:
        messages.error(request, "Enter a valid through date.")
    else:
        created = generate_recurring_expense_drafts(through_date=through, actor=request.user)
        messages.success(request, f"Created {len(created)} draft expense(s). None were posted or paid.")
    return redirect("financial_recurring_expenses")


@require_http_methods(["GET", "POST"])
@require_financial_permission("bank")
@require_core_writes
def financial_currency_center(request):
    rate_form = HistoricalExchangeRateForm(prefix="rate", user=request.user)
    account_form = CashBankAccountForm(prefix="account", user=request.user)
    if request.method == "POST":
        if request.POST.get("action") == "rate":
            rate_form = HistoricalExchangeRateForm(request.POST, prefix="rate", user=request.user)
            if rate_form.is_valid():
                rate = rate_form.save(commit=False)
                rate.is_approved = can_approve_financial_transactions(request.user)
                rate.created_by = rate.modified_by = request.user
                if rate.is_approved:
                    rate.approved_by = request.user
                    rate.approved_at = timezone.now()
                rate.save()
                messages.success(request, "Historical rate saved.")
                return redirect("financial_currency_center")
        elif request.POST.get("action") == "account":
            account_form = CashBankAccountForm(request.POST, prefix="account", user=request.user)
            if account_form.is_valid():
                account = account_form.save(commit=False)
                account.created_by = account.modified_by = request.user
                account.save()
                messages.success(request, "Cash/bank account saved.")
                return redirect("financial_currency_center")
    return render(
        request,
        "crm/financial_core/operations.html",
        _common(
            request,
            operation="currency",
            title="Currency and Cash Accounts",
            rate_form=rate_form,
            account_form=account_form,
            rates=HistoricalExchangeRate.objects.order_by("-rate_date")[:200],
            records=(CurrencyReviewItem.objects.select_related("content_type", "resolved_rate").order_by("state", "transaction_date")[:300]
                     if accessible_financial_sides(request.user) == {"CA", "BD"} else CurrencyReviewItem.objects.none()),
        ),
    )


@require_http_methods(["GET", "POST"])
@require_financial_permission("bank")
@require_core_writes
def financial_bank_reconciliations(request):
    form = BankReconciliationForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.created_by = record.modified_by = request.user
        record.save()
        messages.success(request, "Draft reconciliation created.")
        return redirect("financial_bank_reconciliation_detail", pk=record.pk)
    records = BankReconciliation.objects.select_related("account", "approved_by").filter(
        account__side__in=accessible_financial_sides(request.user)
    ).order_by("-statement_end_date")
    return render(request, "crm/financial_core/operations.html", _common(request, operation="reconciliations", title="Bank Reconciliation", form=form, records=records))


@require_http_methods(["GET", "POST"])
@require_financial_permission("bank")
@require_core_writes
def financial_bank_reconciliation_detail(request, pk):
    reconciliation = get_object_or_404(
        BankReconciliation.objects.select_related("account").filter(
            account__side__in=accessible_financial_sides(request.user)
        ), pk=pk
    )
    line_form = BankStatementLineForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "line" and line_form.is_valid():
        line = line_form.save(commit=False)
        line.reconciliation = reconciliation
        line.created_by = line.modified_by = request.user
        line.save()
        messages.success(request, "Statement transaction added.")
        return redirect("financial_bank_reconciliation_detail", pk=pk)
    book_lines = JournalLine.objects.filter(account=reconciliation.account.gl_account).select_related("journal").order_by("-journal__journal_date")[:300]
    return render(
        request,
        "crm/financial_core/reconciliation_detail.html",
        _common(request, reconciliation=reconciliation, line_form=line_form, book_lines=book_lines),
    )


@require_POST
@require_financial_permission("bank")
@require_core_writes
def financial_bank_reconciliation_action(request, pk):
    reconciliation = get_object_or_404(
        BankReconciliation.objects.filter(account__side__in=accessible_financial_sides(request.user)), pk=pk
    )
    action = request.POST.get("action")
    try:
        if action == "submit":
            submit_bank_reconciliation(reconciliation, actor=request.user)
            messages.success(request, "Reconciliation submitted.")
        elif action == "approve":
            if not can_approve_financial_transactions(request.user):
                raise BankReconciliationError("Approval permission is required.")
            approve_bank_reconciliation(reconciliation, actor=request.user)
            messages.success(request, "Reconciliation approved.")
        elif action == "match":
            match_statement_transaction(
                statement_line=get_object_or_404(reconciliation.statement_lines, pk=request.POST.get("statement_line")),
                journal_line=get_object_or_404(JournalLine, pk=request.POST.get("journal_line")),
                matched_amount=request.POST.get("matched_amount"),
                actor=request.user,
            )
            messages.success(request, "Transaction matched.")
        else:
            messages.error(request, "Unknown reconciliation action.")
    except (BankReconciliationError, ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("financial_bank_reconciliation_detail", pk=pk)


@require_http_methods(["GET", "POST"])
@require_financial_permission("production_cost")
@require_core_writes
def financial_production_costs(request):
    can_enter = can_enter_production_cost(request.user)
    if request.method == "POST" and not can_enter:
        raise Http404("Production cost record not found.")
    form = ProductionCostRecordForm(request.POST or None, user=request.user) if can_enter else None
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.created_by = record.modified_by = request.user
        record.save()
        messages.success(request, "Production cost record saved as draft.")
        return redirect("financial_production_costs")
    records = scope_production_costs_for_user(
        ProductionCostRecord.objects.select_related("production_order", "supplier", "supplier_bill", "expense"), request.user
    ).order_by("-cost_date")[:300]
    return render(request, "crm/financial_core/operations.html", _common(request, operation="production_costs", title="Production Costs", form=form, records=records))


@require_POST
@require_financial_permission("approve")
@require_core_writes
def financial_production_cost_approve(request, pk):
    try:
        approve_production_cost(
            get_object_or_404(scope_production_costs_for_user(ProductionCostRecord.objects.all(), request.user), pk=pk),
            actor=request.user,
        )
        messages.success(request, "Production cost approved.")
    except ProductionCostError as exc:
        messages.error(request, str(exc))
    return redirect("financial_production_costs")


@require_http_methods(["GET", "POST"])
@require_financial_permission("payroll_detail")
@require_core_writes
def financial_payroll(request):
    can_manage = can_manage_payroll(request.user)
    if request.method == "POST" and not can_manage:
        raise Http404("Payroll record not found.")
    form = PayrollBatchForm(request.POST or None, user=request.user) if can_manage else None
    if request.method == "POST" and form.is_valid():
        batch = form.save(commit=False)
        batch.created_by = batch.modified_by = request.user
        batch.save()
        messages.success(request, "Payroll batch created.")
        return redirect("financial_payroll")
    records = scope_payroll_for_user(PayrollBatch.objects.select_related("department", "approved_by").prefetch_related(
        "private_lines__employee", "private_lines__department"
    ), request.user).order_by("-period_end")[:100]
    return render(
        request,
        "crm/financial_core/operations.html",
        _common(
            request,
            operation="payroll",
            title="Payroll",
            form=form,
            line_form=PayrollLineForm(user=request.user) if can_manage else None,
            payment_form=PayrollPaymentForm(user=request.user) if can_pay_payroll(request.user) else None,
            records=records,
        ),
    )


@require_POST
@require_financial_permission("payroll_manage")
@require_core_writes
def financial_payroll_add_line(request, pk):
    batch = get_object_or_404(scope_payroll_for_user(PayrollBatch.objects.all(), request.user), pk=pk)
    form = PayrollLineForm(request.POST, user=request.user)
    if form.is_valid():
        line = form.save(commit=False)
        line.batch = batch
        line.created_by = line.modified_by = request.user
        line.save()
        messages.success(request, "Private payroll line saved.")
    else:
        messages.error(request, form.errors.as_text())
    return redirect("financial_payroll")


@require_POST
@require_financial_permission("approve")
@require_core_writes
def financial_payroll_approve(request, pk):
    try:
        approve_payroll_batch(
            get_object_or_404(scope_payroll_for_user(PayrollBatch.objects.all(), request.user), pk=pk), actor=request.user
        )
        messages.success(request, "Payroll approved and accrued.")
    except ExpenseManagementError as exc:
        messages.error(request, str(exc))
    return redirect("financial_payroll")


@require_POST
@require_financial_permission("payroll_pay")
@require_core_writes
def financial_payroll_pay(request, pk):
    form = PayrollPaymentForm(request.POST, user=request.user)
    if form.is_valid():
        try:
            pay_payroll_batch(
                get_object_or_404(scope_payroll_for_user(PayrollBatch.objects.all(), request.user), pk=pk), actor=request.user, **form.cleaned_data
            )
            messages.success(request, "Payroll payment posted.")
        except (ExpenseManagementError, ValueError, ValidationError) as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return redirect("financial_payroll")


def _exception_queryset(request):
    rows = scope_by_financial_side(FinancialExceptionReview.objects.select_related(
        "customer", "supplier", "reviewed_by"
    ).annotate(document_count=Count("documents")), request.user)
    filters = {
        "severity": "severity",
        "area": "area",
        "exception_type": "exception_type",
        "status": "review_status",
        "side": "side",
        "currency": "currency",
    }
    for parameter, field_name in filters.items():
        value = (request.GET.get(parameter) or "").strip()
        if value:
            rows = rows.filter(**{field_name: value})
    counterparty = (request.GET.get("counterparty") or "").strip()
    if counterparty:
        rows = rows.filter(counterparty_name__icontains=counterparty)
    date_from = parse_date(request.GET.get("date_from", ""))
    date_to = parse_date(request.GET.get("date_to", ""))
    if date_from:
        rows = rows.filter(transaction_date__gte=date_from)
    if date_to:
        rows = rows.filter(transaction_date__lte=date_to)
    evidence = (request.GET.get("evidence") or "").strip()
    if evidence == "AVAILABLE":
        rows = rows.filter(document_count__gt=0)
    elif evidence == "MISSING":
        rows = rows.filter(document_count=0)
    return rows.order_by("severity", "area", "exception_id")


@require_financial_permission("exception_review")
def financial_exception_center(request):
    page = Paginator(_exception_queryset(request), 50).get_page(request.GET.get("page"))
    all_allowed = scope_by_financial_side(FinancialExceptionReview.objects.all(), request.user)
    totals = all_allowed.aggregate(
        critical=Count("id", filter=Q(severity="CRITICAL")),
        high=Count("id", filter=Q(severity="HIGH")),
        evidence_required=Count(
            "id", filter=Q(review_status=FinancialExceptionReview.STATUS_EVIDENCE_REQUIRED)
        ),
        resolved=Count("id", filter=Q(review_status=FinancialExceptionReview.STATUS_RESOLVED)),
    )
    return render(
        request,
        "crm/financial_core/exception_list.html",
        _common(request, page=page, totals=totals, areas=sorted(EVIDENCE_BY_AREA)),
    )


@require_financial_permission("exception_review")
def financial_exception_detail(request, pk):
    exception = get_object_or_404(
        scope_by_financial_side(FinancialExceptionReview.objects.select_related("customer", "supplier", "reviewed_by").prefetch_related(
            "documents", "adjustment_requests__created_by", "adjustment_requests__approved_by"
        ), request.user),
        pk=pk,
    )
    return render(
        request,
        "crm/financial_core/exception_detail.html",
        _common(
            request,
            exception=exception,
            decision_form=FinancialExceptionDecisionForm(),
            evidence_form=FinancialEvidenceUploadForm(),
            adjustment_form=FinancialAdjustmentProposalForm(exception=exception, user=request.user),
            can_create_adjustment=can_create_financial_adjustment(request.user),
            can_approve_adjustment=can_approve_financial_adjustment(request.user),
        ),
    )


def _audit_exception(exception, *, actor, action, reason, before, after):
    FinancialAuditEvent.objects.create(
        content_type=ContentType.objects.get_for_model(exception, for_concrete_model=False),
        object_id=exception.pk,
        action=action,
        reason=reason,
        before_value=before,
        after_value=after,
        source_reference=exception.exception_id,
        actor=actor,
    )


@require_POST
@require_financial_permission("exception_review")
def financial_exception_decide(request, pk):
    exception = get_object_or_404(scope_by_financial_side(FinancialExceptionReview.objects.all(), request.user), pk=pk)
    if not request.POST.get("confirmation_token"):
        form = FinancialExceptionDecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, form.errors.as_text())
            return redirect("financial_exception_detail", pk=pk)
        token = signing.dumps(
            {"exception": exception.pk, "action": form.cleaned_data["action"], "notes": form.cleaned_data["notes"]},
            salt="financial-exception-decision",
        )
        return render(
            request,
            "crm/financial_core/confirm_action.html",
            _common(
                request,
                title="Confirm exception review decision",
                summary=f"{exception.exception_id}: {form.cleaned_data['action'].replace('_', ' ').title()}",
                post_url=request.path,
                confirmation_token=token,
            ),
        )
    try:
        payload = signing.loads(
            request.POST["confirmation_token"], salt="financial-exception-decision", max_age=900
        )
    except signing.BadSignature as exc:
        raise Http404("The confirmation expired or is invalid.") from exc
    if payload.get("exception") != exception.pk:
        raise Http404("The confirmation does not match this exception.")
    statuses = dict(FinancialExceptionDecisionForm.ACTION_CHOICES)
    if payload.get("action") not in statuses:
        raise Http404("Unknown review action.")
    with transaction.atomic():
        locked = FinancialExceptionReview.objects.select_for_update().get(pk=exception.pk)
        previous = locked.review_status
        locked.review_status = payload["action"]
        locked.review_notes = payload["notes"]
        locked.reviewed_by = request.user
        locked.reviewed_at = timezone.now()
        locked.modified_by = request.user
        locked.change_reason = payload["notes"]
        locked.save(update_fields=(
            "review_status", "review_notes", "reviewed_by", "reviewed_at", "modified_by", "modified_at", "change_reason"
        ))
        _audit_exception(
            locked,
            actor=request.user,
            action=payload["action"],
            reason=payload["notes"],
            before={"review_status": previous},
            after={"review_status": locked.review_status},
        )
    messages.success(request, "Exception review decision recorded. No financial transaction was changed.")
    return redirect("financial_exception_detail", pk=pk)


@require_POST
@require_financial_permission("exception_review")
def financial_exception_evidence(request, pk):
    exception = get_object_or_404(scope_by_financial_side(FinancialExceptionReview.objects.all(), request.user), pk=pk)
    form = FinancialEvidenceUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, form.errors.as_text())
        return redirect("financial_exception_detail", pk=pk)
    document = _attach_document(
        exception,
        form.cleaned_data["evidence"],
        actor=request.user,
        document_type="HISTORICAL_EVIDENCE",
        description=form.cleaned_data["description"],
    )
    _audit_exception(
        exception,
        actor=request.user,
        action="EVIDENCE_ATTACHED",
        reason=form.cleaned_data["description"],
        before={},
        after={"document_id": document.pk},
    )
    messages.success(request, "Evidence attached. The exception still requires a confirmed review decision.")
    return redirect("financial_exception_detail", pk=pk)


@require_POST
@require_financial_permission("adjustment_create")
def financial_exception_propose_adjustment(request, pk):
    exception = get_object_or_404(scope_by_financial_side(FinancialExceptionReview.objects.all(), request.user), pk=pk)
    if not request.POST.get("confirmation_token"):
        form = FinancialAdjustmentProposalForm(request.POST, exception=exception, user=request.user)
        if not form.is_valid():
            messages.error(request, form.errors.as_text())
            return redirect("financial_exception_detail", pk=pk)
        cleaned = form.cleaned_data
        payload = {
            "exception": exception.pk,
            "adjustment_type": cleaned["adjustment_type"],
            "journal_date": cleaned["journal_date"].isoformat(),
            "side": cleaned["side"],
            "currency": cleaned["currency"],
            "native_amount": str(cleaned["native_amount"]),
            "rate_to_cad": str(cleaned["rate_to_cad"]),
            "rate_to_bdt": str(cleaned["rate_to_bdt"]),
            "debit_account": cleaned["debit_account"].pk,
            "credit_account": cleaned["credit_account"].pk,
            "source_journal": cleaned["source_journal"].pk if cleaned["source_journal"] else None,
            "reason": cleaned["reason"],
            "before_values": cleaned["before_values"],
            "after_values": cleaned["after_values"],
            "evidence_documents": [document.pk for document in cleaned["evidence_documents"]],
        }
        token = signing.dumps(payload, salt="financial-adjustment-proposal", compress=True)
        return render(
            request,
            "crm/financial_core/confirm_action.html",
            _common(
                request,
                title="Confirm adjustment proposal",
                summary=(
                    f"Propose {cleaned['adjustment_type']} for {cleaned['native_amount']} {cleaned['currency']}. "
                    "Approval will not post the correction."
                ),
                post_url=request.path,
                confirmation_token=token,
            ),
        )
    try:
        payload = signing.loads(
            request.POST["confirmation_token"], salt="financial-adjustment-proposal", max_age=900
        )
    except signing.BadSignature as exc:
        raise Http404("The confirmation expired or is invalid.") from exc
    if payload.pop("exception", None) != exception.pk:
        raise Http404("The confirmation does not match this exception.")
    from crm.models import FinancialAccount, FinancialDocument, JournalEntry

    evidence = list(exception.documents.filter(pk__in=payload.pop("evidence_documents")))
    source_journal_id = payload.pop("source_journal")
    try:
        proposal = submit_adjustment_request(
            actor=request.user,
            exception=exception,
            request_id=f"FADJ-{uuid.uuid4().hex[:12].upper()}",
            journal_date=parse_date(payload.pop("journal_date")),
            debit_account=FinancialAccount.objects.get(pk=payload.pop("debit_account")),
            credit_account=FinancialAccount.objects.get(pk=payload.pop("credit_account")),
            source_journal=JournalEntry.objects.filter(pk=source_journal_id).first(),
            evidence_documents=evidence,
            **payload,
        )
    except (FinancialAdjustmentError, ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("financial_exception_detail", pk=pk)
    messages.success(request, f"Adjustment {proposal.request_id} submitted for independent approval. Nothing was posted.")
    return redirect("financial_exception_detail", pk=pk)


@require_POST
@require_financial_permission("adjustment_approve")
def financial_adjustment_decide(request, pk):
    adjustment = get_object_or_404(
        FinancialAdjustmentRequest.objects.filter(side__in=accessible_financial_sides(request.user)), pk=pk
    )
    approve = request.POST.get("decision") == "approve"
    notes = (request.POST.get("notes") or "").strip()
    if request.POST.get("confirm") != "yes":
        if len(notes) < 5:
            messages.error(request, "Approval or rejection notes are required.")
            return redirect("financial_exception_detail", pk=adjustment.exception_id)
        return render(
            request,
            "crm/financial_core/confirm_adjustment.html",
            _common(request, adjustment=adjustment, approve=approve, notes=notes),
        )
    try:
        decide_adjustment_request(adjustment, actor=request.user, approve=approve, notes=notes)
        if approve and adjustment.exception_id:
            FinancialExceptionReview.objects.filter(pk=adjustment.exception_id).update(
                review_status=FinancialExceptionReview.STATUS_APPROVED,
                reviewed_by=request.user,
                reviewed_at=timezone.now(),
                modified_by=request.user,
                change_reason=f"Approved adjustment {adjustment.request_id}; not posted.",
            )
        messages.success(request, "Adjustment decision recorded. No journal was posted.")
    except (FinancialAdjustmentError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("financial_exception_detail", pk=adjustment.exception_id)


@require_financial_permission("exception_review")
def financial_permission_controls(request):
    matrix = (
        ("CEO / Super Admin", "CA and BD", "All financial workflows, sensitive reports, bank details, exceptions"),
        ("Finance", "Assigned accounting sides", "Full workflow, payroll detail, adjustments, reports"),
        ("Accounts", "Assigned accounting sides", "Transactions, supplier payments, bank details, reports"),
        ("Director / Manager", "Assigned side and department", "Approval and department expense reporting"),
        ("Sales", "Owned customers and invoices", "Quotation and owned invoice status/download only"),
        ("Production", "Assigned business side", "Production cost records only"),
        ("HR", "Assigned business side", "Payroll detail and entry only; no payment or profit"),
    )
    return render(
        request,
        "crm/financial_core/permission_controls.html",
        _common(request, matrix=matrix, accessible_sides=sorted(accessible_financial_sides(request.user))),
    )


@require_financial_permission("document")
def financial_document_download(request, pk):
    document = get_object_or_404(FinancialDocument.objects.select_related("content_type"), pk=pk)
    source = document.source_record
    if not can_access_financial_object(request.user, source):
        raise Http404("Document not found.")
    try:
        handle = document.file.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("Document file not found.") from exc
    filename = (document.file.name.rsplit("/", 1)[-1] or f"financial-document-{document.pk}").replace('"', "")
    return FileResponse(handle, as_attachment=True, filename=filename)


@require_http_methods(["GET", "POST"])
def financial_quick_costing_timeline(request, pk):
    if not request.user.is_authenticated or not (
        can_view_internal_costing(request.user)
        or can_manage_financial_transactions(request.user)
        or can_enter_production_cost(request.user)
    ):
        return HttpResponseForbidden("No access")
    quick_costing = get_object_or_404(
        QuickCosting.objects.select_related("opportunity", "factory_timeline", "factory_timeline__source_default"),
        pk=pk,
    )
    if request.method == "GET":
        return redirect("quick_costing_detail", pk=pk)
    snapshot = getattr(quick_costing, "factory_timeline", None)
    daily_default = snapshot.source_default if snapshot else configured_factory_default(quick_costing)
    roles = operations_role_names(request.user)
    can_override_rate = bool(
        request.user.is_superuser
        or roles.intersection({ROLE_CEO, ROLE_FINANCE, ROLE_ADMIN, ROLE_DIRECTOR, ROLE_MANAGER})
    )
    estimate_initial = {
        "estimated_days": snapshot.estimated_production_days if snapshot else None,
        "daily_factory_cost": snapshot.daily_factory_cost if snapshot else getattr(daily_default, "daily_amount", None),
        "daily_cost_currency": snapshot.daily_cost_currency if snapshot else getattr(daily_default, "currency", ""),
    }
    estimate_form = FactoryTimelineEstimateForm(
        prefix="estimate",
        initial=estimate_initial,
        can_override_rate=can_override_rate,
    )
    actual_form = FactoryTimelineActualForm(
        prefix="actual",
        initial={"actual_days": snapshot.actual_production_days if snapshot else None},
    )
    if request.method == "POST":
        if not getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False):
            messages.error(request, "Finance posting is disabled.")
            return redirect("quick_costing_detail", pk=pk)
        action = request.POST.get("action")
        try:
            if action == "estimate":
                estimate_form = FactoryTimelineEstimateForm(
                    request.POST,
                    prefix="estimate",
                    initial=estimate_initial,
                    can_override_rate=can_override_rate,
                )
                if estimate_form.is_valid():
                    if not daily_default:
                        raise FactoryTimelineError(
                            "Configure an active Bangladesh factory daily operating cost in Finance Settings first."
                        )
                    if quick_costing.is_locked and not snapshot:
                        raise FactoryTimelineError(
                            "Historical approved costings remain unchanged; create a costing revision to add a timeline."
                        )
                    if snapshot and snapshot.locked_at:
                        raise FactoryTimelineError("The approved estimate is locked; create a costing revision.")
                    inputs = current_estimated_inputs(quick_costing)
                    save_estimated_factory_timeline(
                        quick_costing,
                        estimated_days=estimate_form.cleaned_data["estimated_days"],
                        daily_default=daily_default,
                        daily_amount_snapshot=estimate_form.cleaned_data["daily_factory_cost"],
                        approved_minimum_margin_percent=(snapshot.approved_minimum_margin_percent if snapshot else None),
                        actor=request.user,
                        **inputs,
                    )
                    messages.success(request, "Factory timeline estimate saved.")
                    return redirect("quick_costing_detail", pk=pk)
            elif action == "actual":
                if not can_enter_production_cost(request.user):
                    return HttpResponseForbidden("Production or Finance permission is required.")
                actual_form = FactoryTimelineActualForm(request.POST, prefix="actual")
                if actual_form.is_valid():
                    if not snapshot:
                        raise FactoryTimelineError("Save and approve the timeline estimate first.")
                    record_actual_factory_timeline(snapshot, actor=request.user, **actual_form.cleaned_data)
                    messages.success(request, "Factory timeline actuals saved.")
                    return redirect("quick_costing_detail", pk=pk)
        except FactoryTimelineError as exc:
            messages.error(request, str(exc))
    return redirect("quick_costing_detail", pk=pk)
