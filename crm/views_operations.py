import csv
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.db.models import F
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from crm.models import (
    AutomationNotification,
    CostingHeader,
    CRMAuditLog,
    EmployeeProfile,
    Invoice,
    ProductionOrder,
    RecentSearch,
    RecentlyViewedRecord,
)
from crm.services.costing_currency import format_finance_money
from crm.services.operations_notifications import (
    filter_notifications_by_search,
    notification_priority_order,
    prepare_notification_display,
    visible_notifications,
)
from crm.services.operations_permissions import (
    OPERATIONS_ROLES,
    PERMISSION_DESCRIPTIONS,
    ROLE_CAPABILITIES,
    ROLE_ADMIN,
    ROLE_CEO,
    can_access_operations_module,
    has_operations_role,
)
from crm.services.employee_profiles import audit_employee_role_changes, employee_display_name, group_names
from crm.services.operations_search import search_operations_records
from crm.services.platform_tools import remember_search, visible_personal_records


NOTIFICATION_FILTERS = (
    ("all", "All"),
    ("unread", "Unread"),
    ("critical", "Critical"),
    ("high", "High"),
    ("normal", "Normal"),
    ("information", "Information"),
    ("mentions", "Mentions"),
    ("tasks", "Tasks"),
    ("approvals", "Approvals"),
    ("production", "Production"),
    ("invoices", "Invoices"),
    ("shipments", "Shipments"),
)


def _can_view_audit(user):
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or user.is_staff or has_operations_role(user, ROLE_CEO))
    )


def _can_manage_roles(user):
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or user.is_staff or has_operations_role(user, ROLE_CEO, ROLE_ADMIN))
    )


def _safe_next_url(request, default_name):
    next_url = request.POST.get("next") or reverse(default_name)
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return reverse(default_name)
    return next_url


def _group_notifications(notifications):
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    groups = {"Today": [], "Yesterday": [], "This Week": [], "Older": []}
    for item in notifications:
        item_date = timezone.localtime(item.created_at).date()
        if item_date == today:
            label = "Today"
        elif item_date == today - timedelta(days=1):
            label = "Yesterday"
        elif item_date >= week_start:
            label = "This Week"
        else:
            label = "Older"
        prepare_notification_display(item)
        groups[label].append(item)
    return [(label, groups[label]) for label in ("Today", "Yesterday", "This Week", "Older") if groups[label]]


