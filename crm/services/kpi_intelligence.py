import hashlib
import json
from calendar import monthrange
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from crm.models import CRMAuditLog
from crm.models_kpi_bonus import KPIBonusCalculation
from crm.models_kpi_intelligence import KPIIntelligenceRuleSet
from crm.models_kpi_reviews import KPIReview
from crm.services.costing_currency import format_finance_money
from crm.services.kpi_bonus import verify_bonus_calculation
from crm.services.kpi_dashboard import (
    APPROVED_STATUSES,
    AUDIENCE_DIRECTOR,
    AUDIENCE_EMPLOYEE,
    AUDIENCE_EXECUTIVE,
    AUDIENCE_HR,
    AUDIENCE_MANAGER,
    DashboardFilters,
    approved_dashboard_snapshot_rows,
    apply_dashboard_filters,
    dashboard_audience,
    scoped_dashboard_employees,
    scoped_dashboard_reviews,
)


INTELLIGENCE_VERSION = "kpi-intelligence/1.0"
INTELLIGENCE_CACHE_VERSION = "kpi-intelligence-cache/1.0"
INTELLIGENCE_CACHE_SECONDS = 60
INTELLIGENCE_RULE_GENERATION_KEY = "kpi-intelligence-rule-generation"
ACTION_SALT = "crm.kpi-intelligence.action.v1"
SCORE_QUANTUM = Decimal("0.01")
PERCENT = Decimal("100")
PAGE_SIZE = 12


class KPIIntelligenceError(Exception):
    pass


class KPIIntelligencePermissionError(KPIIntelligenceError):
    pass


@dataclass(frozen=True, slots=True)
class IntelligenceWidget:
    slug: str
    title: str
    size: str = "full"


@dataclass(frozen=True, slots=True)
class IntelligenceInsight:
    insight_type: str
    status: str
    severity: str
    title: str
    summary: str
    supporting_value: str = ""
    previous_value: str = ""
    change_percentage: str = ""
    employee: str = ""
    manager: str = ""
    department: str = ""
    location: str = ""
    review_period: str = ""
    source_record: str = ""
    recommended_action: str = ""
    action_link: str = ""
    generated_date: str = ""
    intelligence_version: str = INTELLIGENCE_VERSION

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrendAnalytics:
    current_result: Decimal | None
    previous_result: Decimal | None
    difference: Decimal | None
    percentage_change: Decimal | None
    direction: str
    status: str
    periods_included: int
    data_quality_warning: str

    def as_dict(self):
        return {
            "current_result": _score_text(self.current_result),
            "previous_result": _score_text(self.previous_result),
            "difference": _signed_text(self.difference),
            "percentage_change": _signed_text(self.percentage_change),
            "direction": self.direction,
            "status": self.status,
            "periods_included": self.periods_included,
            "data_quality_warning": self.data_quality_warning,
        }


INTELLIGENCE_WIDGETS = {
    AUDIENCE_EMPLOYEE: (
        IntelligenceWidget("employee-analytics", "My Performance Intelligence"),
        IntelligenceWidget("trends", "My Trends"),
        IntelligenceWidget("yellow-attention", "What Needs Attention"),
        IntelligenceWidget("green-success", "Positive Results"),
        IntelligenceWidget("data-quality", "Data Quality"),
    ),
    AUDIENCE_MANAGER: (
        IntelligenceWidget("company-health", "Team Health"),
        IntelligenceWidget("critical-alerts", "Critical Red Alerts"),
        IntelligenceWidget("yellow-attention", "Yellow Attention"),
        IntelligenceWidget("green-success", "Green Results"),
        IntelligenceWidget("manager-analytics", "Manager Workload"),
        IntelligenceWidget("employee-analytics", "Employee Performance"),
        IntelligenceWidget("bonus-readiness", "Bonus Readiness"),
        IntelligenceWidget("review-completion", "Review Completion"),
        IntelligenceWidget("trends", "Team Trends"),
        IntelligenceWidget("recommended-actions", "Recommended Actions"),
        IntelligenceWidget("data-quality", "Data Quality"),
    ),
    AUDIENCE_DIRECTOR: (
        IntelligenceWidget("company-health", "Department Health"),
        IntelligenceWidget("critical-alerts", "Critical Red Alerts"),
        IntelligenceWidget("yellow-attention", "Yellow Attention"),
        IntelligenceWidget("green-success", "Green Results"),
        IntelligenceWidget("department-analytics", "Department Performance"),
        IntelligenceWidget("manager-analytics", "Manager Performance"),
        IntelligenceWidget("employee-analytics", "Employee Performance"),
        IntelligenceWidget("bonus-readiness", "Bonus Readiness"),
        IntelligenceWidget("review-completion", "Review Completion"),
        IntelligenceWidget("trends", "Department Trends"),
        IntelligenceWidget("recommended-actions", "Recommended Actions"),
        IntelligenceWidget("data-quality", "Data Quality"),
    ),
    AUDIENCE_HR: (
        IntelligenceWidget("company-health", "Workforce KPI Health"),
        IntelligenceWidget("critical-alerts", "Critical Red Alerts"),
        IntelligenceWidget("yellow-attention", "Yellow Attention"),
        IntelligenceWidget("green-success", "Green Results"),
        IntelligenceWidget("department-analytics", "Department Performance"),
        IntelligenceWidget("manager-analytics", "Manager Performance"),
        IntelligenceWidget("employee-analytics", "Employee Performance"),
        IntelligenceWidget("bonus-readiness", "Bonus Readiness"),
        IntelligenceWidget("review-completion", "Review Completion"),
        IntelligenceWidget("trends", "Workforce Trends"),
        IntelligenceWidget("recommended-actions", "Recommended Actions"),
        IntelligenceWidget("data-quality", "Data Quality"),
    ),
    AUDIENCE_EXECUTIVE: (
        IntelligenceWidget("company-health", "Company Health"),
        IntelligenceWidget("critical-alerts", "Critical Red Alerts"),
        IntelligenceWidget("yellow-attention", "Yellow Attention"),
        IntelligenceWidget("green-success", "Green Results"),
        IntelligenceWidget("department-analytics", "Department Performance"),
        IntelligenceWidget("manager-analytics", "Manager Performance"),
        IntelligenceWidget("employee-analytics", "Employee Performance"),
        IntelligenceWidget("bonus-readiness", "Bonus Readiness"),
        IntelligenceWidget("review-completion", "Review Completion"),
        IntelligenceWidget("trends", "Company Trends"),
        IntelligenceWidget("location-analytics", "Canada and Bangladesh"),
        IntelligenceWidget("recommended-actions", "Executive Action Center"),
        IntelligenceWidget("data-quality", "Data Quality"),
    ),
}


