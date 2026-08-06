from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from crm.services.employee_identity import employee_lead_ownership_q
from crm.services.operations_permissions import (
    ROLE_ACCOUNTS,
    ROLE_ADMIN,
    ROLE_CEO,
    ROLE_DIRECTOR,
    ROLE_FINANCE,
    ROLE_HR,
    ROLE_MANAGER,
    ROLE_PRODUCTION,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    operations_role_names,
)


VIEW_ROLES = {ROLE_CEO, ROLE_DIRECTOR, ROLE_MANAGER, ROLE_ACCOUNTS, ROLE_FINANCE}
MANAGE_ROLES = {ROLE_CEO, ROLE_ACCOUNTS, ROLE_FINANCE}
APPROVE_ROLES = {ROLE_CEO, ROLE_DIRECTOR, ROLE_MANAGER, ROLE_FINANCE}
PAYROLL_DETAIL_ROLES = {ROLE_CEO, ROLE_FINANCE, ROLE_HR}
SENSITIVE_REPORT_ROLES = {ROLE_CEO, ROLE_ACCOUNTS, ROLE_FINANCE}
EXCEPTION_REVIEW_ROLES = {ROLE_CEO, ROLE_FINANCE}
SALES_ROLES = {ROLE_SALES, ROLE_SALES_MANAGER}
FINANCE_OPERATION_TYPES_CUSTOMER = {
    "CUSTOMER_PAYMENT", "CUSTOMER_REFUND", "CUSTOMER_CREDIT_NOTE", "CUSTOMER_CREDIT",
}
FINANCE_OPERATION_TYPES_PRODUCTION = {"PRODUCTION_COST", "FACTORY_DAILY_COST"}
FINANCE_OPERATION_TYPES_MANAGER_APPROVAL = {
    "SUPPLIER_BILL", "COMPANY_EXPENSE", "UTILITY_BILL", "PRODUCTION_COST", "FACTORY_DAILY_COST",
}


def _authenticated(user):
    return bool(user and getattr(user, "is_authenticated", False))


def _access(user):
    try:
        return user.access
    except Exception:
        return None


def _roles(user):
    if not _authenticated(user):
        return set()
    cached = getattr(user, "_financial_role_names", None)
    if cached is None:
        cached = operations_role_names(user)
        user._financial_role_names = cached
    return cached


def _has_any_accounting_flag(user):
    access = _access(user)
    return bool(access and (access.can_accounting_ca or access.can_accounting_bd))


def accessible_financial_sides(user):
    if not _authenticated(user):
        return set()
    roles = _roles(user)
    if user.is_superuser or ROLE_CEO in roles:
        return {"CA", "BD"}
    access = _access(user)
    if not access:
        return set()
    sides = set()
    if access.can_accounting_ca and access.role != "BD":
        sides.add("CA")
    if access.can_accounting_bd:
        sides.add("BD")
    if not sides and roles & (SALES_ROLES | {ROLE_PRODUCTION, ROLE_HR}):
        sides.add(access.role)
    return sides


def requested_financial_side(user, requested=""):
    sides = accessible_financial_sides(user)
    requested = (requested or "").upper().strip()
    if requested in sides:
        return requested
    if len(sides) == 1:
        return next(iter(sides))
    return ""


def scope_by_financial_side(queryset, user, field="side", *, include_unassigned=False):
    sides = accessible_financial_sides(user)
    if not sides:
        return queryset.none()
    if sides == {"CA", "BD"}:
        return queryset
    query = Q(**{f"{field}__in": sorted(sides)})
    if include_unassigned:
        query |= Q(**{field: ""})
    return queryset.filter(query)


def employee_department_id(user):
    try:
        return user.employee_profile.department_ref_id
    except Exception:
        return None


def can_view_financial_core(user):
    if not _authenticated(user):
        return False
    if user.is_superuser:
        return True
    return bool(_roles(user) & VIEW_ROLES) and bool(accessible_financial_sides(user))


def can_manage_financial_transactions(user):
    return bool(
        _authenticated(user)
        and (user.is_superuser or bool(_roles(user) & MANAGE_ROLES) and _has_any_accounting_flag(user))
    )


def can_approve_financial_transactions(user):
    return bool(
        _authenticated(user)
        and (user.is_superuser or bool(_roles(user) & APPROVE_ROLES) and _has_any_accounting_flag(user))
    )


def can_view_payroll_detail(user):
    return _authenticated(user) and (user.is_superuser or bool(_roles(user) & PAYROLL_DETAIL_ROLES))


def can_manage_payroll(user):
    return _authenticated(user) and (user.is_superuser or bool(_roles(user) & {ROLE_CEO, ROLE_FINANCE, ROLE_HR}))


