import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from crm.models_employee import EmployeeProfile
from crm.models_kpi import KPIRoleTemplate
from crm.models_kpi_assignments import EmployeeKPIRoleAssignment
from crm.models_kpi_bonus import KPIBonusCalculation
from crm.models_kpi_reviews import KPIReview
from crm.services.kpi_review_permissions import (
    has_full_kpi_review_access,
    has_hr_kpi_review_access,
    visible_kpi_employees,
    visible_kpi_reviews,
)
from crm.services.kpi_reviews import verify_approved_snapshot
from crm.services.operations_permissions import (
    ROLE_DIRECTOR,
    ROLE_MANAGER,
    ROLE_SALES_MANAGER,
    has_operations_role,
)


DASHBOARD_CACHE_VERSION = "kpi-dashboard/1.0"
DASHBOARD_CACHE_SECONDS = 60
APPROVED_STATUSES = (KPIReview.STATUS_APPROVED, KPIReview.STATUS_LOCKED)
OPEN_STATUSES = (
    KPIReview.STATUS_DRAFT,
    KPIReview.STATUS_SUBMITTED,
    KPIReview.STATUS_UNDER_REVIEW,
    KPIReview.STATUS_REJECTED,
)
KPI_STATUSES = ("green", "yellow", "red")
LOCATION_CHOICES = (("ca", "Canada"), ("bd", "Bangladesh"))
SCORE_QUANTUM = Decimal("0.01")

AUDIENCE_EMPLOYEE = "employee"
AUDIENCE_MANAGER = "manager"
AUDIENCE_DIRECTOR = "director"
AUDIENCE_HR = "hr"
AUDIENCE_EXECUTIVE = "executive"


class KPIDashboardError(Exception):
    pass


class KPIDashboardPermissionError(KPIDashboardError):
    pass


@dataclass(frozen=True, slots=True)
class DashboardWidget:
    slug: str
    title: str
    size: str = "standard"


@dataclass(frozen=True, slots=True)
class DashboardFilters:
    employee_id: int | None = None
    role_id: int | None = None
    department: str = ""
    location: str = ""
    period_type: str = ""
    month: int | None = None
    quarter: int | None = None
    year: int | None = None
    manager_id: int | None = None
    status: str = ""

    @classmethod
    def from_querydict(cls, values, *, today=None):
        today = today or timezone.localdate()
        year = _bounded_int(values.get("year"), 2000, 2200)
        return cls(
            employee_id=_positive_int(values.get("employee")),
            role_id=_positive_int(values.get("role")),
            department=_text(values.get("department"), 80),
            location=(
                _text(values.get("location"), 10)
                if values.get("location") in dict(LOCATION_CHOICES)
                else ""
            ),
            period_type=(
                values.get("period")
                if values.get("period") in dict(KPIReview.PERIOD_CHOICES)
                else ""
            ),
            month=_bounded_int(values.get("month"), 1, 12),
            quarter=_bounded_int(values.get("quarter"), 1, 4),
            year=year or today.year,
            manager_id=_positive_int(values.get("manager")),
            status=(
                values.get("status")
                if values.get("status") in KPI_STATUSES
                else ""
            ),
        )

    def cache_token(self):
        payload = json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