REPORT_TYPES = {
    "executive-summary": "Executive KPI Summary",
    "department-performance": "Department Performance",
    "manager-performance": "Manager Performance",
    "employee-performance": "Employee Performance",
    "review-completion": "Review Completion",
    "critical-red": "Critical Red Report",
    "bonus-readiness": "Bonus Readiness",
    "monthly-comparison": "Monthly Comparison",
    "quarterly-comparison": "Quarterly Comparison",
    "annual-comparison": "Annual Comparison",
}


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _score_text(value):
    if value is None:
        return ""
    return str(value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP))


def _signed_text(value):
    if value is None:
        return ""
    rounded = value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
    return f"{rounded:+}"


def _average(values):
    values = tuple(value for value in values if value is not None)
    if not values:
        return None
    return sum(values, Decimal("0")) / len(values)


def _employee_name(employee):
    return (
        employee.public_name
        or employee.user.get_full_name()
        or employee.user.get_username()
    )


def _user_name(user):
    if user is None:
        return "Unassigned"
    profile = getattr(user, "employee_profile", None)
    if profile and profile.public_name:
        return profile.public_name
    return user.get_full_name() or user.get_username()


def _department_details(employee):
    if employee.department_ref_id:
        return employee.department_ref.code, employee.department_ref.name
    return employee.department or "", employee.department_name or "Unassigned"


def _clean_text(value, limit=500):
    return " ".join(strip_tags(str(value or "")).split())[:limit]


def _percent(part, whole):
    if not whole:
        return None
    return Decimal(part) * PERCENT / Decimal(whole)


def _latest_and_previous(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["review"].employee_id].append(row)
    latest = {}
    previous = {}
    for employee_id, employee_rows in grouped.items():
        latest[employee_id] = employee_rows[0]
        if len(employee_rows) > 1:
            previous[employee_id] = employee_rows[1]
    return latest, previous


def _status_counts(rows):
    counts = {"green": 0, "yellow": 0, "red": 0}
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] += 1
    return counts


def _status_for_score(score, rules):
    if score is None:
        return ""
    if score >= rules.health_green_threshold:
        return "green"
    if score >= rules.minimum_score:
        return "yellow"
    return "red"


def effective_intelligence_rule_set(as_of, *, code=None, required=True):
    queryset = KPIIntelligenceRuleSet.objects.filter(
        status=KPIIntelligenceRuleSet.STATUS_PUBLISHED,
        effective_start__lte=as_of,
    ).filter(
        Q(effective_end__isnull=True) | Q(effective_end__gte=as_of)
    )
    if code:
        queryset = queryset.filter(code=code)
    rows = list(queryset.order_by("code", "-version")[:2])
    if not rows and not required:
        return None
    if len(rows) != 1:
        raise KPIIntelligenceError(
            "Exactly one effective published intelligence rule set is required; "
            f"found {len(rows)}."
        )
    return rows[0]


def record_intelligence_event(
    *,
    actor,
    event,
    record_id="center",
    record_label="KPI Intelligence",
    target_url="",
):
    return CRMAuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        module="kpi_intelligence",
        record_id=str(record_id)[:64],
        record_label=_clean_text(record_label, 220),
        action_type=CRMAuditLog.ACTION_UPDATED,
        field_name=_clean_text(event, 100),
        previous_value="",
        new_value=_clean_text(event, 200),
        target_url=str(target_url or "")[:300],
    )


def record_intelligence_events(*, actor, events):
    rows = [
        CRMAuditLog(
            actor=actor if actor and actor.is_authenticated else None,
            module="kpi_intelligence",
            record_id=str(event.get("record_id", "center"))[:64],
            record_label=_clean_text(
                event.get("record_label", "KPI Intelligence"),
                220,
            ),
            action_type=CRMAuditLog.ACTION_UPDATED,
            field_name=_clean_text(event["event"], 100),
            previous_value="",
            new_value=_clean_text(event["event"], 200),
            target_url=str(event.get("target_url", ""))[:300],
        )
        for event in events
    ]
    if rows:
        CRMAuditLog.objects.bulk_create(rows)
    return rows


@transaction.atomic
def publish_intelligence_rule_set(rule_set, *, actor):
    if not actor or not actor.is_authenticated:
        raise KPIIntelligencePermissionError("An authenticated publisher is required.")
    if dashboard_audience(actor) != AUDIENCE_EXECUTIVE:
        raise KPIIntelligencePermissionError(
            "Only CEO or Super Admin may publish intelligence rules."
        )
    locked = KPIIntelligenceRuleSet.objects.select_for_update().get(pk=rule_set.pk)
    if locked.status != KPIIntelligenceRuleSet.STATUS_DRAFT:
        raise KPIIntelligenceError("Only Draft intelligence rules can be published.")
    locked.status = KPIIntelligenceRuleSet.STATUS_PUBLISHED
    locked.published_by = actor
    locked.published_at = timezone.now()
    locked.save(
        update_fields=("status", "published_by", "published_at", "updated_at")
    )
    record_intelligence_event(
        actor=actor,
        event="intelligence_rule_changed",
        record_id=locked.pk,
        record_label=f"{locked.name} v{locked.version}",
    )
    cache.set(
        INTELLIGENCE_RULE_GENERATION_KEY,
        timezone.now().isoformat(),
        timeout=None,
    )
    return locked


def intelligence_widgets(user):
    return INTELLIGENCE_WIDGETS[dashboard_audience(user)]


def allowed_report_types(user):
    audience = dashboard_audience(user)
    if audience == AUDIENCE_EMPLOYEE:
        allowed = {"employee-performance"}
    elif audience == AUDIENCE_MANAGER:
        allowed = {
            "manager-performance",
            "employee-performance",
            "review-completion",
            "critical-red",
            "monthly-comparison",
            "quarterly-comparison",
            "annual-comparison",
        }
    elif audience in {AUDIENCE_DIRECTOR, AUDIENCE_HR}:
        allowed = set(REPORT_TYPES) - {"executive-summary"}
    else:
        allowed = set(REPORT_TYPES)
    return tuple(
        {"code": code, "name": name}
        for code, name in REPORT_TYPES.items()
        if code in allowed
    )