def can_pay_payroll(user):
    return _authenticated(user) and (user.is_superuser or bool(_roles(user) & {ROLE_CEO, ROLE_FINANCE}) and _has_any_accounting_flag(user))


def can_view_sensitive_financials(user):
    return _authenticated(user) and (user.is_superuser or bool(_roles(user) & SENSITIVE_REPORT_ROLES))


def can_view_bank_details(user):
    return can_view_sensitive_financials(user) and bool(accessible_financial_sides(user))


def can_export_financial_data(user):
    return can_view_sensitive_financials(user) and bool(accessible_financial_sides(user))


def can_enter_production_cost(user):
    if not _authenticated(user):
        return False
    if user.is_superuser:
        return True
    return bool(_roles(user) & (MANAGE_ROLES | {ROLE_PRODUCTION})) and bool(accessible_financial_sides(user))


def can_view_production_cost(user):
    return can_enter_production_cost(user) or can_approve_financial_transactions(user)


def can_view_sales_finance_scope(user):
    if not _authenticated(user):
        return False
    return can_view_financial_core(user) or bool(_roles(user) & SALES_ROLES)


def can_view_finance_operations(user):
    if not _authenticated(user):
        return False
    if user.is_superuser:
        return True
    return bool(
        _roles(user)
        & {ROLE_CEO, ROLE_FINANCE, ROLE_ACCOUNTS, ROLE_DIRECTOR, ROLE_MANAGER, ROLE_SALES,
           ROLE_SALES_MANAGER, ROLE_PRODUCTION, ROLE_HR}
    ) and bool(accessible_financial_sides(user))


def can_view_finance_approval_center(user):
    if not can_view_finance_operations(user):
        return False
    roles = _roles(user)
    return bool(
        user.is_superuser
        or ROLE_CEO in roles
        or ROLE_FINANCE in roles and _has_any_accounting_flag(user)
        or (bool(roles & {ROLE_DIRECTOR, ROLE_MANAGER}) and _has_any_accounting_flag(user))
    )


def can_submit_finance_operation(user, operation_type):
    if not can_view_finance_operations(user):
        return False
    roles = _roles(user)
    if user.is_superuser or roles & {ROLE_CEO, ROLE_FINANCE, ROLE_ACCOUNTS}:
        return True
    if ROLE_PRODUCTION in roles:
        return operation_type in FINANCE_OPERATION_TYPES_PRODUCTION
    if ROLE_HR in roles:
        return operation_type == "PAYROLL"
    return False


def can_review_finance_operation(user, operation=None):
    if not _authenticated(user):
        return False
    roles = _roles(user)
    if user.is_superuser or ROLE_CEO in roles:
        return True
    if ROLE_FINANCE in roles and _has_any_accounting_flag(user):
        return True
    if not roles & {ROLE_DIRECTOR, ROLE_MANAGER} or not _has_any_accounting_flag(user):
        return False
    if operation is None or operation.operation_type not in FINANCE_OPERATION_TYPES_MANAGER_APPROVAL:
        return False
    department_id = employee_department_id(user)
    return bool(department_id and operation.department_id == department_id)


def can_post_finance_operation(user):
    return bool(
        _authenticated(user)
        and (
            user.is_superuser
            or ROLE_CEO in _roles(user)
            or ROLE_FINANCE in _roles(user) and _has_any_accounting_flag(user)
        )
    )


def can_view_full_posting_preview(user):
    return can_view_sensitive_financials(user)


def scope_finance_operations_for_user(queryset, user):
    if not can_view_finance_operations(user):
        return queryset.none()
    roles = _roles(user)
    scoped = scope_by_financial_side(queryset, user)
    if user.is_superuser or roles & {ROLE_CEO, ROLE_FINANCE, ROLE_ACCOUNTS}:
        return scoped
    creator_scope = Q(created_by=user) | Q(submitted_by=user)
    if roles & {ROLE_DIRECTOR, ROLE_MANAGER}:
        department_id = employee_department_id(user)
        approval_scope = Q(pk__in=[])
        if department_id:
            approval_scope = (
                Q(department_id=department_id, operation_type__in=FINANCE_OPERATION_TYPES_MANAGER_APPROVAL)
                | Q(
                    department_id=department_id,
                    operation_type="PAYROLL",
                    state__in=("APPROVED", "POSTED", "REVERSED"),
                )
            )
        return scoped.filter(creator_scope | approval_scope)
    if ROLE_PRODUCTION in roles:
        return scoped.filter(creator_scope | Q(operation_type__in=FINANCE_OPERATION_TYPES_PRODUCTION))
    if ROLE_HR in roles:
        return scoped.filter(creator_scope | Q(operation_type="PAYROLL"))
    if roles & SALES_ROLES:
        from crm.models import Invoice

        invoice_ids = scope_invoices_for_user(Invoice.objects.all(), user).values("pk")
        return scoped.filter(
            creator_scope | Q(operation_type__in=FINANCE_OPERATION_TYPES_CUSTOMER, invoice_id__in=invoice_ids)
        )
    return scoped.filter(creator_scope)


