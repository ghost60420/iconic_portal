from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST

from crm.forms_finance_operations import CustomerAdjustmentOperationForm, FORM_BY_TYPE
from crm.forms_financial_core import ExpenseCategoryForm, FinancialEvidenceUploadForm
from crm.models import (
    Department,
    FinanceOperation,
    FinancialAuditEvent,
    FinancialBudget,
    FinancialDocument,
)
from crm.services.finance_operations import (
    FinanceOperationError,
    FinanceOperationWritesDisabled,
    UTILITY_ACCOUNT_KEYS,
    WORKFLOW_BY_SLUG,
    audit_operation,
    post_operation,
    review_operation,
    submit_operation,
    workflow_definition,
)
from crm.services.financial_permissions import (
    accessible_financial_sides,
    can_access_finance_operation,
    can_manage_financial_transactions,
    can_post_finance_operation,
    can_review_finance_operation,
    can_submit_finance_operation,
    can_view_bank_details,
    can_view_full_posting_preview,
    require_financial_permission,
    scope_finance_operations_for_approval,
    scope_finance_operations_for_user,
)


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
    "money-transfer",
    "owner-investment",
    "owner-withdrawal",
    "loan-received",
    "loan-principal",
    "asset-purchase",
    "inventory-adjustment",
)


def _query_without(request, *keys):
    query = request.GET.copy()
    for key in keys:
        query.pop(key, None)
    return query.urlencode()


def _common(request, **context):
    context.update(
        financial_core_writes_enabled=bool(getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False)),
        financial_core_reporting_active=bool(getattr(settings, "FINANCIAL_CORE_REPORTING_ACTIVE", False)),
        can_view_full_posting=can_view_full_posting_preview(request.user),
        can_post_operations=can_post_finance_operation(request.user),
        can_view_bank_accounts=can_view_bank_details(request.user),
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
    scoped = scope_finance_operations_for_user(FinanceOperation.objects.all(), request.user)
    today = timezone.localdate()
    counts = scoped.aggregate(
        pending=Count("id", filter=Q(state=FinanceOperation.STATE_PENDING)),
        approved=Count("id", filter=Q(state=FinanceOperation.STATE_APPROVED)),
        evidence=Count("id", filter=Q(state=FinanceOperation.STATE_EVIDENCE_REQUIRED)),
        today=Count("id", filter=Q(transaction_date=today)),
        blocked=Count("id", filter=~Q(posting_error="")),
    )
    cards = [
        workflow for slug in PRIMARY_CENTER_SLUGS
        if can_submit_finance_operation(request.user, (workflow := WORKFLOW_BY_SLUG[slug])["operation_type"])
    ]
    recent = _operation_queryset(request).order_by("-created_at")[:8]
    return render(
        request,
        "crm/finance_operations/center.html",
        _common(request, cards=cards, counts=counts, recent=recent, today=today),
    )


def _form_for_request(request, workflow, *, bind=True):
    form_class = FORM_BY_TYPE[workflow["operation_type"]]
    kwargs = {
        "data": request.POST or None if bind else None,
        "files": request.FILES or None if bind else None,
        "user": request.user,
        "workflow": workflow,
    }
    if form_class is CustomerAdjustmentOperationForm:
        kwargs["initial_kind"] = workflow["operation_type"]
    return form_class(**kwargs)


def _utility_context(request, form):
    utility_type = form.data.get("utility_type") if form.is_bound else None
    if not utility_type:
        return {"utility_history": [], "utility_budgets": []}
    history = scope_finance_operations_for_user(FinanceOperation.objects.all(), request.user).filter(
        operation_type=FinanceOperation.TYPE_UTILITY_BILL,
        details__utility_type=utility_type,
    ).exclude(state=FinanceOperation.STATE_REJECTED).order_by("-transaction_date")[:12]
    account_key = UTILITY_ACCOUNT_KEYS.get(utility_type)
    budget_sides = Q(side__in=accessible_financial_sides(request.user)) | Q(side="")
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
        return redirect("finance_operation_create", workflow_slug=workflow_slug)
    form = _form_for_request(request, workflow, bind=not category_post)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                operation = submit_operation(form.build_operation(), actor=request.user)
                _attach_document(
                    operation,
                    form.cleaned_data.get("supporting_document"),
                    actor=request.user,
                    description=f"{workflow['title']} evidence",
                )
            messages.success(request, f"{operation.operation_number} submitted for approval. No journal was posted.")
            return redirect("finance_operation_detail", pk=operation.pk)
        except (FinanceOperationError, ValidationError, ValueError) as exc:
            form.add_error(None, str(exc))
    recent = _operation_queryset(request).filter(operation_type=workflow["operation_type"]).order_by("-created_at")[:10]
    context = _utility_context(request, form) if workflow["operation_type"] == FinanceOperation.TYPE_UTILITY_BILL else {}
    context.update(
        workflow=workflow,
        form=form,
        recent=recent,
        category_form=category_form,
        can_manage_categories=can_manage_categories,
    )
    return render(request, "crm/finance_operations/form.html", _common(request, **context))


@require_financial_permission("operations")
def finance_operation_detail(request, pk):
    operation = _get_operation(request, pk)
    content_type = ContentType.objects.get_for_model(operation, for_concrete_model=False)
    audit_events = FinancialAuditEvent.objects.filter(
        content_type=content_type, object_id=operation.pk
    ).select_related("actor").order_by("-created_at")
    return render(
        request,
        "crm/finance_operations/detail.html",
        _common(
            request,
            operation=operation,
            audit_events=audit_events,
            evidence_form=FinancialEvidenceUploadForm(),
            can_review=can_review_finance_operation(request.user, operation),
            can_attach=(
                operation.state in {
                    FinanceOperation.STATE_PENDING,
                    FinanceOperation.STATE_EVIDENCE_REQUIRED,
                }
                and (operation.submitted_by_id == request.user.pk or can_review_finance_operation(request.user, operation))
            ),
        ),
    )


@require_POST
@require_financial_permission("operations")
def finance_operation_evidence(request, pk):
    operation = _get_operation(request, pk)
    allowed = (
        operation.state in {FinanceOperation.STATE_PENDING, FinanceOperation.STATE_EVIDENCE_REQUIRED}
        and (operation.submitted_by_id == request.user.pk or can_review_finance_operation(request.user, operation))
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
    rows = scope_finance_operations_for_approval(
        FinanceOperation.objects.select_related(
            "department", "submitted_by", "customer", "supplier"
        ).annotate(document_count=Count("documents")),
        request.user,
    ).filter(state__in=(FinanceOperation.STATE_PENDING, FinanceOperation.STATE_EVIDENCE_REQUIRED))
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
    if request.method == "POST":
        notes = (request.POST.get("notes") or "").strip()
        try:
            operation = review_operation(operation, actor=request.user, action=action, notes=notes)
            messages.success(request, f"{operation.operation_number} marked {operation.get_state_display().lower()}.")
            return redirect("finance_operation_detail", pk=operation.pk)
        except FinanceOperationError as exc:
            messages.error(request, str(exc))
    return render(
        request,
        "crm/finance_operations/confirm.html",
        _common(request, operation=operation, action=action, mode="review"),
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
            messages.success(request, f"{operation.operation_number} posted to the Financial Core.")
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