WIDGETS = {
    AUDIENCE_EMPLOYEE: (
        DashboardWidget("employee-score", "My KPI", "full"),
        DashboardWidget("role-progress", "Assigned Roles", "full"),
        DashboardWidget("trend", "Performance Trend", "full"),
        DashboardWidget("improvement", "Areas Needing Improvement", "full"),
    ),
    AUDIENCE_MANAGER: (
        DashboardWidget("team-summary", "Team Performance", "full"),
        DashboardWidget("review-queue", "Review Queue", "full"),
        DashboardWidget("leaderboard", "Team Ranking", "full"),
        DashboardWidget("trend", "Team Trend", "full"),
        DashboardWidget("risk", "Team Risk", "standard"),
        DashboardWidget("bonus-summary", "Bonus Eligibility", "standard"),
    ),
    AUDIENCE_DIRECTOR: (
        DashboardWidget("executive-summary", "Department KPI", "full"),
        DashboardWidget("department-summary", "Department Ranking", "full"),
        DashboardWidget("trend", "Performance Trend", "full"),
        DashboardWidget("risk", "Risk Indicators", "full"),
        DashboardWidget("health", "Operational KPI Health", "full"),
    ),
    AUDIENCE_HR: (
        DashboardWidget("team-summary", "Company Performance", "full"),
        DashboardWidget("review-queue", "Review Completion", "full"),
        DashboardWidget("department-summary", "Department Ranking", "full"),
        DashboardWidget("trend", "Performance Trend", "full"),
        DashboardWidget("risk", "Employee Risk", "full"),
    ),
    AUDIENCE_EXECUTIVE: (
        DashboardWidget("executive-summary", "Company KPI", "full"),
        DashboardWidget("location-summary", "Canada and Bangladesh", "standard"),
        DashboardWidget("department-summary", "Department Ranking", "wide"),
        DashboardWidget("leaderboard", "Employee Ranking", "wide"),
        DashboardWidget("risk", "Critical Risk", "standard"),
        DashboardWidget("bonus-forecast", "Bonus Forecast", "standard"),
        DashboardWidget("trend", "Company Trend", "wide"),
        DashboardWidget("review-queue", "Review Completion", "full"),
        DashboardWidget("manager-summary", "Manager Performance", "full"),
        DashboardWidget("health", "Operational KPI Health", "full"),
    ),
}

EXPORT_INTERFACES = (
    {"format": "pdf", "label": "PDF", "available": False},
    {"format": "xlsx", "label": "Excel", "available": False},
    {"format": "csv", "label": "CSV", "available": False},
    {"format": "print", "label": "Print", "available": False},
)


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _positive_int(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _bounded_int(value, minimum, maximum):
    result = _positive_int(value)
    return result if result is not None and minimum <= result <= maximum else None


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _score(value):
    result = _decimal(value)
    if result is None or result < 0 or result > 100:
        return None
    return result


def _display_score(value):
    if value is None:
        return None
    return str(value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP))


def _average(values):
    values = tuple(value for value in values if value is not None)
    if not values:
        return None
    return sum(values, Decimal("0")) / len(values)


def _user_name(user):
    if user is None:
        return "Unassigned"
    try:
        profile = user.employee_profile
    except Exception:
        profile = None
    if profile and profile.public_name:
        return profile.public_name
    return user.get_full_name() or user.get_username()


def _employee_name(employee):
    return employee.public_name or employee.user.get_full_name() or employee.user.username


def dashboard_audience(user):
    if not user or not getattr(user, "is_authenticated", False):
        raise KPIDashboardPermissionError("Authentication is required.")
    if has_full_kpi_review_access(user):
        return AUDIENCE_EXECUTIVE
    if has_hr_kpi_review_access(user):
        return AUDIENCE_HR
    if has_operations_role(user, ROLE_DIRECTOR):
        return AUDIENCE_DIRECTOR
    if has_operations_role(user, ROLE_MANAGER, ROLE_SALES_MANAGER):
        return AUDIENCE_MANAGER
    return AUDIENCE_EMPLOYEE


def dashboard_widgets(user):
    return WIDGETS[dashboard_audience(user)]


def scoped_dashboard_reviews(user, audience=None):
    audience = audience or dashboard_audience(user)
    reviews = visible_kpi_reviews(user)
    if audience == AUDIENCE_EMPLOYEE:
        return reviews.filter(employee__user=user)
    if audience == AUDIENCE_MANAGER:
        return reviews.filter(manager=user).exclude(employee__user=user)
    return reviews


def scoped_dashboard_employees(user, audience=None, *, as_of=None):
    audience = audience or dashboard_audience(user)
    if audience == AUDIENCE_EMPLOYEE:
        return EmployeeProfile.objects.select_related(
            "user",
            "user__access",
            "department_ref",
            "position_ref",
        ).filter(user=user, is_archived=False)
    return visible_kpi_employees(user, as_of=as_of).select_related("user__access")


_scoped_reviews = scoped_dashboard_reviews
_scoped_employees = scoped_dashboard_employees