def _rows(user, audience, filters):
    return approved_dashboard_snapshot_rows(
        user,
        filters,
        audience=audience,
    )


def _history_rows(user, audience, filters, periods):
    rows = []
    invalid = 0
    start_year = max(filters.year - periods + 1, 2000)
    for year in range(start_year, filters.year + 1):
        year_filters = replace(filters, year=year)
        year_rows, year_invalid = _rows(user, audience, year_filters)
        rows.extend(year_rows)
        invalid += year_invalid
    rows.sort(
        key=lambda row: (
            row["review"].review_date,
            row["review"].period_end,
            row["review"].pk,
        ),
        reverse=True,
    )
    return rows, invalid


def _bonus_rows(review_ids):
    rows = []
    invalid = 0
    calculations = KPIBonusCalculation.objects.filter(
        review_id__in=review_ids
    ).select_related("rule_set", "employee", "manager")
    for calculation in calculations:
        if not verify_bonus_calculation(calculation):
            invalid += 1
            continue
        rows.append(calculation)
    return rows, invalid


def _trend(points, rules):
    if len(points) < rules.trend_periods:
        return TrendAnalytics(
            current_result=points[-1][1] if points else None,
            previous_result=None,
            difference=None,
            percentage_change=None,
            direction="insufficient",
            status="",
            periods_included=len(points),
            data_quality_warning="Insufficient History",
        )
    current = points[-1][1]
    previous = points[-2][1]
    difference = current - previous
    change = (
        difference * PERCENT / abs(previous)
        if previous != 0
        else None
    )
    if difference >= rules.improvement_threshold:
        direction = "improving"
        status = "green"
    elif difference <= -rules.decline_percentage:
        direction = "falling"
        status = "red"
    else:
        direction = "stable" if difference == 0 else "watch"
        status = "yellow"
    return TrendAnalytics(
        current_result=current,
        previous_result=previous,
        difference=difference,
        percentage_change=change,
        direction=direction,
        status=status,
        periods_included=len(points),
        data_quality_warning="",
    )


