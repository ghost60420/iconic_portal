from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import transaction
from django.urls import reverse

from crm.models import CRMAuditLog
from crm.services.operations_permissions import (
    ROLE_ADMIN,
    ROLE_CEO,
    ROLE_DIRECTOR,
    ROLE_HR,
    ROLE_MANAGER,
    has_operations_role,
    employee_department,
)


def employee_display_name(user):
    if not user:
        return "System"
    cached_profile = getattr(getattr(user, "_state", None), "fields_cache", {}).get("employee_profile")
    if cached_profile is not None:
        display_name = cached_profile.display_name
    else:
        cache_key = f"crm-employee-display:{user.pk}"
        display_name = cache.get(cache_key)
        if display_name is None:
            from crm.models import EmployeeProfile

            display_name = EmployeeProfile.objects.filter(user_id=user.pk).values_list("display_name", flat=True).first() or ""
            cache.set(cache_key, display_name, 300)
    return display_name or user.first_name or user.get_username()


def build_employee_timeline(profile):
    rows = list(
        CRMAuditLog.objects.filter(module="employees", record_id=str(profile.user_id))
        .select_related("actor", "actor__employee_profile")
        .order_by("-created_at", "-id")[:100]
    )
    events = []
    aggregate_role_change = any(row.field_name == "roles" for row in rows)
    for row in rows:
        if row.field_name in {"role_added", "role_removed"} and aggregate_role_change:
            continue
        if row.field_name == "profile":
            title = "Created"
        elif row.field_name == "role_added":
            title = "Role changed"
        elif row.field_name == "role_removed":
            title = "Role changed"
        elif row.field_name == "roles":
            title = "Role changed"
        elif row.field_name == "department":
            title = "Department changed"
        elif row.field_name == "manager":
            title = "Manager changed"
        elif row.field_name == "password_reset":
            title = "Password reset"
        elif row.field_name == "status":
            title = "Status changed"
        elif row.field_name == "active":
            title = "CRM access changed"
        else:
            continue
        detail = row.new_value or row.previous_value or ""
        if row.previous_value and row.new_value and row.field_name not in {"profile", "password_reset"}:
            detail = f"{row.previous_value} → {row.new_value}"
        events.append(
            {
                "timestamp": row.created_at,
                "title": title,
                "detail": detail,
                "actor": employee_display_name(row.actor),
            }
        )
    if not any(row.field_name == "profile" for row in rows):
        events.append(
            {
                "timestamp": profile.created_at,
                "title": "Created",
                "detail": profile.employee_id or "Employee profile created",
                "actor": "System",
            }
        )
    if profile.user.last_login:
        events.append(
            {
                "timestamp": profile.user.last_login,
                "title": "Last login",
                "detail": "CRM sign-in",
                "actor": profile.public_name,
            }
        )
    return sorted(events, key=lambda event: event["timestamp"], reverse=True)


def can_manage_employees(user):
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_superuser
            or user.is_staff
            or user.has_perm("crm.manage_employee_profiles")
            or has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_ADMIN, ROLE_HR)
        )
    )


def can_manage_roles(user):
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_superuser
            or user.is_staff
            or has_operations_role(user, ROLE_CEO, ROLE_ADMIN)
        )
    )


def can_view_all_sales_profiles(user):
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_superuser
            or user.is_staff
            or user.has_perm("crm.view_all_sales_profiles")
            or has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_MANAGER, ROLE_ADMIN)
        )
    )


def can_view_sales_profile(user, target_user):
    if not user or not user.is_authenticated:
        return False
    if user.pk == target_user.pk:
        return True
    if user.is_superuser or user.is_staff or has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_ADMIN):
        return True
    if not has_operations_role(user, ROLE_MANAGER):
        return False
    try:
        viewer_department = employee_department(user)
        target_department = employee_department(target_user)
    except Exception:
        return False
    return bool(viewer_department and viewer_department == target_department)


def can_view_team_performance(user):
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_superuser
            or user.is_staff
            or has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_MANAGER)
        )
    )


def employee_audit(actor, target_user, field_name, old_value, new_value):
    if str(old_value or "") == str(new_value or ""):
        return
    row = CRMAuditLog(
        actor=actor if actor and actor.is_authenticated else None,
        module="employees",
        record_id=str(target_user.pk),
        record_label=employee_display_name(target_user),
        action_type=CRMAuditLog.ACTION_UPDATED,
        field_name=field_name,
        previous_value=str(old_value or "")[:4000],
        new_value=str(new_value or "")[:4000],
        target_url=reverse("employee_edit", args=[target_user.pk]),
    )
    transaction.on_commit(lambda: CRMAuditLog.objects.bulk_create([row]), robust=True)


def group_names(user):
    return list(user.groups.order_by("name").values_list("name", flat=True))


def audit_employee_role_changes(*, actor, target_user, before, after):
    before = sorted(set(before))
    after = sorted(set(after))
    employee_audit(actor, target_user, "roles", ", ".join(before), ", ".join(after))
    for role_name in sorted(set(after) - set(before)):
        employee_audit(actor, target_user, "role_added", "", role_name)
    for role_name in sorted(set(before) - set(after)):
        employee_audit(actor, target_user, "role_removed", role_name, "")


def set_employee_roles(*, actor, target_user, selected_roles):
    before = group_names(target_user)
    selected = list(Group.objects.filter(pk__in=[role.pk for role in selected_roles]))
    target_user.groups.set(selected)
    after = sorted(role.name for role in selected)
    audit_employee_role_changes(actor=actor, target_user=target_user, before=before, after=after)