def _date_bounds(filters):
    if filters.month:
        start = date(filters.year, filters.month, 1)
        if filters.month == 12:
            end = date(filters.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(filters.year, filters.month + 1, 1) - timedelta(days=1)
        return start, end
    if filters.quarter:
        start_month = ((filters.quarter - 1) * 3) + 1
        start = date(filters.year, start_month, 1)
        if start_month == 10:
            end = date(filters.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(filters.year, start_month + 3, 1) - timedelta(days=1)
        return start, end
    return date(filters.year, 1, 1), date(filters.year, 12, 31)


def apply_dashboard_filters(queryset, filters, *, include_kpi_status=True):
    start, end = _date_bounds(filters)
    queryset = queryset.filter(review_date__range=(start, end))
    if filters.employee_id:
        queryset = queryset.filter(employee_id=filters.employee_id)
    if filters.manager_id:
        queryset = queryset.filter(manager_id=filters.manager_id)
    if filters.department:
        queryset = queryset.filter(
            Q(employee__department_ref__code=filters.department)
            | Q(employee__department=filters.department)
        )
    if filters.location:
        group_name = (
            getattr(settings, "CA_TEAM_GROUP", "CA_TEAM")
            if filters.location == "ca"
            else getattr(settings, "BD_TEAM_GROUP", "BD_TEAM")
        )
        queryset = queryset.filter(employee__user__groups__name=group_name)
    if filters.period_type:
        queryset = queryset.filter(period_type=filters.period_type)
    if include_kpi_status and filters.status:
        queryset = queryset.filter(
            approved_snapshot__result__status=filters.status
        )
    return queryset.distinct()


def dashboard_filter_options(user, filters):
    audience = dashboard_audience(user)
    employees = list(
        _scoped_employees(user, audience).order_by(
            "display_name",
            "user__username",
        )
    )
    request_access = None
    request_profile = None
    for employee in employees:
        if employee.user_id != user.pk:
            continue
        request_profile = employee
        user._state.fields_cache["employee_profile"] = employee
        access = employee.user._state.fields_cache.get("access")
        if access is not None:
            request_access = access
            user._state.fields_cache["access"] = access
            user._crm_user_access_cached = access
        break
    employee_ids = [employee.pk for employee in employees]
    manager_rows = (
        ()
        if audience == AUDIENCE_EMPLOYEE
        else (
            _scoped_reviews(user, audience)
            .exclude(manager__isnull=True)
            .filter(employee_id__in=employee_ids)
            .values(
                "manager_id",
                "manager__first_name",
                "manager__last_name",
                "manager__username",
            )
            .distinct()
            .order_by("manager__first_name", "manager__username")
        )
    )
    roles = KPIRoleTemplate.objects.filter(
        employee_assignments__employee_id__in=employee_ids
    ).distinct().order_by("name")
    departments = {}
    for employee in employees:
        code = (
            employee.department_ref.code
            if employee.department_ref_id
            else employee.department
        )
        name = (
            employee.department_ref.name
            if employee.department_ref_id
            else employee.get_department_display()
        )
        if code:
            departments[code] = name
    managers = []
    for row in manager_rows:
        name = " ".join(
            part
            for part in (
                row["manager__first_name"],
                row["manager__last_name"],
            )
            if part
        )
        managers.append(
            {
                "id": row["manager_id"],
                "name": name or row["manager__username"],
            }
        )
    return {
        "audience": audience,
        "employees": tuple(
            {"id": employee.pk, "name": _employee_name(employee)}
            for employee in employees
        ),
        "roles": tuple({"id": role.pk, "name": role.name} for role in roles),
        "departments": tuple(
            {"code": code, "name": name}
            for code, name in sorted(departments.items(), key=lambda row: row[1])
        ),
        "locations": tuple(
            {"code": code, "name": name} for code, name in LOCATION_CHOICES
        ),
        "managers": tuple(managers),
        "periods": tuple(
            {"code": code, "name": name}
            for code, name in KPIReview.PERIOD_CHOICES
        ),
        "statuses": tuple(
            {"code": status, "name": status.title()} for status in KPI_STATUSES
        ),
        "selected": filters,
        "exports": EXPORT_INTERFACES,
        "_request_access": request_access,
        "_request_profile": request_profile,
    }


def _snapshot_rows(user, audience, filters):
    queryset = _scoped_reviews(user, audience).filter(status__in=APPROVED_STATUSES)
    queryset = apply_dashboard_filters(queryset, filters)
    rows = []
    invalid = 0
    for review in queryset.order_by("-period_end", "-review_date", "-pk"):
        if not verify_approved_snapshot(review):
            invalid += 1
            continue
        definition = review.approved_snapshot.get("definition", {})
        if filters.role_id and not any(
            role.get("template_id") == filters.role_id
            for role in definition.get("roles", [])
        ):
            continue
        result = review.approved_snapshot.get("result")
        score = _score(result.get("score")) if isinstance(result, dict) else None
        status = result.get("status") if isinstance(result, dict) else None
        if score is None or status not in KPI_STATUSES:
            invalid += 1
            continue
        rows.append(
            {
                "review": review,
                "result": result,
                "score": score,
                "status": status,
                "critical_red": bool(result.get("critical_red")),
            }
        )
    return rows, invalid


def approved_dashboard_snapshot_rows(user, filters, *, audience=None):
    audience = audience or dashboard_audience(user)
    return _snapshot_rows(user, audience, filters)


def _latest_rows(rows):
    latest = {}
    for row in rows:
        latest.setdefault(row["review"].employee_id, row)
    return tuple(latest.values())


def _status_counts(rows):
    counts = {status: 0 for status in KPI_STATUSES}
    for row in rows:
        counts[row["status"]] += 1
    return counts


def _comparison(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def _employee_score_widget(user, audience, filters):
    rows, invalid = _snapshot_rows(user, audience, filters)
    latest = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    employee = latest["review"].employee if latest else (
        _scoped_employees(user, audience).first()
    )
    if employee is None:
        return {
            "kind": "empty",
            "message": "No employee performance record is available.",
        }
    today = timezone.localdate()
    assignments = list(
        EmployeeKPIRoleAssignment.objects.filter(
            employee=employee,
            is_active=True,
            is_archived=False,
            start_date__lte=today,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .select_related("kpi_template", "manager__employee_profile")
        .order_by("kpi_template__name")
    )
    manager_ids = {row.manager_id for row in assignments if row.manager_id}
    manager_name = (
        _user_name(assignments[0].manager)
        if len(manager_ids) == 1 and assignments
        else ("Multiple" if manager_ids else "Unassigned")
    )
    bonus = (
        KPIBonusCalculation.objects.filter(review=latest["review"])
        .only("eligibility_status", "result_snapshot")
        .first()
        if latest
        else None
    )
    entries = (
        latest["review"].approved_snapshot.get("entries", []) if latest else []
    )
    completed_entries = sum(
        1 for entry in entries if entry.get("actual_value") is not None
    )
    completion = (
        Decimal(completed_entries) * 100 / len(entries) if entries else None
    )
    team_score = None
    if bonus:
        team_score = _score(
            bonus.result_snapshot.get("components", {})
            .get("team", {})
            .get("score")
        )
    return {
        "kind": "employee-score",
        "employee": _employee_name(employee),
        "score": _display_score(latest["score"]) if latest else None,
        "status": latest["status"] if latest else "",
        "individual_completion": _display_score(completion),
        "team_completion": _display_score(team_score),
        "bonus_eligibility": (
            bonus.get_eligibility_status_display() if bonus else "Pending"
        ),
        "review_status": (
            latest["review"].get_status_display() if latest else "Not reviewed"
        ),
        "review_period": (
            latest["review"].get_period_type_display() if latest else "Not available"
        ),
        "manager": manager_name,
        "role_count": len(assignments),
        "goal_completion": _display_score(latest["score"]) if latest else None,
        "previous_change": _display_score(
            _comparison(
                latest["score"] if latest else None,
                previous["score"] if previous else None,
            )
        ),
        "invalid_snapshots": invalid,
    }


def _role_progress_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    latest = rows[0] if rows else None
    if not latest:
        return {"kind": "empty", "message": "No approved role results are available."}
    roles = []
    for role in latest["result"].get("roles", []):
        roles.append(
            {
                "name": role.get("template_name", "KPI role"),
                "score": _display_score(_score(role.get("score"))),
                "weighted_score": _display_score(
                    _score(role.get("weighted_score"))
                ),
                "weight": _display_score(_score(role.get("weight"))),
                "status": role.get("status", ""),
            }
        )
    return {"kind": "progress", "rows": roles}


def _improvement_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    if not rows:
        return {"kind": "empty", "message": "No approved KPI result is available."}
    result = rows[0]["result"]
    items = []
    for role in result.get("roles", []):
        template_result = role.get("template_result", {})
        for item in template_result.get("items", []):
            if item.get("status") in {"yellow", "red"}:
                items.append(
                    {
                        "name": item.get("name", "KPI item"),
                        "role": role.get("template_name", ""),
                        "score": _display_score(_score(item.get("score"))),
                        "status": item.get("status", ""),
                        "critical": bool(item.get("critical_red")),
                    }
                )
    items.sort(key=lambda item: Decimal(item["score"] or "101"))
    return {
        "kind": "risk-list",
        "rows": items[:8],
        "empty_message": "No Yellow or Red KPI items.",
    }


def _team_summary_widget(user, audience, filters):
    rows, invalid = _snapshot_rows(user, audience, filters)
    latest = _latest_rows(rows)
    counts = _status_counts(latest)
    average = _average(row["score"] for row in latest)
    review_ids = [row["review"].pk for row in latest]
    bonus_counts = defaultdict(int)
    for status in KPIBonusCalculation.objects.filter(
        review_id__in=review_ids
    ).values_list("eligibility_status", flat=True):
        bonus_counts[status] += 1
    return {
        "kind": "summary",
        "score": _display_score(average),
        "employee_count": len(latest),
        "green": counts["green"],
        "yellow": counts["yellow"],
        "red": counts["red"],
        "completion": _display_score(
            Decimal(len(latest)) * 100 / max(len(latest) + invalid, 1)
        ),
        "eligible": bonus_counts[KPIBonusCalculation.ELIGIBLE],
        "not_eligible": bonus_counts[KPIBonusCalculation.NOT_ELIGIBLE],
        "blocked": bonus_counts[KPIBonusCalculation.BLOCKED],
        "invalid_snapshots": invalid,
    }


def _executive_summary_widget(user, audience, filters):
    payload = _team_summary_widget(user, audience, filters)
    payload["kind"] = "executive-summary"
    return payload


def _trend_series(rows):
    monthly = defaultdict(list)
    quarterly = defaultdict(list)
    annual = defaultdict(list)
    for row in rows:
        review_date = row["review"].review_date
        monthly[f"{review_date:%b}"].append(row["score"])
        quarter = ((review_date.month - 1) // 3) + 1
        quarterly[f"Q{quarter}"].append(row["score"])
        annual[str(review_date.year)].append(row["score"])

    def serialize(grouped):
        return [
            {
                "label": label,
                "value": _display_score(_average(values)),
            }
            for label, values in grouped.items()
        ]

    return {
        "monthly": serialize(monthly),
        "quarterly": serialize(quarterly),
        "annual": serialize(annual),
    }


def _trend_widget(user, audience, filters):
    rows, invalid = _snapshot_rows(user, audience, filters)
    return {
        "kind": "trend",
        "series": _trend_series(rows),
        "invalid_snapshots": invalid,
    }


def _leaderboard_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    latest = sorted(
        _latest_rows(rows),
        key=lambda row: (row["score"], row["review"].employee_id),
        reverse=True,
    )

    def serialize(row, rank):
        return {
            "rank": rank,
            "employee": _employee_name(row["review"].employee),
            "employee_id": row["review"].employee_id,
            "score": _display_score(row["score"]),
            "status": row["status"],
            "department": row["review"].employee.department_name,
        }

    return {
        "kind": "leaderboard",
        "top": [serialize(row, index + 1) for index, row in enumerate(latest[:10])],
        "bottom": [
            serialize(row, len(latest) - index)
            for index, row in enumerate(reversed(latest[-10:]))
        ],
    }


def _queue_rows(user, audience, filters):
    queryset = _scoped_reviews(user, audience).filter(status__in=OPEN_STATUSES)
    queryset = apply_dashboard_filters(
        queryset,
        filters,
        include_kpi_status=False,
    )
    return list(queryset.order_by("period_end", "submitted_at", "pk")[:50])


def _review_queue_widget(user, audience, filters):
    today = timezone.localdate()
    upcoming_limit = today + timedelta(days=14)
    rows = _queue_rows(user, audience, filters)
    pending = [
        row
        for row in rows
        if row.status in {KPIReview.STATUS_SUBMITTED, KPIReview.STATUS_UNDER_REVIEW}
    ]
    late = [row for row in rows if row.period_end < today]
    upcoming = [
        row for row in rows if today <= row.period_end <= upcoming_limit
    ]
    employee_count = _scoped_employees(user, audience, as_of=today).count()
    completed_count = len(
        _latest_rows(_snapshot_rows(user, audience, filters)[0])
    )
    missing = max(employee_count - completed_count, 0)
    return {
        "kind": "review-queue",
        "pending": len(pending),
        "completed": completed_count,
        "missing": missing,
        "late": len(late),
        "upcoming": len(upcoming),
        "workload": len(rows),
        "rows": [
            {
                "id": review.pk,
                "employee": _employee_name(review.employee),
                "status": review.status,
                "status_label": review.get_status_display(),
                "period_end": review.period_end.isoformat(),
                "manager": _user_name(review.manager),
                "late": review.period_end < today,
            }
            for review in rows[:10]
        ],
    }


def _department_summary_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    grouped = defaultdict(list)
    for row in _latest_rows(rows):
        grouped[row["review"].employee.department_name or "Unassigned"].append(
            row["score"]
        )
    ranking = sorted(
        (
            {
                "name": name,
                "score": _display_score(_average(scores)),
                "employees": len(scores),
            }
            for name, scores in grouped.items()
        ),
        key=lambda row: Decimal(row["score"] or "0"),
        reverse=True,
    )
    return {"kind": "bar-chart", "rows": ranking}


def _location_summary_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    latest = _latest_rows(rows)
    employee_user_ids = [row["review"].employee.user_id for row in latest]
    ca_group = getattr(settings, "CA_TEAM_GROUP", "CA_TEAM")
    bd_group = getattr(settings, "BD_TEAM_GROUP", "BD_TEAM")
    membership = defaultdict(set)
    for user_id, group_name in (
        EmployeeProfile.objects.filter(user_id__in=employee_user_ids)
        .values_list("user_id", "user__groups__name")
    ):
        if group_name == ca_group:
            membership["Canada"].add(user_id)
        elif group_name == bd_group:
            membership["Bangladesh"].add(user_id)
    payload = []
    for label in ("Canada", "Bangladesh"):
        scores = [
            row["score"]
            for row in latest
            if row["review"].employee.user_id in membership[label]
        ]
        payload.append(
            {
                "name": label,
                "score": _display_score(_average(scores)),
                "employees": len(scores),
            }
        )
    return {"kind": "location-summary", "rows": payload}


def _risk_widget(user, audience, filters):
    rows, invalid = _snapshot_rows(user, audience, filters)
    risks = []
    for row in _latest_rows(rows):
        if row["critical_red"] or row["status"] == "red":
            risks.append(
                {
                    "review_id": row["review"].pk,
                    "employee": _employee_name(row["review"].employee),
                    "score": _display_score(row["score"]),
                    "status": row["status"],
                    "critical": row["critical_red"],
                    "department": row["review"].employee.department_name,
                }
            )
    risks.sort(key=lambda item: Decimal(item["score"] or "101"))
    return {
        "kind": "risk-list",
        "rows": risks[:15],
        "invalid_snapshots": invalid,
        "empty_message": "No Red or Critical Red results.",
    }


def _bonus_rows(review_ids):
    return KPIBonusCalculation.objects.filter(review_id__in=review_ids).only(
        "eligibility_status",
        "result_snapshot",
        "employee_id",
        "review_id",
    )


def _bonus_summary_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    latest = _latest_rows(rows)
    counts = defaultdict(int)
    for bonus in _bonus_rows([row["review"].pk for row in latest]):
        counts[bonus.eligibility_status] += 1
    total = len(latest)
    return {
        "kind": "bonus-summary",
        "eligible": counts[KPIBonusCalculation.ELIGIBLE],
        "not_eligible": counts[KPIBonusCalculation.NOT_ELIGIBLE],
        "blocked": counts[KPIBonusCalculation.BLOCKED],
        "pending": max(len(latest) - sum(counts.values()), 0),
        "eligible_percent": _display_score(
            Decimal(counts[KPIBonusCalculation.ELIGIBLE]) * 100 / total
            if total
            else Decimal("0")
        ),
    }


def _bonus_forecast_widget(user, audience, filters):
    if audience != AUDIENCE_EXECUTIVE:
        raise KPIDashboardPermissionError("Bonus forecast is executive only.")
    rows, _invalid = _snapshot_rows(user, audience, filters)
    latest = _latest_rows(rows)
    total = Decimal("0")
    eligible = 0
    multipliers = []
    currencies = defaultdict(Decimal)
    bonuses = list(_bonus_rows([row["review"].pk for row in latest]))
    for bonus in bonuses:
        payout = bonus.result_snapshot.get("payout", {})
        amount = _decimal(payout.get("final_amount"))
        currency = _text(payout.get("currency"), 3).upper()
        if amount is not None and currency:
            currencies[currency] += amount
            total += amount
        multiplier = _decimal(bonus.result_snapshot.get("attendance_multiplier"))
        if multiplier is not None:
            multipliers.append(multiplier)
        if bonus.eligibility_status == KPIBonusCalculation.ELIGIBLE:
            eligible += 1
    return {
        "kind": "bonus-forecast",
        "eligible": eligible,
        "calculated": len(currencies) > 0,
        "currency_totals": [
            {"currency": currency, "amount": f"{amount:.2f}"}
            for currency, amount in sorted(currencies.items())
        ],
        "attendance_impact": _display_score(
            _average(multipliers) * 100 if multipliers else None
        ),
        "calculation_count": len(bonuses),
    }


HEALTH_CATEGORIES = (
    ("Factory", {"production"}, ("factory",)),
    ("Sales", {"sales"}, ("sales",)),
    ("Production", {"production"}, ("production",)),
    ("Sampling", set(), ("sample", "sampling")),
    ("Marketing", {"marketing"}, ("marketing",)),
    ("Quality", {"quality_control"}, ("quality", "qc")),
    ("Financial KPI Readiness", {"accounts"}, ("accounts", "finance")),
)


def _health_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    latest = _latest_rows(rows)
    health = []
    for label, departments, role_tokens in HEALTH_CATEGORIES:
        scores = []
        statuses = []
        for row in latest:
            employee = row["review"].employee
            department = (
                employee.department_ref.code
                if employee.department_ref_id
                else employee.department
            )
            role_names = " ".join(
                role.get("template_name", "").casefold()
                for role in row["result"].get("roles", [])
            )
            if department in departments or any(
                token in role_names for token in role_tokens
            ):
                scores.append(row["score"])
                statuses.append(row["status"])
        score = _average(scores)
        status = (
            "red"
            if "red" in statuses
            else "yellow"
            if "yellow" in statuses
            else "green"
            if statuses
            else ""
        )
        health.append(
            {
                "name": label,
                "score": _display_score(score),
                "employees": len(scores),
                "status": status,
            }
        )
    return {"kind": "health", "rows": health}


def _manager_summary_widget(user, audience, filters):
    rows, _invalid = _snapshot_rows(user, audience, filters)
    grouped = defaultdict(list)
    for row in _latest_rows(rows):
        grouped[row["review"].manager].append(row["score"])
    payload = []
    for manager, scores in grouped.items():
        payload.append(
            {
                "name": _user_name(manager),
                "score": _display_score(_average(scores)),
                "reviews": len(scores),
            }
        )
    payload.sort(key=lambda item: Decimal(item["score"] or "0"), reverse=True)
    return {"kind": "manager-summary", "rows": payload}


WIDGET_BUILDERS = {
    "employee-score": _employee_score_widget,
    "role-progress": _role_progress_widget,
    "improvement": _improvement_widget,
    "team-summary": _team_summary_widget,
    "executive-summary": _executive_summary_widget,
    "trend": _trend_widget,
    "leaderboard": _leaderboard_widget,
    "review-queue": _review_queue_widget,
    "department-summary": _department_summary_widget,
    "location-summary": _location_summary_widget,
    "risk": _risk_widget,
    "bonus-summary": _bonus_summary_widget,
    "bonus-forecast": _bonus_forecast_widget,
    "health": _health_widget,
    "manager-summary": _manager_summary_widget,
}


def dashboard_widget_payload(user, slug, filters, *, use_cache=True):
    audience = dashboard_audience(user)
    allowed = {widget.slug for widget in WIDGETS[audience]}
    if slug not in allowed or slug not in WIDGET_BUILDERS:
        raise KPIDashboardPermissionError("This KPI widget is not available.")
    cache_key = (
        f"{DASHBOARD_CACHE_VERSION}:{user.pk}:{audience}:"
        f"{slug}:{filters.cache_token()}"
    )
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    payload = WIDGET_BUILDERS[slug](user, audience, filters)
    payload.update(
        {
            "slug": slug,
            "audience": audience,
            "generated_at": timezone.now().isoformat(),
            "snapshot_only": True,
        }
    )
    if use_cache:
        cache.set(cache_key, payload, DASHBOARD_CACHE_SECONDS)
    return payload
