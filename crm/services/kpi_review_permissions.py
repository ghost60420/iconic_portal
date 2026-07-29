from django.db.models import Q
from django.utils import timezone

from crm.models_employee import EmployeeProfile
from crm.models_kpi_assignments import EmployeeKPIRoleAssignment
from crm.models_kpi_reviews import KPIReview
from crm.services.operations_permissions import (
    ROLE_CEO,
    ROLE_DIRECTOR,
    ROLE_HR,
    ROLE_MANAGER,
    employee_department,
    has_operations_role,
)


def _authenticated(user):
    return bool(user and getattr(user, "is_authenticated", False))


def _department_matches(user, employee):
    department = employee_department(user)
    if not department:
        return False
    employee_department_code = (
        employee.department_ref.code
        if employee.department_ref_id
        else employee.department
    )
    return department == employee_department_code


def has_full_kpi_review_access(user):
    return bool(
        _authenticated(user)
        and (
            user.is_superuser
            or has_operations_role(user, ROLE_CEO)
        )
    )


def has_hr_kpi_review_access(user):
    return bool(
        _authenticated(user)
        and has_operations_role(user, ROLE_HR)
    )


def can_open_kpi_review_queue(user):
    return bool(
        _authenticated(user)
        and (
            has_full_kpi_review_access(user)
            or has_hr_kpi_review_access(user)
            or has_operations_role(user, ROLE_DIRECTOR, ROLE_MANAGER)
        )
    )


def can_manage_kpi_review_queue(user):
    return bool(
        _authenticated(user)
        and (
            has_full_kpi_review_access(user)
            or has_operations_role(user, ROLE_DIRECTOR, ROLE_MANAGER)
        )
    )


def can_view_employee_performance(user, employee):
    if not _authenticated(user):
        return False
    if employee.user_id == user.pk:
        return True
    if has_full_kpi_review_access(user) or has_hr_kpi_review_access(user):
        return True
    if has_operations_role(user, ROLE_DIRECTOR):
        return _department_matches(user, employee)
    today = timezone.localdate()
    return EmployeeKPIRoleAssignment.objects.filter(
        employee=employee,
        manager=user,
        is_active=True,
        is_archived=False,
        start_date__lte=today,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).exists()


def can_view_kpi_review(user, review):
    if not _authenticated(user):
        return False
    if review.employee.user_id == user.pk:
        return review.status in {
            KPIReview.STATUS_APPROVED,
            KPIReview.STATUS_LOCKED,
        }
    if has_full_kpi_review_access(user) or has_hr_kpi_review_access(user):
        return True
    if has_operations_role(user, ROLE_DIRECTOR):
        return _department_matches(user, review.employee)
    return review.manager_id == user.pk


def can_manage_kpi_review(user, review):
    if not _authenticated(user):
        return False
    if review.employee.user_id == user.pk:
        return False
    if has_full_kpi_review_access(user):
        return True
    if has_operations_role(user, ROLE_DIRECTOR):
        return _department_matches(user, review.employee)
    return review.manager_id == user.pk


def can_create_kpi_review(user, employee, *, review_date=None):
    if not _authenticated(user) or employee.user_id == user.pk:
        return False
    if has_full_kpi_review_access(user):
        return True
    if has_operations_role(user, ROLE_DIRECTOR):
        return _department_matches(user, employee)
    review_date = review_date or timezone.localdate()
    return EmployeeKPIRoleAssignment.objects.filter(
        employee=employee,
        manager=user,
        is_active=True,
        is_archived=False,
        start_date__lte=review_date,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=review_date)
    ).exists()


def can_approve_kpi_review(user, review):
    if not _authenticated(user) or review.employee.user_id == user.pk:
        return False
    if has_full_kpi_review_access(user):
        return True
    return bool(
        has_operations_role(user, ROLE_DIRECTOR)
        and _department_matches(user, review.employee)
    )


def can_lock_kpi_review(user, review):
    return can_approve_kpi_review(user, review)


def visible_kpi_reviews(user):
    queryset = KPIReview.objects.select_related(
        "employee__user",
        "employee__department_ref",
        "manager__employee_profile",
        "submitted_by__employee_profile",
        "approved_by__employee_profile",
    )
    if not _authenticated(user):
        return queryset.none()
    if has_full_kpi_review_access(user) or has_hr_kpi_review_access(user):
        return queryset

    visibility = Q(employee__user=user) | Q(manager=user)
    if has_operations_role(user, ROLE_DIRECTOR):
        department = employee_department(user)
        if department:
            visibility |= Q(employee__department_ref__code=department) | Q(
                employee__department=department
            )
    return queryset.filter(visibility).distinct()


def visible_kpi_employees(user, *, as_of=None):
    queryset = EmployeeProfile.objects.select_related(
        "user",
        "department_ref",
        "position_ref",
    ).filter(is_archived=False)
    if not _authenticated(user):
        return queryset.none()
    if has_full_kpi_review_access(user) or has_hr_kpi_review_access(user):
        return queryset
    if has_operations_role(user, ROLE_DIRECTOR):
        department = employee_department(user)
        if not department:
            return queryset.none()
        return queryset.filter(
            Q(department_ref__code=department) | Q(department=department)
        )

    as_of = as_of or timezone.localdate()
    employee_ids = (
        EmployeeKPIRoleAssignment.objects.filter(
            manager=user,
            is_active=True,
            is_archived=False,
            start_date__lte=as_of,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=as_of))
        .values_list("employee_id", flat=True)
    )
    return queryset.filter(pk__in=employee_ids)
