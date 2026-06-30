import json
import os
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import connection
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from crm.models import (
    AutomationNotification,
    CRMSetting,
    CostingHeader,
    Department,
    EmployeeProfile,
    FavoriteRecord,
    Invoice,
    Lead,
    Opportunity,
    Position,
    ProductionOrder,
    SavedFilter,
    UserDashboardPreference,
)
from crm.services.operations_permissions import (
    OPERATIONS_ROLES,
    PERMISSION_DESCRIPTIONS,
    ROLE_ADMIN,
    ROLE_CEO,
    has_operations_role,
)
from crm.services.platform_tools import (
    DASHBOARD_WIDGETS,
    RECORD_CONFIGS,
    can_manage_archives,
    request_performance_summary,
    set_record_archived,
    toggle_favorite,
)


FILTER_MODULE_ROUTES = {
    "leads": "leads_list",
    "opportunities": "opportunities_list",
    "quotations": "cost_sheet_list",
    "production": "production_list",
    "invoices": "invoice_list",
    "customers": "customers_list",
}


def _can_manage_settings(user):
    return bool(user.is_superuser or has_operations_role(user, ROLE_CEO, ROLE_ADMIN))


def _safe_query_params(post):
    ignored = {"csrfmiddlewaretoken", "name", "module", "next"}
    return {
        key: [str(value)[:200] for value in post.getlist(key)[:20]]
        for key in post.keys()
        if key not in ignored
    }


@login_required
@require_POST
def dashboard_preferences(request):
    if request.user.is_superuser or has_operations_role(request.user, ROLE_CEO):
        return HttpResponseForbidden("The CEO dashboard layout is fixed.")
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid dashboard layout."}, status=400)
    allowed = {key for key, _label in DASHBOARD_WIDGETS}
    hidden = [key for key in payload.get("hidden", []) if key in allowed]
    order = [key for key in payload.get("order", []) if key in allowed]
    preference, _created = UserDashboardPreference.objects.update_or_create(
        user=request.user,
        defaults={"hidden_widgets": hidden, "widget_order": order},
    )
    return JsonResponse({"ok": True, "hidden": preference.hidden_widgets, "order": preference.widget_order})


@login_required
@require_POST
def saved_filter_save(request):
    module = (request.POST.get("module") or "").strip().lower()
    name = " ".join((request.POST.get("name") or "").split())[:120]
    if module not in FILTER_MODULE_ROUTES or not name:
        return JsonResponse({"ok": False, "error": "Enter a valid filter name and module."}, status=400)
    row, _created = SavedFilter.objects.update_or_create(
        user=request.user,
        module=module,
        name=name,
        defaults={"query_params": _safe_query_params(request.POST)},
    )
    return JsonResponse({"ok": True, "id": row.pk, "name": row.name})


@login_required
@require_POST
def saved_filter_delete(request, pk):
    get_object_or_404(SavedFilter, pk=pk, user=request.user).delete()
    return redirect("main_dashboard")


@login_required
@require_POST
def favorite_toggle(request, record_type, object_id):
    result = toggle_favorite(request.user, record_type, object_id)
    if result is None:
        return JsonResponse({"ok": False, "error": "Record is not available."}, status=404)
    return JsonResponse({"ok": True, "favorite": result})


@login_required
@require_POST
def archive_record(request, record_type, object_id):
    if record_type not in RECORD_CONFIGS or not can_manage_archives(request.user):
        return HttpResponseForbidden("Archive permission required.")
    archived = (request.POST.get("action") or "archive") != "restore"
    descriptor = set_record_archived(request.user, record_type, object_id, archived)
    if not descriptor:
        raise Http404("Record not found.")
    messages.success(request, f"{descriptor['record_type']} {'archived' if archived else 'restored'}.")
    return redirect(descriptor["target_url"])


def _database_size():
    try:
        if connection.vendor == "sqlite":
            path = Path(connection.settings_dict["NAME"])
            return path.stat().st_size if path.exists() else None
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_database_size(current_database())")
                return int(cursor.fetchone()[0])
    except Exception:
        return None
    return None


@login_required
def system_health(request):
    if not (request.user.is_superuser or has_operations_role(request.user, ROLE_CEO)):
        return HttpResponseForbidden("CEO access required.")
    context = {
        "employee_count": EmployeeProfile.objects.filter(is_archived=False).exclude(status=EmployeeProfile.STATUS_RESIGNED).count(),
        "open_lead_count": Lead.objects.filter(is_archived=False).exclude(lead_status__in=("Converted", "Lost")).count(),
        "open_opportunity_count": Opportunity.objects.filter(is_archived=False, is_open=True).count(),
        "pending_approval_count": CostingHeader.objects.filter(is_archived=False, quotation_number__gt="", quotation_status="draft").count(),
        "production_order_count": ProductionOrder.objects.filter(is_archived=False).count(),
        "invoice_count": Invoice.objects.filter(is_archived=False).count(),
        "notification_count": AutomationNotification.objects.count(),
        "database_size": _database_size(),
        "performance": request_performance_summary(),
        "version": os.environ.get("APP_VERSION") or os.environ.get("GIT_COMMIT") or "Not configured",
        "last_backup": os.environ.get("LAST_BACKUP_AT") or "Not configured",
        "last_deployment": os.environ.get("DEPLOYED_AT") or "Not configured",
    }
    return render(request, "crm/platform/system_health.html", context)


@login_required
def crm_settings(request):
    if not _can_manage_settings(request.user):
        return HttpResponseForbidden("CEO or Admin access required.")
    if request.method == "POST":
        action = request.POST.get("action")
        library = request.POST.get("library")
        model = Position if library == "position" else Department if library == "department" else None
        if action == "add_library" and model:
            name = " ".join((request.POST.get("name") or "").split())[:120]
            code = (request.POST.get("code") or "").strip().lower().replace(" ", "_")[:60]
            if name and code:
                model.objects.update_or_create(code=code, defaults={"name": name, "is_active": True})
                messages.success(request, f"{model.__name__} saved.")
        elif action == "toggle_library" and model:
            row = get_object_or_404(model, pk=request.POST.get("id"))
            row.is_active = not row.is_active
            row.save(update_fields=("is_active",))
            messages.success(request, f"{row.name} {'enabled' if row.is_active else 'disabled'}.")
        return redirect("crm_settings")

    roles = Group.objects.filter(name__in=OPERATIONS_ROLES).prefetch_related("permissions").order_by("name")
    settings_by_category = {}
    for row in CRMSetting.objects.all():
        settings_by_category.setdefault(row.category, []).append(row)
    return render(
        request,
        "crm/platform/settings.html",
        {
            "positions": Position.objects.all(),
            "departments": Department.objects.all(),
            "roles": roles,
            "permission_descriptions": PERMISSION_DESCRIPTIONS,
            "settings_by_category": settings_by_category,
        },
    )
