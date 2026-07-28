from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from crm.models_employee import EmployeeProfile
from crm.models_kpi import KPIRoleTemplate
from crm.models_kpi_assignments import (
    EmployeeKPIRoleAssignment,
    EmployeeKPIRoleAssignmentHistory,
)


EXACT_ROLE_WEIGHT = Decimal("100.00")
ZERO_WEIGHT = Decimal("0.00")


def _resolve_employee(employee, *, lock=False):
    employee_id = getattr(employee, "pk", employee)
    if not employee_id:
        raise ValidationError({"employee": "A valid employee is required."})
    queryset = EmployeeProfile.objects.select_related("user")
    if lock:
        queryset = queryset.select_for_update()
    resolved = queryset.filter(pk=employee_id).first()
    if resolved is None:
        raise ValidationError({"employee": "The selected employee does not exist."})
    return resolved


def _resolve_employee_for_read(employee):
    if isinstance(employee, EmployeeProfile) and employee.pk:
        return employee
    return _resolve_employee(employee)


def _resolve_template(kpi_template):
    template_id = getattr(kpi_template, "pk", kpi_template)
    if not template_id:
        raise ValidationError({"kpi_template": "A valid KPI template is required."})
    resolved = KPIRoleTemplate.objects.filter(pk=template_id).first()
    if resolved is None:
        raise ValidationError(
            {"kpi_template": "The selected KPI template does not exist."}
        )
    return resolved


def _resolve_user(user, *, field, required=False):
    if user is None and not required:
        return None
    user_id = getattr(user, "pk", user)
    if not user_id:
        raise ValidationError({field: f"A valid {field.replace('_', ' ')} is required."})
    resolved = get_user_model().objects.filter(pk=user_id).first()
    if resolved is None:
        raise ValidationError(
            {field: f"The selected {field.replace('_', ' ')} does not exist."}
        )
    return resolved


def _assignment_queryset(employee):
    return EmployeeKPIRoleAssignment.objects.filter(employee=employee).select_related(
        "employee__user",
        "kpi_template",
        "manager__employee_profile",
    )


def _effective_on(assignment, as_of):
    return (
        assignment.is_active
        and not assignment.is_archived
        and assignment.start_date <= as_of
        and (assignment.end_date is None or assignment.end_date >= as_of)
    )


def detect_invalid_overlaps(employee=None, *, assignments=None):
    if assignments is None:
        resolved_employee = _resolve_employee_for_read(employee)
        assignments = list(_assignment_queryset(resolved_employee))
    else:
        assignments = list(assignments)

    active_assignments = [
        assignment
        for assignment in assignments
        if assignment.is_active and not assignment.is_archived
    ]
    boundaries = {assignment.start_date for assignment in active_assignments}
    for assignment in active_assignments:
        if assignment.end_date and assignment.end_date < date.max:
            boundaries.add(assignment.end_date + timedelta(days=1))

    invalid = []
    for boundary in sorted(boundaries):
        effective = [
            assignment
            for assignment in active_assignments
            if _effective_on(assignment, boundary)
        ]
        if not effective:
            continue
        total = sum(
            (Decimal(str(assignment.role_weight)) for assignment in effective),
            ZERO_WEIGHT,
        )
        template_ids = [assignment.kpi_template_id for assignment in effective]
        duplicate_templates = sorted(
            {
                template_id
                for template_id in template_ids
                if template_ids.count(template_id) > 1
            }
        )
        reasons = []
        if total != EXACT_ROLE_WEIGHT:
            reasons.append("weight_total")
        if duplicate_templates:
            reasons.append("duplicate_template")
        if reasons:
            invalid.append(
                {
                    "date": boundary,
                    "total": total,
                    "assignment_ids": [
                        assignment.pk for assignment in effective if assignment.pk
                    ],
                    "duplicate_template_ids": duplicate_templates,
                    "reasons": reasons,
                }
            )
    return invalid


def validate_assignment_set(assignments):
    invalid = detect_invalid_overlaps(assignments=assignments)
    if not invalid:
        return True
    first = invalid[0]
    reason = (
        "Duplicate KPI templates overlap."
        if "duplicate_template" in first["reasons"]
        else "Active KPI role weights must total exactly 100.00 percent."
    )
    raise ValidationError(
        {
            "role_weight": (
                f"{reason} Invalid schedule begins {first['date'].isoformat()} "
                f"with total {first['total'].quantize(Decimal('0.01'))}."
            )
        }
    )