@login_required
def notification_list(request):
    valid_filters = {value for value, _label in NOTIFICATION_FILTERS}
    if "filter" in request.GET:
        selected_filter = (request.GET.get("filter") or "all").strip().lower()
        if selected_filter not in valid_filters:
            selected_filter = "all"
        request.session["crm_notification_filter"] = selected_filter
    else:
        selected_filter = request.session.get("crm_notification_filter", "all")
        if selected_filter not in valid_filters:
            selected_filter = "all"
    notification_type = (request.GET.get("type") or "").strip()
    priority = (request.GET.get("priority") or "").strip()
    read_status = (request.GET.get("status") or "").strip()
    search_query = (request.GET.get("q") or "").strip()
    show_older = request.GET.get("older") == "1"
    base_queryset = visible_notifications(request.user)
    queryset = base_queryset.select_related("assigned_user", "record_content_type")
    if selected_filter == "unread":
        queryset = queryset.filter(is_read=False)
    elif selected_filter in {"critical", "high", "normal", "information"}:
        queryset = queryset.filter(priority=selected_filter)
    elif selected_filter == "mentions":
        queryset = queryset.filter(notification_type="mention")
    elif selected_filter == "tasks":
        queryset = queryset.filter(notification_type__in=["task_assigned", "task_completed"])
    elif selected_filter == "approvals":
        queryset = queryset.filter(notification_type__in=["ceo_approval", "ceo_approved", "ceo_rejected"])
    elif selected_filter == "production":
        queryset = queryset.filter(notification_type__in=["production_created", "sample_due", "production_due"])
    elif selected_filter == "invoices":
        queryset = queryset.filter(notification_type="invoice_overdue")
    elif selected_filter == "shipments":
        queryset = queryset.filter(notification_type__in=["shipment_due", "shipment_delayed"])
    if notification_type:
        queryset = queryset.filter(notification_type=notification_type)
    if priority:
        queryset = queryset.filter(priority=priority)
    if read_status == "unread":
        queryset = queryset.filter(is_read=False)
    elif read_status == "read":
        queryset = queryset.filter(is_read=True)
    queryset = filter_notifications_by_search(queryset, search_query)
    cutoff = timezone.now() - timedelta(days=30)
    has_older_notifications = base_queryset.filter(created_at__lt=cutoff).exists()
    if not show_older:
        queryset = queryset.filter(created_at__gte=cutoff)
    notifications = list(
        queryset.annotate(priority_rank=notification_priority_order())
        .order_by("priority_rank", "-created_at", "-id")[:200]
    )
    return render(
        request,
        "crm/operations/notification_list.html",
        {
            "notification_groups": _group_notifications(notifications),
            "notification_type": notification_type,
            "priority": priority,
            "read_status": read_status,
            "unread_count": base_queryset.filter(is_read=False).count(),
            "type_choices": AutomationNotification.TYPE_CHOICES,
            "priority_choices": AutomationNotification.PRIORITY_CHOICES,
            "show_older": show_older,
            "has_older_notifications": has_older_notifications,
            "visible_read_ids": [item.pk for item in notifications if item.is_read],
            "notification_filters": NOTIFICATION_FILTERS,
            "selected_filter": selected_filter,
            "search_query": search_query,
        },
    )