def can_access_finance_operation(user, operation):
    if not operation or not getattr(operation, "pk", None):
        return False
    from crm.models import FinanceOperation

    return scope_finance_operations_for_user(FinanceOperation.objects.filter(pk=operation.pk), user).exists()


def scope_finance_operations_for_approval(queryset, user):
    if not _authenticated(user):
        return queryset.none()
    roles = _roles(user)
    scoped = scope_by_financial_side(queryset, user)
    if user.is_superuser or ROLE_CEO in roles or ROLE_FINANCE in roles and _has_any_accounting_flag(user):
        return scoped
    if roles & {ROLE_DIRECTOR, ROLE_MANAGER} and _has_any_accounting_flag(user):
        department_id = employee_department_id(user)
        if department_id:
            return scoped.filter(
                department_id=department_id,
                operation_type__in=FINANCE_OPERATION_TYPES_MANAGER_APPROVAL,
            )
    return scoped.none()


def can_download_financial_document(user):
    if not _authenticated(user):
        return False
    return bool(
        user.is_superuser
        or can_view_sales_finance_scope(user)
        or can_view_payroll_detail(user)
        or can_enter_production_cost(user)
        or can_review_financial_exceptions(user)
    )


def can_view_expenses(user):
    return can_manage_financial_transactions(user) or can_approve_financial_transactions(user)


def can_view_supplier_finance(user):
    return can_manage_financial_transactions(user) or can_approve_financial_transactions(user)


def can_review_financial_exceptions(user):
    if not _authenticated(user):
        return False
    if user.is_superuser or bool(_roles(user) & EXCEPTION_REVIEW_ROLES):
        return True
    access = _access(user)
    return bool(
        ROLE_ADMIN in _roles(user)
        and access
        and access.can_view_ceo_tools
        and _has_any_accounting_flag(user)
    )


def can_create_financial_adjustment(user):
    return can_review_financial_exceptions(user) and (user.is_superuser or bool(_roles(user) & {ROLE_CEO, ROLE_FINANCE}))


def can_approve_financial_adjustment(user):
    return can_approve_financial_transactions(user) and bool(
        user.is_superuser or _roles(user) & {ROLE_CEO, ROLE_DIRECTOR, ROLE_FINANCE}
    )


def scope_invoices_for_user(queryset, user):
    if not _authenticated(user):
        return queryset.none()
    roles = _roles(user)
    if user.is_superuser or ROLE_CEO in roles:
        return queryset
    if can_view_financial_core(user):
        return scope_by_financial_side(queryset, user, "invoice_region")
    if not roles & SALES_ROLES:
        return queryset.none()
    ownership = (
        Q(opportunity__assigned_to=user)
        | Q(quick_costing__salesperson=user)
        | Q(quick_costing__opportunity__assigned_to=user)
        | Q(order__opportunity__assigned_to=user)
        | employee_lead_ownership_q(user, "opportunity__lead__")
        | employee_lead_ownership_q(user, "quick_costing__opportunity__lead__")
    )
    return queryset.filter(ownership).distinct()


def can_access_invoice(user, invoice):
    if not invoice or not getattr(invoice, "pk", None):
        return False
    from crm.models import Invoice

    return scope_invoices_for_user(Invoice.objects.filter(pk=invoice.pk), user).exists()


def scope_expenses_for_user(queryset, user):
    scoped = scope_by_financial_side(queryset, user)
    roles = _roles(user)
    if user.is_superuser or roles & {ROLE_CEO, ROLE_FINANCE, ROLE_ACCOUNTS}:
        return scoped
    if roles & {ROLE_DIRECTOR, ROLE_MANAGER}:
        department_id = employee_department_id(user)
        return scoped.filter(department_id=department_id) if department_id else scoped.none()
    return scoped.none()


def scope_supplier_bills_for_user(queryset, user):
    if not (can_manage_financial_transactions(user) or can_approve_financial_transactions(user)):
        return queryset.none()
    return scope_by_financial_side(queryset, user)


def scope_payroll_for_user(queryset, user):
    if not can_view_payroll_detail(user):
        return queryset.none()
    return scope_by_financial_side(queryset, user)


def scope_bank_accounts_for_user(queryset, user):
    if not can_view_bank_details(user):
        return queryset.none()
    return scope_by_financial_side(queryset, user)


