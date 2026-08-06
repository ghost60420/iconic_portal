from datetime import date
from pathlib import Path
import os
import subprocess

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.migrations.recorder import MigrationRecorder
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST

from crm.forms_finance_operations import (
    CURRENCIES,
    PAYMENT_METHODS,
    CustomerAdjustmentOperationForm,
    FinanceSetupImportForm,
    FORM_BY_TYPE,
)
from crm.forms_financial_core import ExpenseCategoryForm, FinancialEvidenceUploadForm
from crm.models import (
    CashBankAccount,
    Customer,
    Department,
    ExpenseCategory,
    FinanceOperation,
    FinancialAccount,
    FinancialAdjustmentRequest,
    FinancialAuditEvent,
    FinancialBudget,
    FinancialDocument,
    FinancialExceptionReview,
    FinancialPeriod,
    HistoricalExchangeRate,
    InvoiceSettings,
    Supplier,
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
from crm.services.finance_setup_import import (
    FinanceSetupImportError,
    REQUIRED_FIELDS,
    SUPPORTED_RECORD_TYPES,
    import_finance_setup_csv,
)
from crm.services.financial_permissions import (
    accessible_financial_sides,
    can_access_finance_operation,
    can_manage_financial_transactions,
    can_post_finance_operation,
    can_review_finance_operation,
    can_submit_finance_operation,
    can_view_bank_details,
    can_view_finance_approval_center,
    can_view_finance_readiness,
    can_view_financial_core,
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

FINANCE_MIGRATIONS = (
    "0192_receivables_ledger_phase3b",
    "0193_phase3c_invoice_event_source",
    "0194_financial_core",
    "0195_receivable_financial_journal",
    "0196_financial_readiness",
    "0197_finance_operations_layer",
    "0198_approved_financial_relationship_repairs",
)

FINANCE_ROUTE_NAMES = (
    "finance_operations_center",
    "finance_approval_center",
    "finance_today_activity",
    "finance_live_readiness",
)

FINANCE_WORKFLOW_SLUGS = (
    "customer-payment", "customer-refund", "customer-credit", "customer-credit-note",
    "supplier-bill", "supplier-payment", "expense", "utility", "payroll",
    "production-cost", "factory-daily-cost", "bank-deposit", "bank-withdrawal",
    "cash-deposit", "cash-withdrawal", "money-transfer", "bank-fee",
    "owner-investment", "owner-withdrawal", "loan-received", "loan-principal",
    "asset-purchase", "inventory-adjustment",
)


def _setup_snapshot():
    tables = {
        "account": connection.ops.quote_name(FinancialAccount._meta.db_table),
        "category": connection.ops.quote_name(ExpenseCategory._meta.db_table),
        "cash_bank": connection.ops.quote_name(CashBankAccount._meta.db_table),
        "supplier": connection.ops.quote_name(Supplier._meta.db_table),
        "customer": connection.ops.quote_name(Customer._meta.db_table),
        "rate": connection.ops.quote_name(HistoricalExchangeRate._meta.db_table),
        "period": connection.ops.quote_name(FinancialPeriod._meta.db_table),
        "tax": connection.ops.quote_name(InvoiceSettings._meta.db_table),
        "opening": connection.ops.quote_name(FinancialAdjustmentRequest._meta.db_table),
        "exception": connection.ops.quote_name(FinancialExceptionReview._meta.db_table),
    }
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM {tables['account']}),
              (SELECT COUNT(*) FROM {tables['category']} WHERE is_active = TRUE),
              (SELECT COUNT(*) FROM {tables['cash_bank']} WHERE is_active = TRUE),
              (SELECT COUNT(*) FROM {tables['cash_bank']} WHERE is_active = TRUE AND kind = 'BANK'),
              (SELECT COUNT(*) FROM {tables['cash_bank']} WHERE is_active = TRUE AND kind = 'CASH'),
              (SELECT COUNT(*) FROM {tables['supplier']} WHERE is_active = TRUE),
              (SELECT COUNT(*) FROM {tables['customer']} WHERE is_active = TRUE AND is_archived = FALSE),
              (SELECT COUNT(*) FROM {tables['customer']}
                WHERE is_active = TRUE AND is_archived = FALSE
                  AND (TRIM(COALESCE(customer_code, '')) = ''
                    OR (TRIM(COALESCE(account_brand, '')) = ''
                      AND TRIM(COALESCE(contact_name, '')) = ''))),
              (SELECT COUNT(*) FROM {tables['rate']} WHERE is_approved = TRUE),
              (SELECT COUNT(*) FROM {tables['period']} WHERE state = 'OPEN'),
              (SELECT COUNT(*) FROM {tables['tax']}
                WHERE is_active = TRUE AND TRIM(COALESCE(default_tax_note, '')) <> ''),
              (SELECT COUNT(*) FROM {tables['opening']} WHERE adjustment_type = 'OPENING'),
              (SELECT COUNT(*) FROM {tables['opening']}
                WHERE adjustment_type = 'OPENING' AND state = 'POSTED'),
              (SELECT COUNT(*) FROM {tables['exception']}),
              (SELECT COUNT(*) FROM {tables['exception']}
                WHERE review_status NOT IN ('RESOLVED', 'REJECTED'))
            """
        )
        values = cursor.fetchone()
    return {
        "financial_accounts": values[0],
        "expense_categories": values[1],
        "cash_bank_accounts": values[2],
        "bank_accounts": values[3],
        "cash_accounts": values[4],
        "suppliers": values[5],
        "customers": values[6],
        "customers_needing_review": values[7],
        "approved_exchange_rates": values[8],
        "open_periods": values[9],
        "tax_settings": values[10],
        "opening_balances": values[11],
        "posted_opening_balances": values[12],
        "exception_reviews": values[13],
        "unresolved_exceptions": values[14],
        "payment_methods": len(PAYMENT_METHODS),
        "currencies": len(CURRENCIES),
        "business_sides": 2,
    }


def _setup_checklist(setup):
    opening_ready = (
        setup["opening_balances"] > 0
        and setup["posted_opening_balances"] == setup["opening_balances"]
    )
    return (
        {
            "label": "Chart of Accounts",
            "value": f"{setup['financial_accounts']} configured",
            "ready": setup["financial_accounts"] > 0,
        },
        {
            "label": "Bank Accounts",
            "value": f"{setup['bank_accounts']} active",
            "ready": setup["bank_accounts"] > 0,
        },
        {
            "label": "Cash Accounts",
            "value": f"{setup['cash_accounts']} active",
            "ready": setup["cash_accounts"] > 0,
        },
        {
            "label": "Suppliers",
            "value": f"{setup['suppliers']} active",
            "ready": setup["suppliers"] > 0,
        },
        {
            "label": "Customers verification",
            "value": (
                f"{setup['customers']} active; {setup['customers_needing_review']} need review"
            ),
            "ready": setup["customers"] > 0 and setup["customers_needing_review"] == 0,
        },
        {
            "label": "Expense Categories",
            "value": f"{setup['expense_categories']} active",
            "ready": setup["expense_categories"] > 0,
        },
        {
            "label": "Payment Methods",
            "value": f"{setup['payment_methods']} controlled values",
            "ready": setup["payment_methods"] > 0,
        },
        {
            "label": "Exchange Rates",
            "value": f"{setup['approved_exchange_rates']} approved",
            "ready": setup["approved_exchange_rates"] > 0,
        },
        {
            "label": "Accounting Periods",
            "value": f"{setup['open_periods']} open",
            "ready": setup["open_periods"] > 0,
        },
        {
            "label": "Tax Settings",
            "value": f"{setup['tax_settings']} active",
            "ready": setup["tax_settings"] > 0,
        },
        {
            "label": "Opening Balances",
            "value": (
                f"{setup['posted_opening_balances']} posted of {setup['opening_balances']} approved"
            ),
            "ready": opening_ready,
        },
    )


def _deployed_commit():
    configured = (os.getenv("APP_VERSION") or os.getenv("GIT_COMMIT") or "").strip()
    if configured:
        return configured
    for executable in ("/usr/bin/git", "/usr/local/bin/git", "git"):
        try:
            completed = subprocess.run(
                [executable, "rev-parse", "--short=12", "HEAD"],
                cwd=settings.BASE_DIR,
                capture_output=True,
                check=True,
                text=True,
                timeout=2,
            )
            if completed.stdout.strip():
                return completed.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return "Unavailable"


def _route_snapshot():
    rows = []
    for name in FINANCE_ROUTE_NAMES:
        try:
            rows.append({"name": name, "url": reverse(name), "present": True})
        except NoReverseMatch:
            rows.append({"name": name, "url": "", "present": False})
    for slug in FINANCE_WORKFLOW_SLUGS:
        try:
            workflow_definition(slug)
            rows.append({
                "name": f"finance_operation_create:{slug}",
                "url": reverse("finance_operation_create", args=[slug]),
                "present": True,
            })
        except (FinanceOperationError, NoReverseMatch):
            rows.append({"name": f"finance_operation_create:{slug}", "url": "", "present": False})
    try:
        rows.append({
            "name": "finance_posting_preview",
            "url": reverse("finance_posting_preview", args=[1]),
            "present": True,
        })
    except NoReverseMatch:
        rows.append({"name": "finance_posting_preview", "url": "", "present": False})
    return rows


def _menu_links_present():
    try:
        source = get_template("crm/base.html").template.source
    except Exception:
        return False
    required = (
        "finance_operations_center",
        "finance_approval_center",
        "finance_today_activity",
        "finance_live_readiness",
    )
    return all(name in source for name in required)


def _protected_media_snapshot():
    nginx_path = Path("/etc/nginx/conf.d/iconiccrm.conf")
    if not nginx_path.exists():
        return "unknown", "Nginx configuration is not available in this environment."
    try:
        source = nginx_path.read_text(encoding="utf-8")
    except OSError:
        return "unknown", "Nginx configuration could not be read."
    paths = ("/media/financial_core/", "/media/accounting/", "/media/accounting_docs/")
    protected = all(path in source for path in paths)
    return (
        ("good", "All protected financial media locations are configured.")
        if protected else ("bad", "One or more protected financial media locations are missing.")
    )


def _backup_snapshot():
    backup_root = Path(settings.BASE_DIR).parent / "backups"
    backups = sorted(backup_root.glob("finance_operations_predeploy_*"), reverse=True)
    if not backups:
        return "unknown", "No Finance Operations deployment backup is visible to this application."
    latest = backups[0]
    required = ("db.sqlite3", "media.tar.gz", ".env", "source_snapshot.tar.gz")
    complete = all((latest / filename).exists() for filename in required)
    return (
        ("good", latest.name)
        if complete else ("bad", f"{latest.name} is missing one or more required files.")
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
        can_view_approvals=can_view_finance_approval_center(request.user),
        can_view_core_dashboard=can_view_financial_core(request.user),
        can_view_live_readiness=can_view_finance_readiness(request.user),
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
        ),
    )


@require_http_methods(["GET", "POST"])
@require_financial_permission("operations")
def finance_live_readiness(request):
    if not can_view_finance_readiness(request.user):
        raise PermissionDenied("Finance Live Readiness is restricted to CEO and Super Admin users.")
    setup = _setup_snapshot()
    checklist = _setup_checklist(setup)
    import_form = FinanceSetupImportForm(request.POST or None, request.FILES or None)
    import_result = None
    if request.method == "POST" and import_form.is_valid():
        try:
            import_result = import_finance_setup_csv(
                uploaded_file=import_form.cleaned_data["data_file"],
                actor=request.user,
                approval_reference=import_form.cleaned_data["approval_reference"],
                apply=import_form.cleaned_data["mode"] == FinanceSetupImportForm.MODE_APPLY,
            )
        except FinanceSetupImportError as exc:
            import_form.add_error("data_file", str(exc))
        else:
            if import_result["mode"] == "applied":
                messages.success(
                    request,
                    f"Applied {sum(import_result['created'].values())} approved master record(s).",
                )
                return redirect("finance_live_readiness")
    applied = set(
        MigrationRecorder.Migration.objects.filter(app="crm", name__in=FINANCE_MIGRATIONS)
        .values_list("name", flat=True)
    )
    routes = _route_snapshot()
    protected_status, protected_detail = _protected_media_snapshot()
    backup_status, backup_detail = _backup_snapshot()
    menu_ready = _menu_links_present()
    route_count = sum(row["present"] for row in routes)
    statuses = [
        {"label": "Live production commit", "value": _deployed_commit(), "status": "good"},
        {
            "label": "Applied finance migrations",
            "value": f"{len(applied)} of {len(FINANCE_MIGRATIONS)}",
            "status": "good" if len(applied) == len(FINANCE_MIGRATIONS) else "bad",
        },
        {
            "label": "Finance routes present",
            "value": f"{route_count} of {len(routes)}",
            "status": "good" if route_count == len(routes) else "bad",
        },
        {
            "label": "Finance menu links present",
            "value": "Present" if menu_ready else "Missing",
            "status": "good" if menu_ready else "bad",
        },
        {
            "label": "Financial accounts",
            "value": setup["financial_accounts"],
            "status": "good" if setup["financial_accounts"] else "bad",
        },
        {
            "label": "Expense categories",
            "value": setup["expense_categories"],
            "status": "good" if setup["expense_categories"] else "bad",
        },
        {
            "label": "Cash and bank accounts",
            "value": setup["cash_bank_accounts"],
            "status": "good" if setup["cash_bank_accounts"] else "bad",
        },
        {
            "label": "Suppliers",
            "value": setup["suppliers"],
            "status": "good" if setup["suppliers"] else "bad",
        },
        {
            "label": "Financial Core writes",
            "value": "OFF" if not getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False) else "ON",
            "status": "good" if not getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False) else "bad",
        },
        {
            "label": "Financial Core reporting",
            "value": "OFF" if not getattr(settings, "FINANCIAL_CORE_REPORTING_ACTIVE", False) else "ON",
            "status": "good" if not getattr(settings, "FINANCIAL_CORE_REPORTING_ACTIVE", False) else "bad",
        },
        {
            "label": "Historical exceptions",
            "value": (
                f"{setup['unresolved_exceptions']} unresolved of "
                f"{setup['exception_reviews']} loaded"
            ),
            "status": "bad" if setup["unresolved_exceptions"] or not setup["exception_reviews"] else "good",
        },
        {
            "label": "Opening balances",
            "value": (
                f"{setup['posted_opening_balances']} posted of "
                f"{setup['opening_balances']} approved"
            ),
            "status": (
                "good"
                if setup["opening_balances"]
                and setup["posted_opening_balances"] == setup["opening_balances"]
                else "bad"
            ),
        },
        {"label": "Protected media", "value": protected_detail, "status": protected_status},
        {"label": "Deployment backup", "value": backup_detail, "status": backup_status},
    ]
    return render(
        request,
        "crm/finance_operations/readiness.html",
        _common(
            request,
            statuses=statuses,
            setup=setup,
            setup_checklist=checklist,
            setup_complete=all(item["ready"] for item in checklist),
            import_form=import_form,
            import_result=import_result,
            supported_record_types=SUPPORTED_RECORD_TYPES,
            setup_import_fields=REQUIRED_FIELDS.items(),
            migrations=[{"name": name, "applied": name in applied} for name in FINANCE_MIGRATIONS],
            routes=routes,
        ),
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