def validate_projected_assignment(assignment):
    existing = list(
        _assignment_queryset(assignment.employee).exclude(pk=assignment.pk)
    )
    validate_assignment_set([*existing, assignment])
    return True


def assign_kpi_roles(*, employee, assignments, actor, reason=""):
    assignment_specs = list(assignments)
    if not assignment_specs:
        raise ValidationError({"assignments": "At least one assignment is required."})

    with transaction.atomic():
        resolved_employee = _resolve_employee(employee, lock=True)
        resolved_actor = _resolve_user(actor, field="actor", required=True)
        existing = list(_assignment_queryset(resolved_employee))
        candidates = []
        for spec in assignment_specs:
            candidate = EmployeeKPIRoleAssignment(
                employee=resolved_employee,
                kpi_template=_resolve_template(spec.get("kpi_template")),
                role_weight=spec.get("role_weight"),
                manager=_resolve_user(spec.get("manager"), field="manager"),
                start_date=spec.get("start_date"),
                end_date=spec.get("end_date"),
                is_active=spec.get("is_active", True),
                bonus_eligible=spec.get("bonus_eligible", True),
                notes=spec.get("notes", ""),
                created_by=resolved_actor,
                updated_by=resolved_actor,
                is_archived=spec.get("is_archived", False),
            )
            candidate.full_clean()
            candidates.append(candidate)

        validate_assignment_set([*existing, *candidates])
        for candidate in candidates:
            candidate.save(
                validate_timeline=False,
                history_reason=reason,
                history_change_type=EmployeeKPIRoleAssignmentHistory.CHANGE_CREATED,
            )
        return candidates


def assign_kpi_role(
    *,
    employee,
    kpi_template,
    role_weight,
    start_date,
    actor,
    manager=None,
    end_date=None,
    is_active=True,
    bonus_eligible=True,
    notes="",
    reason="",
):
    return assign_kpi_roles(
        employee=employee,
        assignments=[
            {
                "kpi_template": kpi_template,
                "role_weight": role_weight,
                "manager": manager,
                "start_date": start_date,
                "end_date": end_date,
                "is_active": is_active,
                "bonus_eligible": bonus_eligible,
                "notes": notes,
            }
        ],
        actor=actor,
        reason=reason,
    )[0]


def _resolve_assignment(assignment, *, lock=False):
    assignment_id = getattr(assignment, "pk", assignment)
    if not assignment_id:
        raise ValidationError({"assignment": "A valid assignment is required."})
    queryset = EmployeeKPIRoleAssignment.objects.select_related(
        "employee__user",
        "kpi_template",
        "manager__employee_profile",
    )
    if lock:
        queryset = queryset.select_for_update()
    resolved = queryset.filter(pk=assignment_id).first()
    if resolved is None:
        raise ValidationError({"assignment": "The selected assignment does not exist."})
    return resolved


def update_assignments(
    *,
    updates,
    actor,
    reason="",
    history_change_type=EmployeeKPIRoleAssignmentHistory.CHANGE_UPDATED,
):
    update_specs = list(updates)
    if not update_specs:
        raise ValidationError({"updates": "At least one update is required."})
    resolved_actor = _resolve_user(actor, field="actor", required=True)

    with transaction.atomic():
        resolved_updates = []
        employee_ids = set()
        for spec in update_specs:
            assignment = _resolve_assignment(spec.get("assignment"), lock=True)
            employee_ids.add(assignment.employee_id)
            resolved_updates.append((assignment, dict(spec.get("changes") or {})))
        if len(employee_ids) != 1:
            raise ValidationError(
                {"updates": "One atomic update may affect only one employee."}
            )

        employee = _resolve_employee(employee_ids.pop(), lock=True)
        current = list(_assignment_queryset(employee))
        by_id = {assignment.pk: assignment for assignment in current}
        allowed_fields = {
            "role_weight",
            "manager",
            "start_date",
            "end_date",
            "is_active",
            "bonus_eligible",
            "notes",
            "is_archived",
        }

        for assignment, changes in resolved_updates:
            unknown = set(changes) - allowed_fields
            if unknown:
                raise ValidationError(
                    {
                        "updates": (
                            "Unsupported assignment fields: "
                            + ", ".join(sorted(unknown))
                        )
                    }
                )
            for field, value in changes.items():
                if field == "manager":
                    value = _resolve_user(value, field="manager")
                setattr(assignment, field, value)
            assignment.updated_by = resolved_actor
            assignment.full_clean()
            by_id[assignment.pk] = assignment

        validate_assignment_set(list(by_id.values()))
        saved = []
        for assignment, _changes in resolved_updates:
            assignment.save(
                validate_timeline=False,
                history_reason=reason,
                history_change_type=history_change_type,
            )
            saved.append(assignment)
        return saved


