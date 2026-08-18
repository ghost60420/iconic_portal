from datetime import date
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST

from crm.forms_finance_operations import (
    CURRENCIES,
    PAYMENT_METHODS,
    CustomerAdjustmentOperationForm,
    FORM_BY_TYPE,
)
from crm.forms_financial_core import ExpenseCategoryForm, FinancialEvidenceUploadForm
from crm.models import (
    CashBankAccount,
    Department,
    ExpenseCategory,
    FinanceOperation,
    FinancialAccount,
    FinancialAuditEvent,
    FinancialBudget,
    FinancialDocument,
    Supplier,
)
from crm.services.finance_operations import (
    FinanceOperationError,
    FinanceOperationWritesDisabled,
    UTILITY_ACCOUNT_KEYS,
    WORKFLOW_BY_SLUG,
    WORKFLOW_BY_TYPE,
    audit_operation,
    post_operation,
    review_operation,
    save_draft_operation,
    submit_operation,
    workflow_definition,
)
from crm.services.operations_permissions import ROLE_CEO, ROLE_FINANCE, operations_role_names
from crm.services.financial_permissions import (
    accessible_financial_sides,
    can_access_finance_operation,
    can_manage_financial_transactions,
    can_post_finance_operation,
    can_review_finance_operation,
    can_submit_finance_operation,
    can_view_bank_details,
    can_view_finance_approval_center,
    can_view_full_posting_preview,
    has_final_finance_approval_authority,
    require_financial_permission,
    scope_finance_operations_for_approval,
    scope_finance_operations_for_user,
)


logger = logging.getLogger(__name__)


PRIMARY_CENTER_SLUGS = (
    "customer-payment",
    "customer-refund",
    "supplier-bill",
    "supplier-payment",
    "expense",
    "utility",
    "payroll",
    "production-cost",
    "factory-daily-cost",
    "bank-deposit",
    "bank-withdrawal",
    "cash-deposit",
    "cash-withdrawal",
    "money-transfer",
    "bank-fee",
    "owner-investment",
    "owner-withdrawal",
    "loan-received",
    "loan-principal",
    "asset-purchase",
    "inventory-adjustment",
)

AUDIT_ACTION_LABELS = {
    "TRANSACTION_CREATED": "Transaction Created",
    "DRAFT_SAVED": "Edited",
    "EVIDENCE_ATTACHED": "Evidence Attached",
    "SUBMITTED": "Submitted for Approval",
    "EVIDENCE_REQUIRED": "Returned for Information",
    "APPROVED": "Approved",
    "REJECTED": "Rejected",
    "POSTED": "Posted",
    "POST_BLOCKED": "Posting Blocked",
}

COUNTRY_CENTER_SLUGS = {
    "CA": (
        "customer-payment", "supplier-bill", "supplier-payment", "expense", "utility", "payroll",
        "bank-deposit", "bank-withdrawal", "cash-deposit", "cash-withdrawal", "money-transfer", "bank-fee",
        "asset-purchase", "loan-received", "owner-investment",
    ),
    "BD": (
        "customer-payment", "supplier-bill", "supplier-payment", "expense", "utility", "payroll",
        "production-cost", "factory-daily-cost", "bank-deposit", "bank-withdrawal", "cash-deposit",
        "cash-withdrawal", "money-transfer", "bank-fee", "asset-purchase", "inventory-adjustment",
    ),
}

ADVANCED_FORM_FIELDS = {
    "rate_to_cad", "rate_to_bdt", "destination_rate_to_cad", "destination_rate_to_bdt",
}