@login_required
def notification_open(request, pk):
    notification = get_object_or_404(visible_notifications(request.user), pk=pk)
    target_url = notification.target_url or reverse("notification_list")
    if not url_has_allowed_host_and_scheme(
        target_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target_url = reverse("notification_list")
    if "dashboard" in urlsplit(target_url).path.casefold():
        target_url = reverse("notification_list")
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at", "updated_at"])
        cache.delete(f"crm-header-unread:{request.user.pk}")
    return redirect(target_url)


@login_required
@require_POST
def notification_mark_read(request, pk):
    notification = get_object_or_404(visible_notifications(request.user), pk=pk)
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at", "updated_at"])
        cache.delete(f"crm-header-unread:{request.user.pk}")
    return redirect(_safe_next_url(request, "notification_list"))


@login_required
@require_POST
def notification_mark_all_read(request):
    updated = visible_notifications(request.user).filter(is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    cache.delete(f"crm-header-unread:{request.user.pk}")
    messages.success(request, f"Marked {updated} notification(s) as read.")
    return redirect(_safe_next_url(request, "notification_list"))


@login_required
@require_POST
def notification_mark_selected_read(request):
    selected_ids = [value for value in request.POST.getlist("notification_ids") if value.isdigit()]
    if not selected_ids:
        messages.warning(request, "Select at least one notification.")
        return redirect(_safe_next_url(request, "notification_list"))
    updated = visible_notifications(request.user).filter(pk__in=selected_ids, is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    cache.delete(f"crm-header-unread:{request.user.pk}")
    messages.success(request, f"Marked {updated} selected notification(s) as read.")
    return redirect(_safe_next_url(request, "notification_list"))


@login_required
@require_POST
def notification_delete_read(request):
    selected_ids = [value for value in request.POST.getlist("notification_ids") if value.isdigit()]
    if not selected_ids:
        messages.warning(request, "No visible read notifications were selected for deletion.")
        return redirect(_safe_next_url(request, "notification_list"))
    queryset = visible_notifications(request.user).filter(pk__in=selected_ids, is_read=True)
    deleted_count = queryset.count()
    queryset.delete()
    cache.delete(f"crm-header-unread:{request.user.pk}")
    messages.success(request, f"Deleted {deleted_count} read notification(s).")
    return redirect(_safe_next_url(request, "notification_list"))


@login_required
def global_search(request):
    query = (request.GET.get("q") or "").strip()
    remember_search(request.user, query)
    groups = search_operations_records(request.user, query, limit=10, include_opportunities=True)
    return render(
        request,
        "crm/operations/global_search.html",
        {"query": query, "result_groups": groups, "result_count": sum(len(rows) for _, rows in groups)},
    )


@login_required
def global_search_suggestions(request):
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        groups = [
            (
                "Recent Searches",
                [
                    {
                        "number": "Search",
                        "name": row.query,
                        "status": "",
                        "amount": "",
                        "url": f"{reverse('global_search')}?q={row.query.replace(' ', '+')}",
                    }
                    for row in RecentSearch.objects.filter(user=request.user)[:5]
                ],
            ),
            (
                "Recent Records",
                [
                    {
                        "number": row.record_type,
                        "name": row.record_label,
                        "status": "Recently viewed",
                        "amount": "",
                        "url": row.target_url,
                    }
                    for row in visible_personal_records(
                        request.user,
                        RecentlyViewedRecord.objects.filter(user=request.user),
                        limit=5,
                    )
                ],
            ),
        ]
        groups = [(label, rows) for label, rows in groups if rows]
    else:
        groups = search_operations_records(request.user, query, limit=10, include_opportunities=True)
    payload = []
    for label, rows in groups:
        payload.append(
            {
                "label": label,
                "rows": [
                    {
                        "number": row["number"] or "",
                        "name": row["name"] or "",
                        "status": row["status"] or "",
                        "amount": row["amount"] or "",
                        "url": row["url"],
                    }
                    for row in rows
                ],
            }
        )
    return JsonResponse({"query": query, "groups": payload})


def _filtered_audit_queryset(request):
    queryset = CRMAuditLog.objects.select_related("actor", "actor__employee_profile")
    filters = {
        "user": (request.GET.get("user") or "").strip(),
        "module": (request.GET.get("module") or "").strip(),
        "action": (request.GET.get("action") or "").strip(),
        "record_id": (request.GET.get("record_id") or "").strip(),
        "date_from": parse_date((request.GET.get("date_from") or "").strip()),
        "date_to": parse_date((request.GET.get("date_to") or "").strip()),
    }
    if filters["user"].isdigit():
        queryset = queryset.filter(actor_id=int(filters["user"]))
    if filters["module"]:
        queryset = queryset.filter(module=filters["module"])
    if filters["action"]:
        queryset = queryset.filter(action_type=filters["action"])
    if filters["record_id"]:
        queryset = queryset.filter(record_id__icontains=filters["record_id"])
    if filters["date_from"]:
        queryset = queryset.filter(created_at__date__gte=filters["date_from"])
    if filters["date_to"]:
        queryset = queryset.filter(created_at__date__lte=filters["date_to"])
    return queryset.order_by("-created_at", "-id"), filters


def _audit_export_values(row):
    actor_name = employee_display_name(row.actor)
    return [
        timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M:%S"),
        actor_name,
        row.module,
        row.record_label or row.record_id,
        row.get_action_type_display(),
        row.field_name,
        row.previous_value,
        row.new_value,
        row.target_url,
    ]


def _export_audit_csv(queryset):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="crm-audit-log.csv"'
    writer = csv.writer(response)
    writer.writerow(["Date", "User", "Module", "Record", "Action", "Field", "Old Value", "New Value", "Link"])
    for row in queryset[:5000]:
        writer.writerow(_audit_export_values(row))
    return response


def _export_audit_excel(queryset):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("CRM Audit Log")
    sheet.append(["Date", "User", "Module", "Record", "Action", "Field", "Old Value", "New Value", "Link"])
    for row in queryset[:5000]:
        sheet.append(_audit_export_values(row))
    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="crm-audit-log.xlsx"'
    return response


@login_required
def audit_log(request):
    if not _can_view_audit(request.user):
        return HttpResponseForbidden("Audit Log is restricted to CEO and administrators.")
    queryset, filters = _filtered_audit_queryset(request)
    export_format = (request.GET.get("export") or "").strip().lower()
    if export_format == "csv":
        return _export_audit_csv(queryset)
    if export_format == "excel":
        return _export_audit_excel(queryset)

    User = get_user_model()
    return render(
        request,
        "crm/operations/audit_log.html",
        {
            "audit_rows": list(queryset[:250]),
            "audit_users": User.objects.filter(crm_audit_logs__isnull=False).select_related("employee_profile").distinct().order_by("username"),
            "module_choices": CRMAuditLog.objects.order_by().values_list("module", flat=True).distinct(),
            "action_choices": CRMAuditLog.ACTION_CHOICES,
            "filters": filters,
        },
    )


@login_required
def operations_queue(request, queue_key):
    today = timezone.localdate()
    if queue_key == "pending-ceo-approvals":
        if not can_access_operations_module(request.user, "quotations"):
            return HttpResponseForbidden("Quotation access is required.")
        title = "Pending CEO Approvals"
        queryset = CostingHeader.objects.select_related("customer", "opportunity", "opportunity__lead").filter(
            quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT,
        ).exclude(quotation_number="")
        if has_operations_role(request.user, "Sales") and not has_operations_role(request.user, ROLE_CEO):
            queryset = queryset.filter(quoted_by=request.user)
        rows = [
            {
                "number": row.quotation_number,
                "name": (row.customer.account_brand if row.customer else "") or row.brand or row.style_name,
                "status": row.get_quotation_status_display(),
                "date": row.quoted_at or row.updated_at,
                "amount": "",
                "url": reverse("cost_sheet_client_quotation", args=[row.pk]),
            }
            for row in queryset.order_by("quoted_at", "id")[:250]
        ]
    elif queue_key in {"production-due-today", "late-production"}:
        if not can_access_operations_module(request.user, "production"):
            return HttpResponseForbidden("Production access is required.")
        queryset = ProductionOrder.objects.select_related("customer", "assigned_production_manager").filter(
            is_archived=False,
        ).exclude(operational_status__in=["shipped", "cancelled"])
        if queue_key == "production-due-today":
            title = "Production Due Today"
            queryset = queryset.filter(bulk_deadline=today)
        else:
            title = "Late Production Orders"
            queryset = queryset.filter(bulk_deadline__lt=today)
        rows = [
            {
                "number": row.order_code or f"Production {row.pk}",
                "name": row.client_name_snapshot or (row.customer.account_brand if row.customer else "") or row.title,
                "status": row.get_operational_status_display(),
                "date": row.bulk_deadline,
                "amount": format_finance_money(row.approved_total_value, row.approved_currency) if row.approved_total_value else "",
                "url": reverse("production_detail", args=[row.pk]),
            }
            for row in queryset.order_by("bulk_deadline", "id")[:250]
        ]
    elif queue_key == "invoices-overdue":
        if not can_access_operations_module(request.user, "invoices"):
            return HttpResponseForbidden("Invoice access is required.")
        title = "Invoices Overdue"
        queryset = Invoice.objects.select_related("customer").filter(is_archived=False).exclude(status__in=["paid", "cancelled"]).filter(
            due_date__lt=today,
            total_amount__gt=F("paid_amount"),
        )
        rows = [
            {
                "number": row.invoice_number,
                "name": (row.customer.account_brand if row.customer else "") or "Invoice customer",
                "status": row.get_status_display(),
                "date": row.due_date,
                "amount": format_finance_money(row.total_amount - row.paid_amount, row.currency),
                "url": reverse("invoice_view", args=[row.pk]),
            }
            for row in queryset.order_by("due_date", "id")[:250]
        ]
    else:
        return HttpResponseForbidden("Unknown operations queue.")
    return render(request, "crm/operations/operations_queue.html", {"queue_title": title, "rows": rows})


def _permission_description(permission):
    return PERMISSION_DESCRIPTIONS.get(
        permission.codename,
        f"Django permission: {permission.name}.",
    )


@login_required
def role_management(request):
    if not _can_manage_roles(request.user):
        return HttpResponseForbidden("Role Management is restricted to CEO and administrators.")
    User = get_user_model()
    allowed_permissions = Permission.objects.select_related("content_type").filter(
        content_type__app_label="crm",
        codename__in=PERMISSION_DESCRIPTIONS,
    ).order_by("content_type__model", "codename")
    allowed_permission_ids = set(allowed_permissions.values_list("id", flat=True))

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "create_role":
            role_name = " ".join((request.POST.get("role_name") or "").split())
            if len(role_name) < 2 or len(role_name) > 150:
                messages.error(request, "Role name must be between 2 and 150 characters.")
            elif Group.objects.filter(name__iexact=role_name).exists():
                messages.error(request, "A role with this name already exists.")
            else:
                selected = {
                    int(value)
                    for value in request.POST.getlist("permissions")
                    if value.isdigit() and int(value) in allowed_permission_ids
                }
                role = Group.objects.create(name=role_name)
                role.permissions.set(selected)
                messages.success(request, f"Role {role.name} created.")
        elif action in {"assign_user", "remove_user"}:
            role = get_object_or_404(Group, pk=request.POST.get("role_id"))
            user = get_object_or_404(User, pk=request.POST.get("user_id"), is_active=True)
            if role.name == ROLE_CEO and not (
                request.user.is_superuser or has_operations_role(request.user, ROLE_CEO)
            ):
                messages.error(request, "Only a CEO or superuser can change CEO role assignments.")
            elif action == "assign_user":
                before_roles = group_names(user)
                role.user_set.add(user)
                audit_employee_role_changes(
                    actor=request.user,
                    target_user=user,
                    before=before_roles,
                    after=group_names(user),
                )
                messages.success(request, f"{employee_display_name(user)} assigned to {role.name}.")
            elif role.name == ROLE_CEO and user == request.user:
                messages.error(request, "You cannot remove your own CEO role.")
            elif role.name == ROLE_CEO and role.user_set.filter(is_active=True).count() <= 1:
                messages.error(request, "The last active CEO cannot be removed.")
            else:
                before_roles = group_names(user)
                role.user_set.remove(user)
                audit_employee_role_changes(
                    actor=request.user,
                    target_user=user,
                    before=before_roles,
                    after=group_names(user),
                )
                messages.success(request, f"{employee_display_name(user)} removed from {role.name}.")
        return redirect("role_management")

    role_rows = []
    groups = Group.objects.prefetch_related("permissions__content_type", "user_set__employee_profile").order_by("name")
    for role in groups:
        permissions = [
            {
                "label": permission.name,
                "description": _permission_description(permission),
            }
            for permission in role.permissions.all()
            if permission.content_type.app_label == "crm"
        ]
        role_rows.append(
            {
                "role": role,
                "members": sorted(
                    (
                        user
                        for user in role.user_set.all()
                        if user.is_active
                        and not user.employee_profile.is_archived
                        and user.employee_profile.status in EmployeeProfile.MENTIONABLE_STATUSES
                    ),
                    key=lambda user: user.get_username().lower(),
                ),
                "permissions": permissions,
                "capabilities": ROLE_CAPABILITIES.get(role.name, ()),
            }
        )
    permission_catalog = [
        {
            "permission": permission,
            "label": permission.name,
            "description": _permission_description(permission),
        }
        for permission in allowed_permissions
    ]
    return render(
        request,
        "crm/operations/role_management.html",
        {
            "role_rows": role_rows,
            "users": User.objects.filter(
                is_active=True,
                employee_profile__is_archived=False,
                employee_profile__status__in=EmployeeProfile.MENTIONABLE_STATUSES,
            ).select_related("employee_profile").order_by("first_name", "last_name", "username"),
            "permission_catalog": permission_catalog,
            "standard_roles": OPERATIONS_ROLES,
        },
    )
