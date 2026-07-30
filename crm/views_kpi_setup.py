from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from crm.models_employee import EmployeeProfile
from crm.models_kpi import KPIRoleTemplate
from crm.models_kpi_assignments import EmployeeKPIRoleAssignment
from crm.models_kpi_release import KPIPolicyApproval
from crm.services.kpi_release import (
    KPIReleaseError,
    activate_assignment_draft,
    approve_policy,
    preview_assignment_plan,
    publish_policy,
    retire_policy,
    save_assignment_draft,
    submit_policy_for_review,
)
from crm.services.kpi_review_permissions import has_full_kpi_review_access


def _setup_access_required(request):
    if not has_full_kpi_review_access(request.user):
        return HttpResponseForbidden("KPI setup is restricted to CEO and Super Admin.")
    return None


def _error_text(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return " ".join(
                f"{field.replace('_', ' ').title()}: {' '.join(values)}"
                for field, values in exc.message_dict.items()
            )
        return " ".join(exc.messages)
    return str(exc)


def _policy_queryset():
    return KPIPolicyApproval.service_objects.select_related(
        "template_version__template",
        "kpi_settings",
        "bonus_weight_profile",
        "bonus_rule_set",
        "intelligence_rule_set",
        "notification_rule",
        "created_by__employee_profile",
        "submitted_by__employee_profile",
        "approved_by__employee_profile",
        "published_by__employee_profile",
    ).order_by("policy_type", "id")


@login_required
@require_http_methods(["GET", "POST"])
def kpi_policy_management(request):
    denied = _setup_access_required(request)
    if denied:
        return denied
    if request.method == "POST":
        approval = _policy_queryset().filter(pk=request.POST.get("policy_id")).first()
        action = (request.POST.get("action") or "").strip()
        reason = (request.POST.get("reason") or "").strip()
        transitions = {
            "submit": lambda row: submit_policy_for_review(
                row,
                actor=request.user,
                reason=reason,
            ),
            "approve": lambda row: approve_policy(
                row,
                actor=request.user,
                reason=reason,
            ),
            "publish": lambda row: publish_policy(row, actor=request.user),
            "retire": lambda row: retire_policy(
                row,
                actor=request.user,
                reason=reason,
            ),
        }
        if approval is None or action not in transitions:
            messages.error(request, "Select a valid KPI policy action.")
        else:
            try:
                updated = transitions[action](approval)
            except (KPIReleaseError, ValidationError, ValueError) as exc:
                messages.error(request, _error_text(exc))
            else:
                messages.success(
                    request,
                    f"{updated.target_label} is now {updated.get_status_display()}.",
                )
        return redirect("kpi_policy_management")

    policies = list(_policy_queryset())
    status_counts = {
        value: sum(row.status == value for row in policies)
        for value, _label in KPIPolicyApproval.STATUS_CHOICES
    }
    return render(
        request,
        "crm/kpi/setup/policies.html",
        {
            "policies": policies,
            "status_counts": status_counts,
        },
    )


def _assignment_specs(post):
    specs = []
    for number in range(1, 4):
        template = (post.get(f"role_{number}_template") or "").strip()
        if not template:
            continue
        raw_weight = (post.get(f"role_{number}_weight") or "").strip()
        manager = (post.get(f"role_{number}_manager") or "").strip()
        try:
            weight = Decimal(raw_weight)
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(
                {f"role_{number}_weight": "Enter a valid role weight."}
            ) from exc
        specs.append(
            {
                "kpi_template": template,
                "role_weight": weight,
                "manager": manager or None,
                "bonus_eligible": post.get(f"role_{number}_bonus") == "on",
                "notes": (post.get(f"role_{number}_notes") or "").strip(),
            }
        )
    return specs


def _assignment_context(*, selected_employee_id=None, preview=None, posted=None):
    posted = posted or {}
    employees = EmployeeProfile.objects.select_related("user").filter(
        is_archived=False,
        user__is_active=True,
    ).order_by("display_name", "user__username")
    managers = EmployeeProfile.objects.select_related("user").filter(
        is_archived=False,
        user__is_active=True,
        status__in=EmployeeProfile.MENTIONABLE_STATUSES,
    ).order_by("display_name", "user__username")
    templates = KPIRoleTemplate.objects.filter(is_active=True).order_by("name")
    selected_employee = employees.filter(pk=selected_employee_id).first()
    assignments = EmployeeKPIRoleAssignment.objects.select_related(
        "employee__user",
        "kpi_template",
        "manager__employee_profile",
    )
    if selected_employee:
        assignments = assignments.filter(employee=selected_employee)
    else:
        assignments = assignments.none()
    role_rows = []
    for number in range(1, 4):
        role_rows.append(
            {
                "number": number,
                "template": str(posted.get(f"role_{number}_template") or ""),
                "weight": str(posted.get(f"role_{number}_weight") or ""),
                "manager": str(posted.get(f"role_{number}_manager") or ""),
                "bonus": (
                    posted.get(f"role_{number}_bonus") == "on"
                    if posted
                    else True
                ),
                "notes": str(posted.get(f"role_{number}_notes") or ""),
            }
        )
    return {
        "employees": employees,
        "managers": managers,
        "templates": templates,
        "selected_employee": selected_employee,
        "assignments": assignments.order_by("-start_date", "kpi_template__name"),
        "preview": preview,
        "posted": posted,
        "default_start_date": timezone.localdate().isoformat(),
        "role_rows": role_rows,
    }


@login_required
@require_http_methods(["GET", "POST"])
def kpi_assignment_management(request):
    denied = _setup_access_required(request)
    if denied:
        return denied
    selected_employee_id = request.GET.get("employee") or request.POST.get("employee")
    preview = None
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        try:
            if action == "activate":
                assignment_ids = request.POST.getlist("assignment_ids")
                reason = (request.POST.get("reason") or "").strip()
                rows = activate_assignment_draft(
                    assignment_ids=assignment_ids,
                    actor=request.user,
                    reason=reason,
                )
                messages.success(request, f"Activated {len(rows)} KPI role assignments.")
                return redirect(
                    f"{request.path}?employee={rows[0].employee_id}"
                )

            start_date = parse_date(request.POST.get("start_date") or "")
            specs = _assignment_specs(request.POST)
            preview = preview_assignment_plan(
                employee=selected_employee_id,
                assignments=specs,
                start_date=start_date,
                actor=request.user,
            )
            if action == "save_draft":
                rows = save_assignment_draft(
                    employee=preview.employee,
                    assignments=specs,
                    start_date=start_date,
                    actor=request.user,
                )
                messages.success(
                    request,
                    f"Saved {len(rows)} inactive KPI assignment drafts.",
                )
                return redirect(
                    f"{request.path}?employee={preview.employee.pk}"
                )
            if action != "preview":
                raise ValidationError({"action": "Select a valid assignment action."})
        except (KPIReleaseError, ValidationError, ValueError) as exc:
            messages.error(request, _error_text(exc))

    return render(
        request,
        "crm/kpi/setup/assignments.html",
        _assignment_context(
            selected_employee_id=selected_employee_id,
            preview=preview,
            posted=request.POST if request.method == "POST" else None,
        ),
    )