FORM_SECTION_LAYOUTS = {
    FinanceOperation.TYPE_CUSTOMER_PAYMENT: (
        ("Transaction Details", "Date, country, currency, and payment reference.", ("transaction_date", "currency", "reference")),
        ("Customer and Invoice", "Choose the customer first, then select an eligible outstanding invoice.", ("customer", "invoice")),
        ("Payment", "Enter the received amount and the account receiving the funds.", ("amount", "payment_method", "payment_account", "supporting_document")),
        ("Notes", "Record the business purpose and any useful review notes.", ("business_purpose", "notes")),
        ("Review", "Use these controls only when an exception requires approval.", ("allow_customer_credit", "duplicate_override", "duplicate_reason")),
    ),
    FinanceOperation.TYPE_SUPPLIER_BILL: (
        ("Bill Information", "Supplier bill number and dates.", ("transaction_date", "reference", "bill_date", "due_date", "currency")),
        ("Supplier", "Select the approved supplier for this country.", ("supplier",)),
        ("Expense Details", "Classify where the cost belongs.", ("category", "department", "production_order", "customer")),
        ("Amounts", "The total must equal subtotal plus tax.", ("amount_before_tax", "tax", "total")),
        ("Evidence", "Add the bill, purpose, or review notes when available.", ("supporting_document", "business_purpose", "notes")),
    ),
    FinanceOperation.TYPE_UTILITY_BILL: (
        ("Utility Information", "Utility, vendor, and service location.", ("utility_type", "vendor", "location", "meter_or_account")),
        ("Billing Period", "Dates covered by this utility bill.", ("billing_period_start", "billing_period_end")),
        ("Bill Details", "Bill date, due date, amount, and reference.", ("transaction_date", "reference", "bill_date", "due_date", "currency", "amount")),
        ("Payment", "Record whether the bill is paid and the account used.", ("payment_status", "payment_account")),
        ("Evidence", "Add the receipt, bill, or review notes when available.", ("supporting_document", "business_purpose", "notes")),
    ),
    FinanceOperation.TYPE_COMPANY_EXPENSE: (
        ("Transaction Details", "Date, country, currency, and receipt reference.", ("transaction_date", "currency", "reference")),
        ("Vendor", "Choose a configured supplier or provide the vendor name.", ("vendor", "vendor_name", "production_order")),
        ("Expense Details", "Classify the expense and responsible department.", ("category", "department", "recurring", "due_date")),
        ("Amounts", "The total must equal subtotal plus tax.", ("amount_before_tax", "tax", "total")),
        ("Payment", "Record payment status, method, and account.", ("payment_status", "payment_method", "payment_account")),
        ("Evidence", "Add the receipt, purpose, or review notes when available.", ("supporting_document", "business_purpose", "notes")),
    ),
    FinanceOperation.TYPE_ACCOUNT_TRANSFER: (
        (
            "Transfer Details",
            "Choose the direction, provider, date, and reference.",
            ("transfer_type", "transaction_date", "transfer_service", "other_transfer_service", "reference"),
        ),
        (
            "Money Sent",
            "Record the exact principal leaving the source company account.",
            ("from_account", "amount", "currency"),
        ),
        (
            "Money Received",
            "Record the exact amount delivered to the destination company account.",
            ("to_account", "destination_amount", "receiving_currency", "provider_exchange_rate"),
        ),
        (
            "Fees",
            "Record only the separate provider fee; the transfer principal is not an expense.",
            ("transfer_fee", "fee_currency"),
        ),
        (
            "Evidence and Notes",
            "Receipt is optional. Add the purpose and any useful review notes.",
            ("supporting_document", "business_purpose", "notes"),
        ),
    ),
}


def _form_sections(form, operation_type):
    layout = FORM_SECTION_LAYOUTS.get(operation_type)
    if layout is None:
        layout = (
            ("Transaction Details", "Core transaction information.", ("transaction_date", "currency", "reference")),
            ("Workflow Details", "Information required for this workflow.", tuple()),
            ("Evidence and Notes", "Optional supporting evidence and business context.", ("supporting_document", "business_purpose", "notes")),
        )
    visible_names = [
        name for name, field in form.fields.items()
        if not field.widget.is_hidden and name not in ADVANCED_FORM_FIELDS
    ]
    assigned = set()
    sections = []
    for title, help_text, names in layout:
        if title == "Workflow Details" and not names:
            names = tuple(
                name for name in visible_names
                if name not in assigned and name not in {"supporting_document", "business_purpose", "notes"}
            )
        fields = [form[name] for name in names if name in visible_names and name not in assigned]
        if fields:
            sections.append({"title": title, "help": help_text, "fields": fields})
            assigned.update(field.name for field in fields)
    remaining = [form[name] for name in visible_names if name not in assigned]
    if remaining:
        sections.append({"title": "Additional Details", "help": "Additional information required for this workflow.", "fields": remaining})
    return sections