def _trend_points(rows, period):
    grouped = defaultdict(list)
    for row in rows:
        review_date = row["review"].review_date
        if period == "monthly":
            key = (review_date.year, review_date.month)
            label = f"{review_date:%b %Y}"
        elif period == "quarterly":
            quarter = ((review_date.month - 1) // 3) + 1
            key = (review_date.year, quarter)
            label = f"Q{quarter} {review_date.year}"
        else:
            key = (review_date.year,)
            label = str(review_date.year)
        grouped[(key, label)].append(row["score"])
    points = [
        (label, _average(values))
        for (key, label), values in sorted(grouped.items(), key=lambda item: item[0][0])
    ]
    return [(label, score) for label, score in points if score is not None]


def _rule_date(filters):
    if filters.month:
        return date(
            filters.year,
            filters.month,
            monthrange(filters.year, filters.month)[1],
        )
    if filters.quarter:
        month = filters.quarter * 3
        return date(filters.year, month, monthrange(filters.year, month)[1])
    return date(filters.year, 12, 31)


def _signed_action(kind, object_id=None, **params):
    payload = {"kind": kind}
    if object_id is not None:
        payload["id"] = int(object_id)
    if params:
        payload["params"] = params
    token = signing.dumps(payload, salt=ACTION_SALT, compress=True)
    return reverse("kpi_intelligence_action", args=[token])


def resolve_intelligence_action(user, token):
    try:
        payload = signing.loads(token, salt=ACTION_SALT, max_age=60 * 60 * 24 * 30)
    except signing.BadSignature as exc:
        raise KPIIntelligencePermissionError("This action link is invalid.") from exc
    audience = dashboard_audience(user)
    kind = payload.get("kind")
    object_id = payload.get("id")
    params = payload.get("params") or {}

    if kind == "review":
        review = scoped_dashboard_reviews(user, audience).filter(pk=object_id).first()
        if review is None:
            raise KPIIntelligencePermissionError("This review is not available.")
        target = reverse("kpi_review_detail", args=[review.pk])
        label = f"Review {review.pk}"
    elif kind == "employee":
        employee = scoped_dashboard_employees(user, audience).filter(pk=object_id).first()
        if employee is None:
            raise KPIIntelligencePermissionError("This employee is not available.")
        target = reverse("employee_performance", args=[employee.user_id])
        label = _employee_name(employee)
    elif kind == "queue":
        if audience == AUDIENCE_EMPLOYEE:
            raise KPIIntelligencePermissionError("Review queue access is not available.")
        target = reverse("kpi_review_list")
        label = "KPI review queue"
    elif kind == "dashboard":
        safe = {}
        if params.get("department"):
            allowed_codes = {
                _department_details(employee)[0]
                for employee in scoped_dashboard_employees(user, audience)
            }
            if params["department"] not in allowed_codes:
                raise KPIIntelligencePermissionError(
                    "This department is not available."
                )
            safe["department"] = params["department"]
        if params.get("manager"):
            manager_id = int(params["manager"])
            if not scoped_dashboard_reviews(user, audience).filter(
                manager_id=manager_id
            ).exists():
                raise KPIIntelligencePermissionError(
                    "This manager is not available."
                )
            safe["manager"] = manager_id
        target = reverse("kpi_dashboard")
        if safe:
            target = f"{target}?{urlencode(safe)}"
        label = "KPI dashboard"
    else:
        raise KPIIntelligencePermissionError("This action is not available.")

    record_intelligence_event(
        actor=user,
        event="critical_action_link_used",
        record_id=f"{kind}:{object_id or 'scope'}",
        record_label=label,
        target_url=target,
    )
    return target


def _critical_reason(row):
    reasons = [
        _clean_text(entry.get("critical_reason"))
        for entry in row["review"].approved_snapshot.get("entries", [])
        if entry.get("critical_red") and entry.get("critical_reason")
    ]
    return reasons[0] if reasons else "Approved review has a Critical Red result."


def _company_health(user, audience, filters, rules):
    rows, invalid = _rows(user, audience, filters)
    latest, previous = _latest_and_previous(rows)
    latest_rows = tuple(latest.values())
    visible_employees = list(scoped_dashboard_employees(user, audience))
    review_ids = [row["review"].pk for row in latest_rows]
    bonuses, invalid_bonus = _bonus_rows(review_ids)

    overall = _average(row["score"] for row in latest_rows)
    completion = _percent(len(latest_rows), len(visible_employees))
    ready_bonuses = sum(
        1
        for bonus in bonuses
        if bonus.eligibility_status == KPIBonusCalculation.ELIGIBLE
    )
    bonus_readiness = _percent(ready_bonuses, len(bonuses)) if bonuses else None
    changes = [
        row["score"] - previous[employee_id]["score"]
        for employee_id, row in latest.items()
        if employee_id in previous
    ]
    average_change = _average(changes)
    if average_change is None:
        improvement = None
    elif average_change >= rules.improvement_threshold:
        improvement = PERCENT
    elif average_change <= -rules.decline_percentage:
        improvement = Decimal("0")
    else:
        span = rules.improvement_threshold + rules.decline_percentage
        improvement = (
            (average_change + rules.decline_percentage) * PERCENT / span
            if span
            else Decimal("50")
        )
    at_risk = sum(
        1
        for row in latest_rows
        if row["status"] == "red" or row["critical_red"]
    )
    risk_control = (
        PERCENT - (_percent(at_risk, len(latest_rows)) or Decimal("0"))
        if latest_rows
        else None
    )

    components = (
        ("Overall KPI", overall, rules.overall_kpi_weight),
        ("Review completion", completion, rules.review_completion_weight),
        ("Bonus readiness", bonus_readiness, rules.bonus_readiness_weight),
        ("Improvement", improvement, rules.improvement_weight),
        ("Risk control", risk_control, rules.risk_control_weight),
    )
    available = [component for component in components if component[1] is not None]
    available_weight = sum(
        (component[2] for component in available),
        Decimal("0"),
    )
    health_score = (
        sum(
            (component[1] * component[2] for component in available),
            Decimal("0"),
        )
        / available_weight
        if available_weight
        else None
    )
    counts = _status_counts(latest_rows)
    warnings = []
    if invalid:
        warnings.append(f"{invalid} approved snapshot(s) failed verification.")
    if invalid_bonus:
        warnings.append(f"{invalid_bonus} bonus snapshot(s) failed verification.")
    if not visible_employees:
        warnings.append("No visible active employees.")
    if len(latest_rows) < len(visible_employees):
        warnings.append("Some employees have no approved review in this period.")
    if not bonuses:
        warnings.append("Bonus readiness is unavailable.")
    if average_change is None:
        warnings.append("Insufficient History")
    return {
        "kind": "company-health",
        "score": _score_text(health_score),
        "status": _status_for_score(health_score, rules),
        "overall_kpi": _score_text(overall),
        "department_average": _score_text(overall),
        "review_completion": _score_text(completion),
        "critical_red": sum(1 for row in latest_rows if row["critical_red"]),
        "green": counts["green"],
        "yellow": counts["yellow"],
        "red": counts["red"],
        "bonus_readiness": _score_text(bonus_readiness),
        "manager_review_completion": _score_text(completion),
        "employee_improvement": _signed_text(average_change),
        "department_improvement": _signed_text(average_change),
        "components": [
            {
                "name": name,
                "value": _score_text(value),
                "weight": _score_text(weight),
                "available": value is not None,
            }
            for name, value, weight in components
        ],
        "warnings": warnings,
        "rule_version": rules.version,
    }


def _department_groups(rows):
    grouped = defaultdict(list)
    for row in rows:
        code, name = _department_details(row["review"].employee)
        grouped[(code, name)].append(row)
    return grouped


def _item_extreme(rows, *, highest):
    items = []
    for row in rows:
        for role in row["result"].get("roles", []):
            for item in role.get("template_result", {}).get("items", []):
                score = _decimal(item.get("score"))
                if score is not None:
                    items.append((score, _clean_text(item.get("name"), 120)))
    if not items:
        return ""
    return (max(items) if highest else min(items))[1]


def _department_analytics(user, audience, filters, rules):
    rows, _invalid = _rows(user, audience, filters)
    latest, previous = _latest_and_previous(rows)
    grouped = _department_groups(tuple(latest.values()))
    employee_counts = defaultdict(int)
    for employee in scoped_dashboard_employees(user, audience):
        code, _name = _department_details(employee)
        employee_counts[code] += 1
    payload = []
    for (code, name), department_rows in grouped.items():
        current = _average(row["score"] for row in department_rows)
        prior_values = [
            previous[row["review"].employee_id]["score"]
            for row in department_rows
            if row["review"].employee_id in previous
        ]
        prior = _average(prior_values)
        points = []
        if prior is not None:
            points.append(("Previous", prior))
        if current is not None:
            points.append(("Current", current))
        trend = _trend(points, rules)
        counts = _status_counts(department_rows)
        payload.append(
            {
                "code": code,
                "name": name,
                "current_score": _score_text(current),
                "previous_score": _score_text(prior),
                "trend": trend.as_dict(),
                "green": counts["green"],
                "yellow": counts["yellow"],
                "red": counts["red"],
                "review_completion": _score_text(
                    _percent(len(department_rows), employee_counts[code])
                ),
                "top_strength": _item_extreme(department_rows, highest=True),
                "main_risk": _item_extreme(department_rows, highest=False),
                "recommended_action": (
                    "Review department risks and agree corrective ownership."
                    if current is not None
                    and current < rules.department_risk_threshold
                    else "Protect the strongest KPI and monitor the next review."
                ),
                "action_link": _signed_action(
                    "dashboard",
                    department=code,
                ),
            }
        )
    payload.sort(
        key=lambda row: Decimal(row["current_score"] or "0"),
        reverse=True,
    )
    return {"kind": "department-analytics", "rows": payload}


def _manager_analytics(user, audience, filters, rules):
    rows, _invalid = _rows(user, audience, filters)
    latest, previous = _latest_and_previous(rows)
    grouped = defaultdict(list)
    for row in latest.values():
        grouped[row["review"].manager].append(row)
    open_reviews = list(
        apply_dashboard_filters(
            scoped_dashboard_reviews(user, audience).exclude(
                status__in=APPROVED_STATUSES
            ),
            filters,
            include_kpi_status=False,
        )
    )
    open_by_manager = defaultdict(list)
    for review in open_reviews:
        open_by_manager[review.manager_id].append(review)
    all_bonuses, _invalid_bonus = _bonus_rows(
        [row["review"].pk for row in latest.values()]
    )
    bonuses_by_review = {
        bonus.review_id: bonus
        for bonus in all_bonuses
    }
    today = timezone.localdate()
    payload = []
    for manager, manager_rows in grouped.items():
        current = _average(row["score"] for row in manager_rows)
        prior = _average(
            previous[row["review"].employee_id]["score"]
            for row in manager_rows
            if row["review"].employee_id in previous
        )
        manager_id = manager.pk if manager else None
        queue = open_by_manager[manager_id]
        counts = _status_counts(manager_rows)
        bonuses = [
            bonuses_by_review[row["review"].pk]
            for row in manager_rows
            if row["review"].pk in bonuses_by_review
        ]
        payload.append(
            {
                "manager_id": manager_id,
                "manager": _user_name(manager),
                "team_average": _score_text(current),
                "review_completion": _score_text(
                    _percent(len(manager_rows), len(manager_rows) + len(queue))
                ),
                "overdue_reviews": sum(
                    1
                    for review in queue
                    if review.period_end
                    < today - timedelta(days=rules.overdue_days)
                ),
                "green": counts["green"],
                "yellow": counts["yellow"],
                "red": counts["red"],
                "team_improvement": _signed_text(
                    current - prior
                    if current is not None and prior is not None
                    else None
                ),
                "repeated_missed_items": sum(
                    1
                    for row in manager_rows
                    for role in row["result"].get("roles", [])
                    for item in role.get("template_result", {}).get("items", [])
                    if item.get("status") in {"yellow", "red"}
                ),
                "bonus_ready": sum(
                    1
                    for bonus in bonuses
                    if bonus.eligibility_status == KPIBonusCalculation.ELIGIBLE
                ),
                "workload": len(queue),
                "workload_status": (
                    "red"
                    if len(queue) > rules.manager_workload_threshold
                    else "yellow"
                    if queue
                    else "green"
                ),
                "action_link": (
                    _signed_action("dashboard", manager=manager_id)
                    if manager_id
                    else ""
                ),
            }
        )
    payload.sort(key=lambda row: row["manager"].casefold())
    return {"kind": "manager-analytics", "rows": payload}


def _employee_analytics(user, audience, filters, rules, page):
    rows, _invalid = _rows(user, audience, filters)
    latest, previous = _latest_and_previous(rows)
    payload = []
    for employee_id, row in latest.items():
        prior = previous.get(employee_id)
        role_items = [
            item
            for role in row["result"].get("roles", [])
            for item in role.get("template_result", {}).get("items", [])
            if _decimal(item.get("score")) is not None
        ]
        strongest = (
            max(role_items, key=lambda item: _decimal(item.get("score")))
            if role_items
            else {}
        )
        weakest = (
            min(role_items, key=lambda item: _decimal(item.get("score")))
            if role_items
            else {}
        )
        comments = row["review"].approved_snapshot.get("comments", {})
        payload.append(
            {
                "employee_id": employee_id,
                "employee": _employee_name(row["review"].employee),
                "current_score": _score_text(row["score"]),
                "previous_score": _score_text(prior["score"] if prior else None),
                "change": _signed_text(
                    row["score"] - prior["score"] if prior else None
                ),
                "status": row["status"],
                "strongest_kpi": _clean_text(strongest.get("name"), 120),
                "improvement_kpi": _clean_text(weakest.get("name"), 120),
                "review_status": row["review"].get_status_display(),
                "review_date": row["review"].review_date.isoformat(),
                "approved_comments": tuple(
                    value
                    for value in (
                        _clean_text(comments.get("manager"), 300),
                        _clean_text(comments.get("approval"), 300),
                    )
                    if value
                ),
                "suggested_action": (
                    "Agree a corrective action for the lowest KPI."
                    if row["status"] in {"red", "yellow"}
                    else "Maintain the strongest KPI and prepare the next goal."
                ),
                "action_link": _signed_action("employee", employee_id),
            }
        )
    payload.sort(key=lambda item: item["employee"].casefold())
    return {
        "kind": "employee-analytics",
        **_paginate(payload, page),
        "self_only": audience == AUDIENCE_EMPLOYEE,
    }


def _bonus_readiness(user, audience, filters, _rules, page):
    rows, _invalid = _rows(user, audience, filters)
    latest, _previous = _latest_and_previous(rows)
    review_ids = [row["review"].pk for row in latest.values()]
    bonuses, invalid = _bonus_rows(review_ids)
    by_review = {bonus.review_id: bonus for bonus in bonuses}
    counts = {
        "eligible": 0,
        "not_eligible": 0,
        "blocked": 0,
        "pending": 0,
    }
    allow_amounts = audience == AUDIENCE_EXECUTIVE
    payload = []
    for row in latest.values():
        bonus = by_review.get(row["review"].pk)
        if bonus is None:
            counts["pending"] += 1
            payload.append(
                {
                    "employee": _employee_name(row["review"].employee),
                    "status": "pending",
                    "reason": "No immutable bonus calculation is available.",
                    "critical_red": row["critical_red"],
                    "rule_version": "",
                    "calculation_date": "",
                    "amount": "",
                    "action_link": _signed_action("review", row["review"].pk),
                }
            )
            continue
        counts[bonus.eligibility_status] += 1
        payout = bonus.result_snapshot.get("payout", {})
        amount = (
            format_finance_money(
                payout.get("final_amount"),
                payout.get("currency"),
            )
            if allow_amounts and payout.get("final_amount") is not None
            else ""
        )
        payload.append(
            {
                "employee": _employee_name(row["review"].employee),
                "status": bonus.eligibility_status,
                "reason": ", ".join(
                    _clean_text(reason, 100) for reason in bonus.reason_codes
                )
                or "Rule conditions satisfied.",
                "critical_red": bool(
                    bonus.result_snapshot.get("critical_red")
                ),
                "rule_version": str(bonus.bonus_rule_version),
                "calculation_date": bonus.calculated_at.isoformat(),
                "amount": amount,
                "currency": payout.get("currency", ""),
                "action_link": _signed_action("review", row["review"].pk),
            }
        )
    payload.sort(key=lambda item: item["employee"].casefold())
    return {
        "kind": "bonus-readiness",
        **counts,
        **_paginate(payload, page),
        "amounts_visible": allow_amounts,
        "invalid_bonus_snapshots": invalid,
    }


def _review_completion(user, audience, filters, rules):
    rows, invalid = _rows(user, audience, filters)
    latest, _previous = _latest_and_previous(rows)
    employees = list(scoped_dashboard_employees(user, audience))
    reviewed_ids = set(latest)
    missing = [
        employee
        for employee in employees
        if employee.pk not in reviewed_ids
    ]
    open_reviews = list(
        apply_dashboard_filters(
            scoped_dashboard_reviews(user, audience).exclude(
                status__in=APPROVED_STATUSES
            ),
            filters,
            include_kpi_status=False,
        )
    )
    today = timezone.localdate()
    overdue = [
        review
        for review in open_reviews
        if review.period_end < today - timedelta(days=rules.overdue_days)
    ]
    return {
        "kind": "review-completion",
        "completion": _score_text(_percent(len(reviewed_ids), len(employees))),
        "completed": len(reviewed_ids),
        "missing": len(missing),
        "open": len(open_reviews),
        "overdue": len(overdue),
        "target": _score_text(rules.review_completion_target),
        "target_met": (
            (_percent(len(reviewed_ids), len(employees)) or Decimal("0"))
            >= rules.review_completion_target
        ),
        "queue_link": (
            _signed_action("queue") if audience != AUDIENCE_EMPLOYEE else ""
        ),
        "invalid_snapshots": invalid,
    }


def _trend_analytics(user, audience, filters, rules):
    rows, invalid = _history_rows(
        user,
        audience,
        filters,
        rules.trend_periods,
    )
    monthly = _trend_points(
        [row for row in rows if row["review"].review_date.year == filters.year],
        "monthly",
    )
    quarterly = _trend_points(
        [row for row in rows if row["review"].review_date.year == filters.year],
        "quarterly",
    )
    annual = _trend_points(rows, "annual")
    return {
        "kind": "trends",
        "monthly": {
            "points": _serialize_points(monthly),
            "analysis": _trend(monthly, rules).as_dict(),
        },
        "quarterly": {
            "points": _serialize_points(quarterly),
            "analysis": _trend(quarterly, rules).as_dict(),
        },
        "annual": {
            "points": _serialize_points(annual),
            "analysis": _trend(annual, rules).as_dict(),
        },
        "invalid_snapshots": invalid,
    }


def _serialize_points(points):
    return [
        {"label": label, "value": _score_text(value)}
        for label, value in points
    ]


def _location_memberships(rows):
    employee_user_ids = {
        row["review"].employee.user_id
        for row in rows
    }
    ca_group = getattr(settings, "CA_TEAM_GROUP", "CA_TEAM")
    bd_group = getattr(settings, "BD_TEAM_GROUP", "BD_TEAM")
    memberships = defaultdict(set)
    from crm.models_employee import EmployeeProfile

    for user_id, group_name in (
        EmployeeProfile.objects.filter(user_id__in=employee_user_ids)
        .values_list("user_id", "user__groups__name")
    ):
        if group_name == ca_group:
            memberships["Canada"].add(user_id)
        elif group_name == bd_group:
            memberships["Bangladesh"].add(user_id)
    return memberships


def _location_analytics(user, audience, filters, rules):
    rows, _invalid = _rows(user, audience, filters)
    latest, previous = _latest_and_previous(rows)
    latest_rows = tuple(latest.values())
    memberships = _location_memberships(latest_rows)
    bonuses, _invalid_bonus = _bonus_rows(
        [row["review"].pk for row in latest_rows]
    )
    bonus_by_employee = {bonus.employee_id: bonus for bonus in bonuses}
    payload = []
    for name in ("Canada", "Bangladesh"):
        location_rows = [
            row
            for row in latest_rows
            if row["review"].employee.user_id in memberships[name]
        ]
        prior = [
            previous[row["review"].employee_id]["score"]
            for row in location_rows
            if row["review"].employee_id in previous
        ]
        current_score = _average(row["score"] for row in location_rows)
        previous_score = _average(prior)
        points = []
        if previous_score is not None:
            points.append(("Previous", previous_score))
        if current_score is not None:
            points.append(("Current", current_score))
        counts = _status_counts(location_rows)
        ready = sum(
            1
            for row in location_rows
            if bonus_by_employee.get(row["review"].employee_id)
            and bonus_by_employee[
                row["review"].employee_id
            ].eligibility_status
            == KPIBonusCalculation.ELIGIBLE
        )
        departments = _department_groups(location_rows)
        payload.append(
            {
                "name": name,
                "score": _score_text(current_score),
                "review_completion": _score_text(
                    _percent(len(location_rows), len(memberships[name]))
                ),
                "department_average": _score_text(
                    _average(
                        _average(row["score"] for row in department_rows)
                        for department_rows in departments.values()
                    )
                ),
                "critical_red": sum(
                    1 for row in location_rows if row["critical_red"]
                ),
                "yellow": counts["yellow"],
                "green": counts["green"],
                "red": counts["red"],
                "trend": _trend(points, rules).as_dict(),
                "bonus_ready": ready,
            }
        )
    return {"kind": "location-analytics", "rows": payload}


def _critical_alerts(user, audience, filters, rules, page):
    rows, _invalid = _rows(user, audience, filters)
    latest, _previous = _latest_and_previous(rows)
    alerts = []
    red_history = defaultdict(int)
    for row in rows:
        if row["status"] == "red":
            red_history[row["review"].employee_id] += 1
    for employee_id, row in latest.items():
        if row["status"] != "red" and not row["critical_red"]:
            continue
        repeated = red_history[employee_id]
        reason = (
            _critical_reason(row)
            if row["critical_red"]
            else "The latest approved KPI result is Red."
        )
        if repeated >= rules.critical_red_count:
            reason = f"{reason} Red status appears in {repeated} reviewed periods."
        alerts.append(
            IntelligenceInsight(
                insight_type="critical_red",
                status="red",
                severity=(
                    rules.critical_alert_severity
                    if row["critical_red"]
                    else rules.red_alert_severity
                ),
                title=f"{_employee_name(row['review'].employee)} needs support",
                summary=reason,
                supporting_value=_score_text(row["score"]),
                employee=_employee_name(row["review"].employee),
                manager=_user_name(row["review"].manager),
                department=row["review"].employee.department_name,
                review_period=row["review"].get_period_type_display(),
                source_record=f"kpi_review:{row['review'].pk}",
                recommended_action="Open the approved review and assign corrective ownership.",
                action_link=_signed_action("review", row["review"].pk),
                generated_date=timezone.now().isoformat(),
            ).as_dict()
        )

    if audience != AUDIENCE_EMPLOYEE:
        departments = _department_groups(tuple(latest.values()))
        for (code, name), department_rows in departments.items():
            score = _average(row["score"] for row in department_rows)
            if score is None or score >= rules.department_risk_threshold:
                continue
            alerts.append(
                IntelligenceInsight(
                    insight_type="department_risk",
                    status="red",
                    severity=rules.red_alert_severity,
                    title=f"{name} is below the configured risk threshold",
                    summary=(
                        f"Approved department average is {_score_text(score)}; "
                        f"the configured threshold is "
                        f"{_score_text(rules.department_risk_threshold)}."
                    ),
                    supporting_value=_score_text(score),
                    department=name,
                    source_record=f"department:{code}",
                    recommended_action="Open the department KPI dashboard and review the main risk.",
                    action_link=_signed_action("dashboard", department=code),
                    generated_date=timezone.now().isoformat(),
                ).as_dict()
            )

    bonuses, _invalid_bonus = _bonus_rows(
        [row["review"].pk for row in latest.values()]
    )
    for bonus in bonuses:
        if bonus.eligibility_status != KPIBonusCalculation.BLOCKED:
            continue
        review = latest.get(bonus.employee_id, {}).get("review")
        if review is None:
            continue
        alerts.append(
            IntelligenceInsight(
                insight_type="bonus_blocked",
                status="red",
                severity=rules.red_alert_severity,
                title=f"{_employee_name(review.employee)} bonus result is blocked",
                summary=", ".join(
                    _clean_text(reason, 100) for reason in bonus.reason_codes
                )
                or "The immutable bonus result is blocked.",
                employee=_employee_name(review.employee),
                department=review.employee.department_name,
                source_record=f"kpi_bonus:{bonus.pk}",
                recommended_action="Review the source KPI approval and bonus reason.",
                action_link=_signed_action("review", review.pk),
                generated_date=timezone.now().isoformat(),
            ).as_dict()
        )
    alerts.sort(
        key=lambda item: (
            item["severity"] != KPIIntelligenceRuleSet.SEVERITY_CRITICAL,
            item["title"],
        )
    )
    return {"kind": "insights", "tone": "red", **_paginate(alerts, page)}


def _yellow_attention(user, audience, filters, rules, page):
    rows, _invalid = _rows(user, audience, filters)
    latest, previous = _latest_and_previous(rows)
    insights = []
    for employee_id, row in latest.items():
        prior = previous.get(employee_id)
        change = row["score"] - prior["score"] if prior else None
        if row["status"] != "yellow" and (
            change is None or change >= Decimal("0")
        ):
            continue
        summary = (
            "The latest approved KPI result is Yellow."
            if row["status"] == "yellow"
            else "The approved KPI trend has moved down."
        )
        insights.append(
            IntelligenceInsight(
                insight_type="attention",
                status="yellow",
                severity=rules.yellow_alert_severity,
                title=f"Check progress with {_employee_name(row['review'].employee)}",
                summary=summary,
                supporting_value=_score_text(row["score"]),
                previous_value=_score_text(prior["score"] if prior else None),
                change_percentage=_signed_text(change),
                employee=_employee_name(row["review"].employee),
                manager=_user_name(row["review"].manager),
                department=row["review"].employee.department_name,
                source_record=f"kpi_review:{row['review'].pk}",
                recommended_action="Discuss the weakest KPI before the next review.",
                action_link=_signed_action("review", row["review"].pk),
                generated_date=timezone.now().isoformat(),
            ).as_dict()
        )
    return {"kind": "insights", "tone": "yellow", **_paginate(insights, page)}


def _green_success(user, audience, filters, rules, page):
    rows, _invalid = _rows(user, audience, filters)
    latest, previous = _latest_and_previous(rows)
    insights = []
    for employee_id, row in latest.items():
        if row["status"] != "green":
            continue
        prior = previous.get(employee_id)
        change = row["score"] - prior["score"] if prior else None
        insights.append(
            IntelligenceInsight(
                insight_type="success",
                status="green",
                severity=KPIIntelligenceRuleSet.SEVERITY_LOW,
                title=f"{_employee_name(row['review'].employee)} is Green",
                summary=(
                    "Approved performance is improving."
                    if change is not None and change >= rules.improvement_threshold
                    else "Approved performance is meeting the Green standard."
                ),
                supporting_value=_score_text(row["score"]),
                previous_value=_score_text(prior["score"] if prior else None),
                change_percentage=_signed_text(change),
                employee=_employee_name(row["review"].employee),
                manager=_user_name(row["review"].manager),
                department=row["review"].employee.department_name,
                source_record=f"kpi_review:{row['review'].pk}",
                recommended_action="Recognize the result privately and protect the strongest KPI.",
                action_link=_signed_action("review", row["review"].pk),
                generated_date=timezone.now().isoformat(),
            ).as_dict()
        )
    insights.sort(
        key=lambda item: Decimal(item["supporting_value"] or "0"),
        reverse=True,
    )
    return {"kind": "insights", "tone": "green", **_paginate(insights, page)}


def _data_quality(user, audience, filters, rules):
    rows, invalid = _rows(user, audience, filters)
    latest, _previous = _latest_and_previous(rows)
    employees = list(scoped_dashboard_employees(user, audience))
    memberships = _location_memberships(tuple(latest.values()))
    located_user_ids = set().union(*memberships.values()) if memberships else set()
    bonuses, invalid_bonuses = _bonus_rows(
        [row["review"].pk for row in latest.values()]
    )
    warnings = []
    if invalid:
        warnings.append(
            {
                "code": "invalid_snapshot",
                "count": invalid,
                "message": "Approved snapshots failed digest or score validation.",
            }
        )
    missing_reviews = len(employees) - len(latest)
    if missing_reviews > 0:
        warnings.append(
            {
                "code": "missing_review",
                "count": missing_reviews,
                "message": "Visible employees have no approved review in this period.",
            }
        )
    missing_department = sum(
        1 for employee in employees if not _department_details(employee)[0]
    )
    if missing_department:
        warnings.append(
            {
                "code": "missing_department",
                "count": missing_department,
                "message": "Employee department data is missing.",
            }
        )
    missing_manager = sum(
        1 for row in latest.values() if row["review"].manager_id is None
    )
    if missing_manager:
        warnings.append(
            {
                "code": "missing_manager",
                "count": missing_manager,
                "message": "Approved reviews do not identify a manager.",
            }
        )
    missing_location = sum(
        1
        for row in latest.values()
        if row["review"].employee.user_id not in located_user_ids
    )
    if missing_location:
        warnings.append(
            {
                "code": "missing_location",
                "count": missing_location,
                "message": "Canada or Bangladesh team location is missing.",
            }
        )
    incomplete_assignment = sum(
        1
        for row in latest.values()
        if any(
            not role.get("assignment_version")
            for role in row["review"].approved_snapshot.get(
                "definition", {}
            ).get("roles", [])
        )
    )
    if incomplete_assignment:
        warnings.append(
            {
                "code": "incomplete_assignment_history",
                "count": incomplete_assignment,
                "message": "Approved definition lacks assignment version history.",
            }
        )
    missing_bonus = max(len(latest) - len(bonuses), 0)
    if missing_bonus:
        warnings.append(
            {
                "code": "missing_bonus_rule",
                "count": missing_bonus,
                "message": "Approved reviews have no immutable bonus result.",
            }
        )
    if invalid_bonuses:
        warnings.append(
            {
                "code": "invalid_bonus_snapshot",
                "count": invalid_bonuses,
                "message": "Bonus result snapshots failed digest validation.",
            }
        )
    history_periods = {
        (
            row["review"].period_type,
            row["review"].period_start,
            row["review"].period_end,
        )
        for row in rows
    }
    if len(history_periods) < 2:
        warnings.append(
            {
                "code": "insufficient_history",
                "count": 1,
                "message": "Insufficient History for annual trend analysis.",
            }
        )
    return {
        "kind": "data-quality",
        "warnings": warnings,
        "complete": not warnings,
    }


def _recommended_actions(user, audience, filters, rules, page):
    critical = _critical_alerts(user, audience, filters, rules, 1)["rows"]
    yellow = _yellow_attention(user, audience, filters, rules, 1)["rows"]
    rows = [
        {
            **item,
            "priority": "Now" if item["status"] == "red" else "Next",
        }
        for item in (critical + yellow)
        if item.get("action_link")
    ]
    return {"kind": "actions", **_paginate(rows, page)}


def _paginate(rows, page):
    if page is None:
        total = len(rows)
        return {
            "rows": rows,
            "page": 1,
            "page_size": total,
            "total": total,
            "has_previous": False,
            "has_next": False,
            "previous_page": None,
            "next_page": None,
        }
    try:
        page = max(int(page), 1)
    except (TypeError, ValueError):
        page = 1
    total = len(rows)
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    return {
        "rows": rows[start:end],
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total,
        "has_previous": page > 1,
        "has_next": end < total,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if end < total else None,
    }


WIDGET_BUILDERS = {
    "company-health": _company_health,
    "critical-alerts": _critical_alerts,
    "yellow-attention": _yellow_attention,
    "green-success": _green_success,
    "department-analytics": _department_analytics,
    "manager-analytics": _manager_analytics,
    "employee-analytics": _employee_analytics,
    "bonus-readiness": _bonus_readiness,
    "review-completion": _review_completion,
    "trends": _trend_analytics,
    "location-analytics": _location_analytics,
    "recommended-actions": _recommended_actions,
    "data-quality": _data_quality,
}


PAGED_WIDGETS = {
    "critical-alerts",
    "yellow-attention",
    "green-success",
    "employee-analytics",
    "bonus-readiness",
    "recommended-actions",
}


def intelligence_widget_payload(
    user,
    slug,
    filters,
    *,
    page=1,
    use_cache=True,
):
    audience = dashboard_audience(user)
    allowed = {widget.slug for widget in INTELLIGENCE_WIDGETS[audience]}
    if slug not in allowed or slug not in WIDGET_BUILDERS:
        raise KPIIntelligencePermissionError(
            "This intelligence section is not available."
        )
    cache_payload = {
        "filters": asdict(filters),
        "page": page if slug in PAGED_WIDGETS else 1,
        "rule_generation": cache.get(INTELLIGENCE_RULE_GENERATION_KEY, "1"),
    }
    token = hashlib.sha256(
        json.dumps(cache_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    cache_key = (
        f"{INTELLIGENCE_CACHE_VERSION}:{user.pk}:{audience}:{slug}:{token}"
    )
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return {**cached, "_cache_hit": True}
    rules = effective_intelligence_rule_set(
        _rule_date(filters),
        required=False,
    )
    if rules is None:
        return {
            "kind": "configuration-required",
            "slug": slug,
            "audience": audience,
            "message": "No effective published intelligence rule set is available.",
            "generated_at": timezone.now().isoformat(),
            "intelligence_version": INTELLIGENCE_VERSION,
            "_cache_hit": False,
        }
    builder = WIDGET_BUILDERS[slug]
    if slug in PAGED_WIDGETS:
        payload = builder(user, audience, filters, rules, page)
    else:
        payload = builder(user, audience, filters, rules)
    payload.update(
        {
            "slug": slug,
            "audience": audience,
            "generated_at": timezone.now().isoformat(),
            "intelligence_version": INTELLIGENCE_VERSION,
            "rule_version": rules.version,
            "snapshot_only": True,
        }
    )
    if use_cache:
        cache.set(cache_key, payload, INTELLIGENCE_CACHE_SECONDS)
    return {**payload, "_cache_hit": False}