def update_assignment(
    assignment,
    *,
    actor,
    reason="",
    history_change_type=EmployeeKPIRoleAssignmentHistory.CHANGE_UPDATED,
    **changes,
):
    return update_assignments(
        updates=[{"assignment": assignment, "changes": changes}],
        actor=actor,
        reason=reason,
        history_change_type=history_change_type,
    )[0]


def deactivate_assignment(assignment, *, actor, reason=""):
    updated = update_assignment(
        assignment,
        actor=actor,
        reason=reason,
        history_change_type=(
            EmployeeKPIRoleAssignmentHistory.CHANGE_DEACTIVATED
        ),
        is_active=False,
    )
    return updated


def archive_assignment(assignment, *, actor, reason=""):
    updated = update_assignment(
        assignment,
        actor=actor,
        reason=reason,
        history_change_type=EmployeeKPIRoleAssignmentHistory.CHANGE_ARCHIVED,
        is_active=False,
        is_archived=True,
    )
    return updated


def assignments_for_date(employee, as_of):
    resolved_employee = _resolve_employee_for_read(employee)
    return (
        _assignment_queryset(resolved_employee)
        .filter(
            is_active=True,
            is_archived=False,
            start_date__lte=as_of,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=as_of))
        .order_by("kpi_template__name", "id")
    )


def current_assignments(employee):
    return assignments_for_date(employee, timezone.localdate())


def total_active_weight(employee, as_of=None):
    as_of = as_of or timezone.localdate()
    return (
        assignments_for_date(employee, as_of).aggregate(total=Sum("role_weight"))[
            "total"
        ]
        or ZERO_WEIGHT
    )


def validate_exact_100(employee, as_of=None):
    total = total_active_weight(employee, as_of)
    if total not in {ZERO_WEIGHT, EXACT_ROLE_WEIGHT}:
        raise ValidationError(
            {
                "role_weight": (
                    "Active KPI role weights must total exactly 100.00 percent; "
                    f"found {total.quantize(Decimal('0.01'))}."
                )
            }
        )
    return True


def get_assigned_manager(employee, *, kpi_template=None, as_of=None):
    assignments = assignments_for_date(
        employee,
        as_of or timezone.localdate(),
    )
    if kpi_template is not None:
        assignments = assignments.filter(kpi_template=_resolve_template(kpi_template))
    manager_ids = list(
        assignments.exclude(manager_id=None)
        .values_list("manager_id", flat=True)
        .distinct()
    )
    if not manager_ids:
        return None
    if len(manager_ids) > 1:
        raise ValidationError(
            {"manager": "The employee has multiple KPI managers for this date."}
        )
    return get_user_model().objects.get(pk=manager_ids[0])


def bonus_eligible_assignments(employee, as_of=None):
    return assignments_for_date(
        employee,
        as_of or timezone.localdate(),
    ).filter(bonus_eligible=True)


def employee_kpi_template_set(employee, as_of=None):
    template_ids = assignments_for_date(
        employee,
        as_of or timezone.localdate(),
    ).values_list("kpi_template_id", flat=True)
    return KPIRoleTemplate.objects.filter(pk__in=template_ids).order_by("name")


list_current_assignments = current_assignments
list_assignments_for_date = assignments_for_date
calculate_total_active_weight = total_active_weight
get_bonus_eligible_assignments = bonus_eligible_assignments
get_employee_kpi_template_set = employee_kpi_template_set