def scope_production_costs_for_user(queryset, user):
    if not can_view_production_cost(user):
        return queryset.none()
    roles = _roles(user)
    if user.is_superuser or ROLE_CEO in roles:
        return queryset
    sides = accessible_financial_sides(user)
    query = Q()
    if "BD" in sides:
        query |= Q(production_order__factory_location__iexact="bd")
    if "CA" in sides:
        query |= ~Q(production_order__factory_location__iexact="bd")
    return queryset.filter(query) if query else queryset.none()


def can_access_financial_object(user, record):
    if not _authenticated(user) or record is None:
        return False
    if user.is_superuser:
        return True
    from crm.models import (
        BankReconciliation,
        BankStatementLine,
        CashBankAccount,
        ExpenseRecord,
        FinancialAdjustmentRequest,
        FinancialAuditEvent,
        FinancialExceptionReview,
        FinanceOperation,
        Invoice,
        JournalEntry,
        PayableAllocation,
        PayableEvent,
        PayrollBatch,
        PayrollLine,
        ProductionCostRecord,
        ReceivableAllocation,
        ReceivableEvent,
        Supplier,
        SupplierBill,
    )

    if isinstance(record, Invoice):
        return can_access_invoice(user, record)
    if isinstance(record, ExpenseRecord):
        return scope_expenses_for_user(ExpenseRecord.objects.filter(pk=record.pk), user).exists()
    if isinstance(record, SupplierBill):
        return scope_supplier_bills_for_user(SupplierBill.objects.filter(pk=record.pk), user).exists()
    if isinstance(record, Supplier):
        return can_view_supplier_finance(user) and record.side in accessible_financial_sides(user)
    if isinstance(record, (PayrollBatch, PayrollLine)):
        batch_id = record.batch_id if isinstance(record, PayrollLine) else record.pk
        return scope_payroll_for_user(PayrollBatch.objects.filter(pk=batch_id), user).exists()
    if isinstance(record, CashBankAccount):
        return scope_bank_accounts_for_user(CashBankAccount.objects.filter(pk=record.pk), user).exists()
    if isinstance(record, BankReconciliation):
        return scope_bank_accounts_for_user(CashBankAccount.objects.filter(pk=record.account_id), user).exists()
    if isinstance(record, BankStatementLine):
        return can_access_financial_object(user, record.reconciliation)
    if isinstance(record, ProductionCostRecord):
        return scope_production_costs_for_user(ProductionCostRecord.objects.filter(pk=record.pk), user).exists()
    if isinstance(record, FinancialExceptionReview):
        sides = accessible_financial_sides(user)
        return can_review_financial_exceptions(user) and (
            sides == {"CA", "BD"} or bool(record.side and record.side in sides)
        )
    if isinstance(record, FinancialAdjustmentRequest):
        return can_review_financial_exceptions(user) and record.side in accessible_financial_sides(user)
    if isinstance(record, FinanceOperation):
        return can_access_finance_operation(user, record)
    if isinstance(record, FinancialAuditEvent):
        return can_review_financial_exceptions(user)
    if isinstance(record, JournalEntry):
        return can_view_sensitive_financials(user) and record.side in accessible_financial_sides(user)
    if isinstance(record, ReceivableEvent):
        return can_access_invoice(user, record.source_invoice) if record.source_invoice_id else can_view_financial_core(user)
    if isinstance(record, ReceivableAllocation):
        return can_access_invoice(user, record.invoice)
    if isinstance(record, (PayableEvent, PayableAllocation)):
        bill = record.bill if isinstance(record, PayableAllocation) else record.source_bill
        return can_access_financial_object(user, bill) if bill else can_view_supplier_finance(user)
    return False


PREDICATES = {
    "view": can_view_financial_core,
    "manage": can_manage_financial_transactions,
    "approve": can_approve_financial_transactions,
    "payroll_detail": can_view_payroll_detail,
    "payroll_manage": can_manage_payroll,
    "payroll_pay": can_pay_payroll,
    "sensitive": can_view_sensitive_financials,
    "bank": can_view_bank_details,
    "export": can_export_financial_data,
    "production_cost": can_view_production_cost,
    "sales_scope": can_view_sales_finance_scope,
    "document": can_download_financial_document,
    "expense": can_view_expenses,
    "supplier": can_view_supplier_finance,
    "exception_review": can_review_financial_exceptions,
    "adjustment_create": can_create_financial_adjustment,
    "adjustment_approve": can_approve_financial_adjustment,
    "operations": can_view_finance_operations,
}


def require_financial_permission(action):
    predicate = PREDICATES[action]

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not predicate(request.user):
                raise PermissionDenied("You do not have permission for this Financial Core operation.")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