def _requested_side(request):
    sides = accessible_financial_sides(request.user)
    requested = (request.GET.get("side") or "").upper().strip()
    if requested:
        if requested not in {"CA", "BD"} or requested not in sides:
            raise PermissionDenied("You do not have access to this business side.")
        return requested
    if len(sides) == 1:
        return next(iter(sides))
    return ""


def _can_use_finance_advanced(user):
    return bool(user.is_superuser or operations_role_names(user) & {ROLE_CEO, ROLE_FINANCE})


def _setup_snapshot():
    account_table = connection.ops.quote_name(FinancialAccount._meta.db_table)
    category_table = connection.ops.quote_name(ExpenseCategory._meta.db_table)
    cash_bank_table = connection.ops.quote_name(CashBankAccount._meta.db_table)
    supplier_table = connection.ops.quote_name(Supplier._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM {account_table} WHERE is_active = TRUE),
              (SELECT COUNT(*) FROM {category_table} WHERE is_active = TRUE),
              (SELECT COUNT(*) FROM {cash_bank_table} WHERE is_active = TRUE),
              (SELECT COUNT(*) FROM {supplier_table} WHERE is_active = TRUE)
            """
        )
        values = cursor.fetchone()
    return {
        "financial_accounts": values[0],
        "expense_categories": values[1],
        "cash_bank_accounts": values[2],
        "suppliers": values[3],
        "payment_methods": len(PAYMENT_METHODS),
        "currencies": len(CURRENCIES),
        "business_sides": 2,
    }


def _query_without(request, *keys):
    query = request.GET.copy()
    for key in keys:
        query.pop(key, None)
    return query.urlencode()


def _common(request, **context):
    can_view_approvals = can_view_finance_approval_center(request.user)
    if can_view_approvals:
        pending_approval_count = context.get("pending_count")
        if pending_approval_count is None:
            pending_approval_count = scope_finance_operations_for_approval(
                FinanceOperation.objects.filter(state=FinanceOperation.STATE_PENDING),
                request.user,
            ).count()
    else:
        pending_approval_count = 0
    context.update(
        financial_core_writes_enabled=bool(getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False)),
        financial_core_reporting_active=bool(getattr(settings, "FINANCIAL_CORE_REPORTING_ACTIVE", False)),
        can_view_full_posting=can_view_full_posting_preview(request.user),
        can_post_operations=can_post_finance_operation(request.user),
        can_view_bank_accounts=can_view_bank_details(request.user),
        can_view_approvals=can_view_approvals,
        finance_pending_approval_count=pending_approval_count,
        show_finance_pending_approval_count=can_view_approvals,
    )
    return context


def _operation_queryset(request):
    queryset = FinanceOperation.objects.select_related(
        "customer",
        "invoice",
        "supplier",
        "supplier_bill",
        "employee__user",
        "department",
        "production_order",
        "opportunity",
        "expense_category__default_account",
        "from_account__gl_account",
        "to_account__gl_account",
        "submitted_by",
        "approved_by",
        "posted_by",
        "primary_journal",
    ).annotate(document_count=Count("documents"))
    return scope_finance_operations_for_user(queryset, request.user)


def _operations_for_side(queryset, side):
    if not side:
        return queryset
    return queryset.filter(
        Q(side=side)
        | Q(operation_type=FinanceOperation.TYPE_ACCOUNT_TRANSFER, to_account__side=side)
    )


def _get_operation(request, pk):
    return get_object_or_404(_operation_queryset(request), pk=pk)


def _attach_document(operation, uploaded_file, *, actor, description):
    if not uploaded_file:
        return None
    content_type = ContentType.objects.get_for_model(operation, for_concrete_model=False)
    document = FinancialDocument(
        content_type=content_type,
        object_id=operation.pk,
        file=uploaded_file,
        document_type="FINANCE_OPERATION_EVIDENCE",
        description=(description or "Supporting evidence").strip(),
        created_by=actor,
        modified_by=actor,
    )
    document.full_clean()
    document.save()
    audit_operation(
        operation,
        "EVIDENCE_ATTACHED",
        actor,
        reason=document.description,
        after={"document_id": document.pk, "filename": uploaded_file.name},
    )
    return document


@require_financial_permission("operations")
def finance_operations_center(request):
    locked_side = _requested_side(request)
    scoped = scope_finance_operations_for_user(FinanceOperation.objects.all(), request.user)
    if locked_side:
        scoped = _operations_for_side(scoped, locked_side)
    today = timezone.localdate()
    counts = scoped.aggregate(
        pending=Count("id", filter=Q(state=FinanceOperation.STATE_PENDING)),
        approved=Count("id", filter=Q(state=FinanceOperation.STATE_APPROVED)),
        evidence=Count("id", filter=Q(state=FinanceOperation.STATE_EVIDENCE_REQUIRED)),
        today=Count("id", filter=Q(transaction_date=today)),
        blocked=Count("id", filter=~Q(posting_error="")),
    )
    card_slugs = COUNTRY_CENTER_SLUGS.get(locked_side, PRIMARY_CENTER_SLUGS)
    cards = [
        workflow for slug in card_slugs
        if can_submit_finance_operation(request.user, (workflow := WORKFLOW_BY_SLUG[slug])["operation_type"])
    ]
    recent = _operation_queryset(request)
    if locked_side:
        recent = _operations_for_side(recent, locked_side)
    recent = recent.order_by("-created_at")[:8]
    setup = _setup_snapshot()
    return render(
        request,
        "crm/finance_operations/center.html",
        _common(
            request,
            cards=cards,
            counts=counts,
            recent=recent,
            today=today,
            setup=setup,
            setup_required=not all(
                setup[key]
                for key in ("financial_accounts", "expense_categories", "cash_bank_accounts", "suppliers")
            ),
            can_manage_setup=can_manage_financial_transactions(request.user),
            locked_side=locked_side,
            country_name={"CA": "Canada", "BD": "Bangladesh"}.get(locked_side, ""),
        ),
    )


def _form_for_request(request, workflow, *, bind=True, locked_side="", initial=None):
    form_class = FORM_BY_TYPE[workflow["operation_type"]]
    data = None
    if bind and request.method == "POST":
        data = request.POST.copy()
        if locked_side:
            data["side"] = locked_side
    kwargs = {
        "data": data,
        "files": request.FILES or None if bind else None,
        "user": request.user,
        "workflow": workflow,
        "locked_side": locked_side,
        "initial": initial,
    }
    if form_class is CustomerAdjustmentOperationForm:
        kwargs["initial_kind"] = workflow["operation_type"]
    return form_class(**kwargs)


def _draft_form_initial(operation):
    saved = operation.details.get("_draft_form_values") if operation else None
    if saved:
        return saved
    if not operation:
        return None
    return {
        "transaction_date": operation.transaction_date,
        "side": operation.side,
        "currency": operation.currency,
        "rate_to_cad": operation.rate_to_cad,
        "rate_to_bdt": operation.rate_to_bdt,
        "reference": operation.reference,
        "business_purpose": operation.business_purpose,
        "notes": operation.notes,
    }


def _draft_for_request(request, workflow):
    raw_id = request.POST.get("draft_id") if request.method == "POST" else request.GET.get("draft")
    if not raw_id:
        return None
    try:
        draft_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise Http404("Finance draft not found.") from exc
    draft = _get_operation(request, draft_id)
    if draft.state != FinanceOperation.STATE_DRAFT or draft.operation_type != workflow["operation_type"]:
        raise Http404("Finance draft not found.")
    if draft.created_by_id != request.user.pk and not can_manage_financial_transactions(request.user):
        raise Http404("Finance draft not found.")
    return draft


def _utility_context(request, form, *, locked_side=""):
    utility_type = form.data.get("utility_type") if form.is_bound else None
    if not utility_type:
        return {"utility_history": [], "utility_budgets": []}
    history = scope_finance_operations_for_user(FinanceOperation.objects.all(), request.user).filter(
        operation_type=FinanceOperation.TYPE_UTILITY_BILL,
        details__utility_type=utility_type,
    ).exclude(state=FinanceOperation.STATE_REJECTED)
    if locked_side:
        history = history.filter(side=locked_side)
    history = history.order_by("-transaction_date")[:12]
    account_key = UTILITY_ACCOUNT_KEYS.get(utility_type)
    budget_sides = Q(side=locked_side) | Q(side="") if locked_side else (
        Q(side__in=accessible_financial_sides(request.user)) | Q(side="")
    )
    budgets = FinancialBudget.objects.filter(
        budget_sides, account__system_key=account_key
    ).select_related("account").order_by(
        "-year", "-month"
    )[:12]
    return {"utility_history": history, "utility_budgets": budgets}


@require_http_methods(["GET", "POST"])
@require_financial_permission("operations")
def finance_operation_create(request, workflow_slug):
    try:
        workflow = workflow_definition(workflow_slug)
    except FinanceOperationError as exc:
        raise Http404(str(exc)) from exc
    if not can_submit_finance_operation(request.user, workflow["operation_type"]):
        raise Http404("Finance workflow not found.")
    editing_draft = _draft_for_request(request, workflow)
    locked_side = _requested_side(request)
    if editing_draft:
        if locked_side and editing_draft.side != locked_side:
            raise PermissionDenied("The draft belongs to a different business side.")
        locked_side = editing_draft.side
    can_manage_categories = (
        workflow["operation_type"] == FinanceOperation.TYPE_COMPANY_EXPENSE
        and can_manage_financial_transactions(request.user)
    )
    category_post = request.method == "POST" and request.POST.get("action") == "category"
    category_form = ExpenseCategoryForm(
        request.POST if category_post else None,
        prefix="category",
        user=request.user,
    ) if can_manage_categories else None
    if category_post and category_form and category_form.is_valid():
        category = category_form.save(commit=False)
        category.created_by = category.modified_by = request.user
        category.change_reason = "Created from Finance Operations Center"
        category.save()
        messages.success(request, f"Expense category {category.name} created.")
        target = reverse("finance_operation_create", kwargs={"workflow_slug": workflow_slug})
        return redirect(f"{target}?side={locked_side}" if locked_side else target)
    form = _form_for_request(
        request,
        workflow,
        bind=not category_post,
        locked_side=locked_side,
        initial=_draft_form_initial(editing_draft),
    )
    missing_document_confirmation = False
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action") or "submit"
        if action not in {"draft", "submit"}:
            form.add_error(None, "Please choose Save Draft or Submit for Approval.")
        else:
            uploaded_document = form.cleaned_data.get("supporting_document")
            has_existing_document = bool(editing_draft and editing_draft.document_count)
            if (
                action == "submit"
                and not uploaded_document
                and not has_existing_document
                and request.POST.get("confirm_missing_document") != "yes"
            ):
                missing_document_confirmation = True
            else:
                try:
                    with transaction.atomic():
                        candidate = form.build_operation()
                        if locked_side and candidate.side != locked_side:
                            raise ValidationError("The business side is locked for this transaction.")
                        candidate.details["_draft_form_values"] = form.draft_form_values()
                        if action == "draft" or editing_draft:
                            operation = save_draft_operation(
                                candidate,
                                actor=request.user,
                                existing=editing_draft,
                            )
                        else:
                            operation = candidate
                        if action == "submit":
                            operation = submit_operation(operation, actor=request.user)
                        _attach_document(
                            operation,
                            uploaded_document,
                            actor=request.user,
                            description=f"{workflow['title']} evidence",
                        )
                    if action == "draft":
                        messages.success(request, "Draft saved successfully.")
                    else:
                        messages.success(
                            request,
                            f"{operation.operation_number} submitted for approval. No journal was posted.",
                        )
                    return redirect("finance_operation_detail", pk=operation.pk)
                except (FinanceOperationError, ValidationError, ValueError) as exc:
                    form.add_error(None, str(exc))
    recent = _operation_queryset(request).filter(operation_type=workflow["operation_type"])
    if locked_side:
        recent = _operations_for_side(recent, locked_side)
    recent = recent.order_by("-created_at")[:10]
    context = (
        _utility_context(request, form, locked_side=locked_side)
        if workflow["operation_type"] == FinanceOperation.TYPE_UTILITY_BILL else {}
    )
    context.update(
        workflow=workflow,
        form=form,
        form_sections=_form_sections(form, workflow["operation_type"]),
        recent=recent,
        category_form=category_form,
        can_manage_categories=can_manage_categories,
        locked_side=locked_side,
        country_name={"CA": "Canada", "BD": "Bangladesh"}.get(locked_side, ""),
        other_side="BD" if locked_side == "CA" else "CA",
        can_switch_side=bool(
            locked_side
            and accessible_financial_sides(request.user) == {"CA", "BD"}
            and _can_use_finance_advanced(request.user)
        ),
        can_view_form_advanced=_can_use_finance_advanced(request.user),
        can_manage_setup=can_manage_financial_transactions(request.user),
        editing_draft=editing_draft,
        missing_document_confirmation=missing_document_confirmation,
    )
    return render(request, "crm/finance_operations/form.html", _common(request, **context))


@require_financial_permission("operations")
def finance_operation_detail(request, pk):
    operation = _get_operation(request, pk)
    content_type = ContentType.objects.get_for_model(operation, for_concrete_model=False)
    audit_events = FinancialAuditEvent.objects.filter(
        content_type=content_type, object_id=operation.pk
    ).select_related("actor").order_by("-created_at")
    for event in audit_events:
        event.display_action = AUDIT_ACTION_LABELS.get(event.action, event.action.replace("_", " ").title())
    can_edit_draft = bool(
        operation.state == FinanceOperation.STATE_DRAFT
        and can_submit_finance_operation(request.user, operation.operation_type)
        and (
            operation.created_by_id == request.user.pk
            or can_manage_financial_transactions(request.user)
        )
    )
    return render(
        request,
        "crm/finance_operations/detail.html",
        _common(
            request,
            operation=operation,
            audit_events=audit_events,
            evidence_form=FinancialEvidenceUploadForm(),
            can_review=can_review_finance_operation(request.user, operation),
            is_self_approved=bool(
                operation.approved_by_id
                and operation.approved_by_id in {operation.created_by_id, operation.submitted_by_id}
            ),
            can_edit_draft=can_edit_draft,
            operation_workflow=WORKFLOW_BY_TYPE[operation.operation_type],
            can_attach=(
                operation.state in {
                    FinanceOperation.STATE_DRAFT,
                    FinanceOperation.STATE_PENDING,
                    FinanceOperation.STATE_EVIDENCE_REQUIRED,
                }
                and (
                    operation.created_by_id == request.user.pk
                    or operation.submitted_by_id == request.user.pk
                    or can_review_finance_operation(request.user, operation)
                )
            ),
        ),
    )


@require_POST
@require_financial_permission("operations")
def finance_operation_evidence(request, pk):
    operation = _get_operation(request, pk)
    allowed = (
        operation.state in {
            FinanceOperation.STATE_DRAFT,
            FinanceOperation.STATE_PENDING,
            FinanceOperation.STATE_EVIDENCE_REQUIRED,
        }
        and (
            operation.created_by_id == request.user.pk
            or operation.submitted_by_id == request.user.pk
            or can_review_finance_operation(request.user, operation)
        )
    )
    if not allowed:
        raise Http404("Finance operation not found.")
    form = FinancialEvidenceUploadForm(request.POST, request.FILES)
    if form.is_valid():
        _attach_document(
            operation,
            form.cleaned_data["evidence"],
            actor=request.user,
            description=form.cleaned_data["description"],
        )
        messages.success(request, "Supporting evidence attached.")
    else:
        messages.error(request, form.errors.as_text())
    return redirect("finance_operation_detail", pk=operation.pk)


@require_financial_permission("operations")
def finance_approval_center(request):
    if not can_view_finance_approval_center(request.user):
        raise Http404("Finance approval center not found.")
    rows = scope_finance_operations_for_approval(
        FinanceOperation.objects.select_related(
            "department", "created_by", "submitted_by", "customer", "supplier"
        ).annotate(document_count=Count("documents")),
        request.user,
    ).filter(state__in=(FinanceOperation.STATE_PENDING, FinanceOperation.STATE_EVIDENCE_REQUIRED))
    status_counts = rows.aggregate(
        pending=Count("id", filter=Q(state=FinanceOperation.STATE_PENDING)),
        returned=Count("id", filter=Q(state=FinanceOperation.STATE_EVIDENCE_REQUIRED)),
    )
    filters = {
        "side": "side",
        "currency": "currency",
        "operation_type": "operation_type",
        "risk": "risk_level",
        "state": "state",
    }
    for parameter, field_name in filters.items():
        value = (request.GET.get(parameter) or "").strip()
        if value:
            rows = rows.filter(**{field_name: value})
    department = (request.GET.get("department") or "").strip()
    if department:
        rows = rows.filter(department_id=department)
    page = Paginator(rows.order_by("-risk_level", "transaction_date", "id"), 50).get_page(request.GET.get("page"))
    departments = Department.objects.filter(pk__in=rows.values("department_id")).order_by("name")
    return render(
        request,
        "crm/finance_operations/approvals.html",
        _common(
            request,
            page=page,
            operation_types=FinanceOperation.TYPE_CHOICES,
            departments=departments,
            selected_filters=request.GET,
            page_query=_query_without(request, "page"),
            pending_count=status_counts["pending"],
            returned_count=status_counts["returned"],
        ),
    )


@require_http_methods(["GET", "POST"])
@require_financial_permission("operations")
def finance_operation_review(request, pk, action):
    operation = _get_operation(request, pk)
    if not can_review_finance_operation(request.user, operation):
        raise Http404("Finance operation not found.")
    action = action.upper()
    if action not in {"APPROVE", "REJECT", "EVIDENCE_REQUIRED"}:
        raise Http404("Unknown review action.")
    requires_self_approval_confirmation = bool(
        action == "APPROVE"
        and request.user.pk in {operation.created_by_id, operation.submitted_by_id}
        and has_final_finance_approval_authority(request.user)
    )
    if request.method == "POST":
        notes = (request.POST.get("notes") or "").strip()
        try:
            operation = review_operation(
                operation,
                actor=request.user,
                action=action,
                notes=notes,
                confirm_self_approval=request.POST.get("confirm_self_approval") == "yes",
            )
            if action == "APPROVE":
                if has_final_finance_approval_authority(request.user):
                    try:
                        operation = post_operation(operation, actor=request.user)
                    except (FinanceOperationWritesDisabled, FinanceOperationError, ValidationError, ValueError) as exc:
                        operation.refresh_from_db()
                        messages.warning(
                            request,
                            f"Transaction approved. Posting is waiting: {exc}",
                        )
                    except Exception as exc:
                        logger.exception(
                            "Unexpected Financial Core blocker after Finance approval",
                            extra={"finance_operation_id": operation.pk},
                        )
                        operation.refresh_from_db()
                        messages.warning(
                            request,
                            f"Transaction approved. Posting is waiting: {exc}",
                        )
                    else:
                        messages.success(request, "Transaction approved and posted successfully.")
                else:
                    messages.success(request, "Transaction approved successfully.")
            elif action == "EVIDENCE_REQUIRED":
                messages.success(request, "Transaction returned for more information.")
            else:
                messages.success(request, "Transaction rejected.")
            return redirect("finance_operation_detail", pk=operation.pk)
        except FinanceOperationError as exc:
            messages.error(request, str(exc))
    return render(
        request,
        "crm/finance_operations/confirm.html",
        _common(
            request,
            operation=operation,
            action=action,
            mode="review",
            requires_self_approval_confirmation=requires_self_approval_confirmation,
        ),
    )


@require_financial_permission("operations")
def finance_posting_preview(request, pk):
    operation = _get_operation(request, pk)
    return render(
        request,
        "crm/finance_operations/posting_preview.html",
        _common(request, operation=operation, preview=operation.posting_preview),
    )


@require_http_methods(["GET", "POST"])
@require_financial_permission("operations")
def finance_operation_post(request, pk):
    operation = _get_operation(request, pk)
    if not can_post_finance_operation(request.user):
        raise Http404("Finance operation not found.")
    if request.method == "POST":
        try:
            operation = post_operation(operation, actor=request.user)
            messages.success(request, f"{operation.operation_number} posted to the General Ledger.")
            return redirect("finance_operation_detail", pk=operation.pk)
        except FinanceOperationWritesDisabled as exc:
            messages.error(request, str(exc))
            return redirect("finance_operation_detail", pk=operation.pk)
        except (FinanceOperationError, ValidationError, ValueError) as exc:
            messages.error(request, f"Posting failed safely: {exc}")
            return redirect("finance_operation_detail", pk=operation.pk)
    return render(
        request,
        "crm/finance_operations/confirm.html",
        _common(request, operation=operation, action="POST", mode="post"),
    )


@require_financial_permission("operations")
def finance_today_activity(request):
    scoped = _operation_queryset(request)
    rows = scoped
    selected_date = parse_date(request.GET.get("date", "")) or timezone.localdate()
    filters = {
        "side": "side",
        "currency": "currency",
        "operation_type": "operation_type",
        "department": "department_id",
        "user": "submitted_by_id",
    }
    for parameter, field_name in filters.items():
        value = (request.GET.get(parameter) or "").strip()
        if value:
            rows = rows.filter(**{field_name: value})
    filtered_scope = rows
    rows = rows.filter(
        Q(created_at__date=selected_date)
        | Q(approved_at__date=selected_date)
        | Q(posted_at__date=selected_date)
        | Q(posting_attempted_at__date=selected_date)
    ).distinct()
    page = Paginator(rows.order_by("-created_at"), 50).get_page(request.GET.get("page"))
    operation_type = ContentType.objects.get_for_model(FinanceOperation, for_concrete_model=False)
    audit_rows = FinancialAuditEvent.objects.filter(
        content_type=operation_type,
        object_id__in=filtered_scope.values("pk"),
        created_at__date=selected_date,
    ).select_related("actor").order_by("-created_at", "-id")
    audit_page = Paginator(audit_rows, 50).get_page(request.GET.get("audit_page"))
    departments = Department.objects.filter(
        pk__in=scoped.exclude(department_id=None).values("department_id")
    ).order_by("name")
    users = get_user_model().objects.filter(
        pk__in=scoped.exclude(submitted_by_id=None).values("submitted_by_id")
    ).order_by("first_name", "last_name", "username")
    return render(
        request,
        "crm/finance_operations/activity.html",
        _common(
            request,
            page=page,
            audit_page=audit_page,
            selected_date=selected_date,
            operation_types=FinanceOperation.TYPE_CHOICES,
            departments=departments,
            users=users,
            selected_filters=request.GET,
            page_query=_query_without(request, "page"),
            audit_page_query=_query_without(request, "audit_page"),
        ),
    )
