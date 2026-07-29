import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from crm.models_kpi_bonus import KPIBonusCalculation
from crm.models_kpi_notifications import (
    KPIAutomationRun,
    KPIEscalationRule,
    KPINotificationRule,
    KPI_NOTIFICATION_SCHEDULES,
)
from crm.models_kpi_reviews import KPIReview
from crm.services.kpi_dashboard import DashboardFilters
from crm.services.kpi_intelligence import (
    KPIIntelligenceError,
    intelligence_widget_payload,
)
from crm.services.kpi_notifications import (
    KPINotificationError,
    KPINotificationRequest,
    create_kpi_notifications,
    effective_notification_rule,
    record_kpi_notification_audits,
)
from crm.services.operations_permissions import (
    ROLE_CEO,
    ROLE_DIRECTOR,
    ROLE_HR,
)


logger = logging.getLogger(__name__)
KPI_AUTOMATION_VERSION = "kpi-automation/1.0"


@dataclass(frozen=True, slots=True)
class AutomationCandidate:
    notification_type: str
    title: str
    message: str
    source_event: str
    source_record_type: str
    source_record_id: str
    source_period: str
    source_state: str
    action_link: str
    due_date: date | None = None
    related_employee: object | None = None
    related_manager: object | None = None
    related_review: object | None = None
    related_department_code: str = ""
    related_department_name: str = ""
    trigger_offset_days: int = 0
    progressive: bool = False


class RecipientDirectory:
    def __init__(self):
        User = get_user_model()
        users = list(
            User.objects.filter(is_active=True)
            .filter(
                Q(groups__name__in=(ROLE_CEO, ROLE_DIRECTOR, ROLE_HR))
                | Q(is_superuser=True)
            )
            .select_related("employee_profile__department_ref")
            .prefetch_related("groups")
            .distinct()
        )
        self.users = {user.pk: user for user in users}
        self.roles = {
            user.pk: {group.name for group in user.groups.all()}
            for user in users
        }
        self.departments = {
            user.pk: self._department(user)
            for user in users
        }

    @staticmethod
    def _department(user):
        profile = getattr(user, "employee_profile", None)
        if not profile:
            return ""
        if profile.department_ref_id:
            return profile.department_ref.code
        return profile.department or ""

    @property
    def executive_users(self):
        return tuple(
            user
            for user in self.users.values()
            if user.is_superuser or ROLE_CEO in self.roles[user.pk]
        )

    @property
    def hr_users(self):
        return tuple(
            user for user in self.users.values() if ROLE_HR in self.roles[user.pk]
        )

    def directors(self, department_code):
        return tuple(
            user
            for user in self.users.values()
            if ROLE_DIRECTOR in self.roles[user.pk]
            and department_code
            and self.departments[user.pk] == department_code
        )


def _department(employee):
    if not employee:
        return "", ""
    if employee.department_ref_id:
        return employee.department_ref.code, employee.department_ref.name
    return employee.department or "", employee.department_name or ""


def _period(review):
    return (
        f"{review.period_type}:{review.period_start.isoformat()}:"
        f"{review.period_end.isoformat()}"
    )


def _local_date(value):
    if not value:
        return None
    return timezone.localtime(value).date()


def _enabled(rule, event_type):
    return event_type in rule.enabled_event_types


def _review_candidates(rule, as_of):
    candidates = []
    offsets = set(rule.reminder_offsets)
    due_dates = [as_of + timedelta(days=offset) for offset in offsets]
    reviews = list(
        KPIReview.objects.select_related(
            "employee__user",
            "employee__department_ref",
            "manager",
        )
        .filter(
            Q(period_end__in=due_dates)
            | Q(period_start=as_of)
            | Q(rejected_at__date=as_of)
            | Q(locked_at__date=as_of)
        )
        .order_by("pk")
    )
    improvement_dates = [
        as_of - timedelta(days=days)
        for days in rule.improvement_reminder_days
    ]
    improvement_reviews = (
        list(
            KPIReview.objects.select_related(
                "employee__user",
                "employee__department_ref",
                "manager",
            )
            .filter(
                status__in=(KPIReview.STATUS_APPROVED, KPIReview.STATUS_LOCKED),
                approved_at__date__in=improvement_dates,
            )
            .order_by("pk")
        )
        if improvement_dates
        else []
    )

    for review in reviews:
        department_code, department_name = _department(review.employee)
        review_action = reverse("kpi_review_detail", args=[review.pk])
        source_period = _period(review)
        if (
            _enabled(rule, "review_open")
            and review.period_start == as_of
            and review.status == KPIReview.STATUS_DRAFT
        ):
            candidates.append(
                AutomationCandidate(
                    notification_type="review_open",
                    title="KPI review period opened",
                    message="The KPI review period is open and ready for the assigned workflow.",
                    source_event="review_period_opened",
                    source_record_type="kpi_review",
                    source_record_id=str(review.pk),
                    source_period=source_period,
                    source_state=f"{review.status}:open",
                    action_link=review_action,
                    due_date=review.period_end,
                    related_employee=review.employee,
                    related_manager=review.manager,
                    related_review=review,
                    related_department_code=department_code,
                    related_department_name=department_name,
                    trigger_offset_days=0,
                )
            )

        offset = (review.period_end - as_of).days
        if offset in offsets and review.status not in {
            KPIReview.STATUS_APPROVED,
            KPIReview.STATUS_LOCKED,
        }:
            if offset > 0:
                event_type = "review_due_soon"
                title = "KPI review due soon"
                message = f"The assigned KPI review is due in {offset} day(s)."
            elif offset == 0:
                event_type = "review_due_today"
                title = "KPI review due today"
                message = "The assigned KPI review is due today."
            else:
                event_type = "review_overdue"
                title = "KPI review overdue"
                message = f"The assigned KPI review is {-offset} day(s) overdue."
            if _enabled(rule, event_type):
                candidates.append(
                    AutomationCandidate(
                        notification_type=event_type,
                        title=title,
                        message=message,
                        source_event=f"{event_type}:{offset}",
                        source_record_type="kpi_review",
                        source_record_id=str(review.pk),
                        source_period=source_period,
                        source_state=f"{review.status}:{offset}",
                        action_link=review_action,
                        due_date=review.period_end,
                        related_employee=review.employee,
                        related_manager=review.manager,
                        related_review=review,
                        related_department_code=department_code,
                        related_department_name=department_name,
                        trigger_offset_days=offset,
                    )
                )
            if (
                _enabled(rule, "approval_pending")
                and review.status
                in {KPIReview.STATUS_SUBMITTED, KPIReview.STATUS_UNDER_REVIEW}
            ):
                candidates.append(
                    AutomationCandidate(
                        notification_type="approval_pending",
                        title="KPI review approval is pending",
                        message="An assigned KPI review is waiting for authorized approval.",
                        source_event=f"approval_pending:{offset}",
                        source_record_type="kpi_review",
                        source_record_id=str(review.pk),
                        source_period=source_period,
                        source_state=f"{review.status}:{offset}",
                        action_link=review_action,
                        due_date=review.period_end,
                        related_employee=review.employee,
                        related_manager=review.manager,
                        related_review=review,
                        related_department_code=department_code,
                        related_department_name=department_name,
                        trigger_offset_days=offset,
                    )
                )

        if (
            _enabled(rule, "rejected_correction")
            and _local_date(review.rejected_at) == as_of
        ):
            candidates.append(
                AutomationCandidate(
                    notification_type="rejected_correction",
                    title="KPI review returned for correction",
                    message="The rejected KPI review requires correction before resubmission.",
                    source_event="review_rejected",
                    source_record_type="kpi_review",
                    source_record_id=str(review.pk),
                    source_period=source_period,
                    source_state=f"{review.status}:{review.rejected_at}",
                    action_link=review_action,
                    due_date=review.period_end,
                    related_employee=review.employee,
                    related_manager=review.manager,
                    related_review=review,
                    related_department_code=department_code,
                    related_department_name=department_name,
                )
            )
        if (
            _enabled(rule, "locked_confirmation")
            and review.status == KPIReview.STATUS_LOCKED
            and _local_date(review.locked_at) == as_of
        ):
            candidates.append(
                AutomationCandidate(
                    notification_type="locked_confirmation",
                    title="KPI review locked",
                    message="The approved KPI review is locked and preserved as history.",
                    source_event="review_locked",
                    source_record_type="kpi_review",
                    source_record_id=str(review.pk),
                    source_period=source_period,
                    source_state=f"{review.status}:{review.snapshot_digest}",
                    action_link=review_action,
                    due_date=review.period_end,
                    related_employee=review.employee,
                    related_manager=review.manager,
                    related_review=review,
                    related_department_code=department_code,
                    related_department_name=department_name,
                )
            )

    if _enabled(rule, "improvement_action"):
        for review in improvement_reviews:
            status = (
                review.approved_snapshot.get("result", {}).get("status", "")
            )
            if status not in {"yellow", "red"}:
                continue
            days = (as_of - _local_date(review.approved_at)).days
            department_code, department_name = _department(review.employee)
            candidates.append(
                AutomationCandidate(
                    notification_type="improvement_action",
                    title="KPI improvement follow-up due",
                    message=(
                        "Follow up on the approved KPI result and agreed improvement work."
                    ),
                    source_event=f"improvement_follow_up:{days}",
                    source_record_type="kpi_review",
                    source_record_id=str(review.pk),
                    source_period=_period(review),
                    source_state=f"{status}:{review.snapshot_digest}:{days}",
                    action_link=reverse("kpi_review_detail", args=[review.pk]),
                    due_date=as_of,
                    related_employee=review.employee,
                    related_manager=review.manager,
                    related_review=review,
                    related_department_code=department_code,
                    related_department_name=department_name,
                    trigger_offset_days=-days,
                )
            )
    return candidates


def _executive_user(directory):
    return directory.executive_users[0] if directory.executive_users else None


def _source_maps(rows):
    review_ids = set()
    bonus_ids = set()
    for row in rows:
        source = row.get("source_record", "")
        prefix, separator, value = source.partition(":")
        if separator and value.isdigit():
            if prefix == "kpi_review":
                review_ids.add(int(value))
            elif prefix == "kpi_bonus":
                bonus_ids.add(int(value))
    review_map = {
        review.pk: review
        for review in KPIReview.objects.select_related(
            "employee__user",
            "employee__department_ref",
            "manager",
        ).filter(pk__in=review_ids)
    }
    bonus_map = {
        bonus.pk: bonus
        for bonus in KPIBonusCalculation.objects.select_related(
            "review__employee__user",
            "review__employee__department_ref",
            "review__manager",
        ).filter(pk__in=bonus_ids)
    }
    return review_map, bonus_map


def _candidate_from_insight(event_type, row, review_map, bonus_map, as_of):
    source = row.get("source_record", "")
    prefix, _separator, value = source.partition(":")
    review = None
    if prefix == "kpi_review" and value.isdigit():
        review = review_map.get(int(value))
    elif prefix == "kpi_bonus" and value.isdigit():
        bonus = bonus_map.get(int(value))
        review = bonus.review if bonus else None
    employee = review.employee if review else None
    manager = review.manager if review else None
    department_code, department_name = _department(employee)
    if prefix == "department":
        department_code = value
        department_name = row.get("department", value)
    reference_date = (
        _local_date(review.approved_at)
        if review and review.approved_at
        else as_of
    )
    age_days = max((as_of - reference_date).days, 0)
    return AutomationCandidate(
        notification_type=event_type,
        title=row.get("title") or "KPI attention required",
        message=row.get("summary") or "Approved KPI intelligence requires review.",
        source_event=row.get("insight_type") or event_type,
        source_record_type=prefix or "kpi_intelligence",
        source_record_id=value or str(as_of.year),
        source_period=_period(review) if review else str(as_of.year),
        source_state=(
            f"{row.get('status')}:{row.get('severity')}:"
            f"{row.get('supporting_value')}:{row.get('change_percentage')}"
        ),
        action_link=row.get("action_link") or reverse("kpi_intelligence"),
        due_date=as_of,
        related_employee=employee,
        related_manager=manager,
        related_review=review,
        related_department_code=department_code,
        related_department_name=department_name,
        trigger_offset_days=-age_days,
        progressive=True,
    )


def _intelligence_candidates(rule, as_of, schedule, directory):
    executive = _executive_user(directory)
    if executive is None:
        return []
    filters = DashboardFilters(year=as_of.year)
    candidates = []

    if schedule == "daily":
        payloads = {}
        for slug in (
            "critical-alerts",
            "yellow-attention",
            "green-success",
            "data-quality",
            "manager-analytics",
            "company-health",
        ):
            payloads[slug] = intelligence_widget_payload(
                executive,
                slug,
                filters,
                page=None,
                use_cache=False,
            )
        if any(
            payload.get("kind") == "configuration-required"
            for payload in payloads.values()
        ):
            if _enabled(rule, "executive_intelligence"):
                candidates.append(
                    AutomationCandidate(
                        notification_type="executive_intelligence",
                        title="Published KPI intelligence rule required",
                        message=(
                            "Executive intelligence automation cannot evaluate "
                            "until an effective published rule exists."
                        ),
                        source_event="intelligence_rule_missing",
                        source_record_type="kpi_intelligence_rule",
                        source_record_id=str(as_of.year),
                        source_period=str(as_of.year),
                        source_state="configuration_required",
                        action_link=reverse("kpi_intelligence"),
                        due_date=as_of,
                    )
                )
            return candidates

        insight_rows = []
        for slug in ("critical-alerts", "yellow-attention", "green-success"):
            insight_rows.extend(payloads[slug].get("rows", []))
        review_map, bonus_map = _source_maps(insight_rows)
        if _enabled(rule, "critical_red"):
            for row in payloads["critical-alerts"].get("rows", []):
                event_type = (
                    "bonus_blocked"
                    if row.get("insight_type") == "bonus_blocked"
                    and _enabled(rule, "bonus_blocked")
                    else "critical_red"
                )
                candidates.append(
                    _candidate_from_insight(
                        event_type,
                        row,
                        review_map,
                        bonus_map,
                        as_of,
                    )
                )
        if _enabled(rule, "yellow_attention"):
            candidates.extend(
                _candidate_from_insight(
                    "yellow_attention",
                    row,
                    review_map,
                    bonus_map,
                    as_of,
                )
                for row in payloads["yellow-attention"].get("rows", [])
            )
        if rule.positive_recognition_enabled and _enabled(
            rule,
            "green_recognition",
        ):
            candidates.extend(
                _candidate_from_insight(
                    "green_recognition",
                    row,
                    review_map,
                    bonus_map,
                    as_of,
                )
                for row in payloads["green-success"].get("rows", [])
            )
        if _enabled(rule, "data_quality"):
            for warning in payloads["data-quality"].get("warnings", []):
                candidates.append(
                    AutomationCandidate(
                        notification_type="data_quality",
                        title="KPI data quality needs attention",
                        message=warning.get("message", "KPI source data is incomplete."),
                        source_event=warning.get("code", "data_quality"),
                        source_record_type="kpi_data_quality",
                        source_record_id=warning.get("code", "warning"),
                        source_period=str(as_of.year),
                        source_state=f"{warning.get('code')}:{warning.get('count')}",
                        action_link=reverse("kpi_intelligence"),
                        due_date=as_of,
                    )
                )
        if _enabled(rule, "manager_queue"):
            manager_ids = {
                row.get("manager_id")
                for row in payloads["manager-analytics"].get("rows", [])
                if row.get("manager_id")
            }
            manager_map = {
                user.pk: user
                for user in get_user_model().objects.filter(
                    pk__in=manager_ids,
                    is_active=True,
                )
            }
            for row in payloads["manager-analytics"].get("rows", []):
                if row.get("workload_status") == "green":
                    continue
                manager_id = row.get("manager_id")
                manager = manager_map.get(manager_id)
                candidates.append(
                    AutomationCandidate(
                        notification_type="manager_queue",
                        title="KPI manager queue needs attention",
                        message=(
                            f"The authorized manager queue contains "
                            f"{row.get('workload', 0)} open review(s)."
                        ),
                        source_event="manager_queue_threshold",
                        source_record_type="manager",
                        source_record_id=str(manager_id or "unassigned"),
                        source_period=str(as_of.year),
                        source_state=(
                            f"{row.get('workload_status')}:{row.get('workload')}:"
                            f"{row.get('overdue_reviews')}"
                        ),
                        action_link=row.get("action_link") or reverse("kpi_review_list"),
                        due_date=as_of,
                        related_manager=manager,
                    )
                )
        health = payloads["company-health"]
        if (
            _enabled(rule, "executive_intelligence")
            and health.get("status") in {"yellow", "red"}
        ):
            candidates.append(
                AutomationCandidate(
                    notification_type="executive_intelligence",
                    title=f"Company KPI health is {health['status'].title()}",
                    message=(
                        "Approved KPI intelligence indicates that executive "
                        "attention is required."
                    ),
                    source_event="company_health_status",
                    source_record_type="company_health",
                    source_record_id=str(as_of.year),
                    source_period=str(as_of.year),
                    source_state=f"{health.get('status')}:{health.get('score')}",
                    action_link=reverse("kpi_intelligence"),
                    due_date=as_of,
                )
            )

    if schedule in {"weekly", "monthly", "quarterly", "annual"} and _enabled(
        rule,
        "executive_intelligence",
    ):
        slug = "department-analytics" if schedule == "weekly" else "company-health"
        payload = intelligence_widget_payload(
            executive,
            slug,
            filters,
            page=None,
            use_cache=False,
        )
        if payload.get("kind") != "configuration-required":
            state = (
                "|".join(
                    f"{row.get('name')}:{row.get('current_score')}:{row.get('trend', {}).get('direction')}"
                    for row in payload.get("rows", [])
                )
                if slug == "department-analytics"
                else f"{payload.get('status')}:{payload.get('score')}"
            )
            candidates.append(
                AutomationCandidate(
                    notification_type="executive_intelligence",
                    title=f"{schedule.title()} KPI summary ready",
                    message="The approved KPI intelligence summary is ready for review.",
                    source_event=f"{schedule}_kpi_summary",
                    source_record_type="kpi_intelligence",
                    source_record_id=f"{schedule}:{as_of.isoformat()}",
                    source_period=_schedule_period(schedule, as_of),
                    source_state=state,
                    action_link=reverse("kpi_intelligence"),
                    due_date=as_of,
                )
            )
    return candidates


def _bonus_candidates(rule, as_of, schedule):
    if schedule not in {"daily", "quarterly"}:
        return []
    enabled = set(rule.enabled_event_types)
    if not enabled.intersection({"bonus_ready", "bonus_pending", "bonus_blocked"}):
        return []
    since = as_of - timedelta(days=rule.source_lookback_days)
    bonuses = KPIBonusCalculation.objects.select_related(
        "review__employee__user",
        "review__employee__department_ref",
        "review__manager",
        "rule_set",
    ).filter(review_date__gte=since, review_date__lte=as_of)
    candidates = []
    for bonus in bonuses:
        if bonus.eligibility_status == KPIBonusCalculation.BLOCKED:
            event_type = "bonus_blocked"
            title = "KPI bonus result is blocked"
            message = "The immutable bonus result requires authorized review."
        elif bonus.eligibility_status == KPIBonusCalculation.ELIGIBLE:
            if bonus.rule_set.approval_required:
                event_type = "bonus_pending"
                title = "KPI bonus result is pending approval"
                message = "The immutable bonus result is ready for authorized approval."
            else:
                event_type = "bonus_ready"
                title = "KPI bonus calculation is ready"
                message = "The immutable bonus result is ready for authorized review."
        else:
            continue
        if event_type not in enabled:
            continue
        review = bonus.review
        department_code, department_name = _department(review.employee)
        age_days = max((as_of - bonus.review_date).days, 0)
        candidates.append(
            AutomationCandidate(
                notification_type=event_type,
                title=title,
                message=message,
                source_event=f"bonus_{bonus.eligibility_status}",
                source_record_type="kpi_bonus",
                source_record_id=str(bonus.pk),
                source_period=_period(review),
                source_state=bonus.result_digest,
                action_link=reverse("kpi_review_detail", args=[review.pk]),
                due_date=as_of,
                related_employee=review.employee,
                related_manager=review.manager,
                related_review=review,
                related_department_code=department_code,
                related_department_name=department_name,
                trigger_offset_days=-age_days,
                progressive=True,
            )
        )
    return candidates


def _schedule_period(schedule, as_of):
    if schedule == "weekly":
        year, week, _weekday = as_of.isocalendar()
        return f"{year}-W{week:02d}"
    if schedule == "monthly":
        return f"{as_of.year}-{as_of.month:02d}"
    if schedule == "quarterly":
        return f"{as_of.year}-Q{((as_of.month - 1) // 3) + 1}"
    if schedule == "annual":
        return str(as_of.year)
    return as_of.isoformat()


def _applicable_escalations(rule, candidate):
    escalations = [
        escalation
        for escalation in rule.escalation_rules.all()
        if escalation.is_active
        and escalation.event_type == candidate.notification_type
    ]
    if not escalations:
        return ()
    if not candidate.progressive:
        return tuple(
            escalation
            for escalation in escalations
            if escalation.trigger_offset_days == candidate.trigger_offset_days
        )
    elapsed = max(-candidate.trigger_offset_days, 0)
    eligible = [
        escalation
        for escalation in escalations
        if escalation.trigger_offset_days <= 0
        and -escalation.trigger_offset_days <= elapsed
    ]
    if not eligible:
        return ()
    return (
        max(
            eligible,
            key=lambda escalation: (
                -escalation.trigger_offset_days,
                escalation.stage,
            ),
        ),
    )


def _scope_users(scope, candidate, directory):
    if scope == "employee":
        user = (
            candidate.related_employee.user
            if candidate.related_employee
            and candidate.related_employee.user.is_active
            else None
        )
        return (user,) if user else ()
    if scope == "manager":
        manager = candidate.related_manager
        return (manager,) if manager and manager.is_active else ()
    if scope == "director":
        return directory.directors(candidate.related_department_code)
    if scope == "hr":
        return directory.hr_users
    if scope == "executive":
        return directory.executive_users
    return ()


def _recipient_action(candidate, scope):
    if scope == "employee" and candidate.related_employee:
        return reverse(
            "employee_performance",
            args=[candidate.related_employee.user_id],
        )
    return candidate.action_link


def _requests(rule, candidates, directory):
    requests = []
    for candidate in candidates:
        for escalation in _applicable_escalations(rule, candidate):
            recipients = {}
            for scope in escalation.recipient_scopes:
                for recipient in _scope_users(scope, candidate, directory):
                    recipients.setdefault(recipient.pk, (recipient, scope))
            for recipient, scope in recipients.values():
                requests.append(
                    KPINotificationRequest(
                        notification_rule=rule,
                        escalation_rule=escalation,
                        recipient=recipient,
                        notification_type=candidate.notification_type,
                        severity=escalation.severity,
                        title=candidate.title,
                        message=candidate.message,
                        action_link=_recipient_action(candidate, scope),
                        source_event=candidate.source_event,
                        source_record_type=candidate.source_record_type,
                        source_record_id=candidate.source_record_id,
                        source_period=candidate.source_period,
                        source_state=(
                            f"{candidate.source_state}:stage:{escalation.stage}"
                        ),
                        due_date=candidate.due_date,
                        related_employee=candidate.related_employee,
                        related_manager=candidate.related_manager,
                        related_review=candidate.related_review,
                        related_department_code=candidate.related_department_code,
                        related_department_name=candidate.related_department_name,
                    )
                )
    return requests


def _collect_candidates(rule, as_of, schedule, directory):
    candidates = []
    if schedule == "daily":
        candidates.extend(_review_candidates(rule, as_of))
    candidates.extend(_intelligence_candidates(rule, as_of, schedule, directory))
    candidates.extend(_bonus_candidates(rule, as_of, schedule))
    return candidates


def _safe_failure_message(exc):
    message = " ".join(str(exc).split())
    return message[:300] or exc.__class__.__name__


def _audit_run(run, event):
    record_kpi_notification_audits(
        actor=run.initiated_by,
        events=(
            {
                "event": event,
                "record_id": run.pk,
                "record_label": f"{run.schedule} KPI automation",
            },
        ),
    )


def run_kpi_automation(*, schedule, as_of=None, actor=None):
    if schedule not in KPI_NOTIFICATION_SCHEDULES:
        raise KPINotificationError("Select a supported KPI automation schedule.")
    as_of = as_of or timezone.localdate()
    try:
        rule = effective_notification_rule(as_of)
    except Exception as exc:
        run = KPIAutomationRun.objects.create(
            task_name=f"kpi_notifications_{schedule}",
            schedule=schedule,
            source_date=as_of,
            retry_limit=0,
            batch_size=100,
            status=KPIAutomationRun.STATUS_FAILED,
            failure_type=exc.__class__.__name__[:120],
            failure_message=_safe_failure_message(exc),
            started_at=timezone.now(),
            completed_at=timezone.now(),
            initiated_by=(
                actor
                if actor and getattr(actor, "is_authenticated", False)
                else None
            ),
        )
        _audit_run(run, "automation_run_started")
        record_kpi_notification_audits(
            actor=actor,
            events=(
                {
                    "event": "notification_delivery_failed",
                    "record_id": run.pk,
                    "record_label": run.failure_type,
                },
                {
                    "event": "automation_run_failed",
                    "record_id": run.pk,
                    "record_label": f"{schedule} KPI automation",
                },
            ),
        )
        return run
    if schedule not in rule.enabled_schedules:
        raise KPINotificationError(
            "The effective notification rule does not enable this schedule."
        )
    run = KPIAutomationRun.objects.create(
        task_name=f"kpi_notifications_{schedule}",
        schedule=schedule,
        source_date=as_of,
        retry_limit=rule.retry_limit,
        batch_size=rule.batch_size,
        initiated_by=actor if actor and getattr(actor, "is_authenticated", False) else None,
    )
    _audit_run(run, "automation_run_started")
    last_error = None
    for attempt in range(rule.retry_limit + 1):
        try:
            directory = RecipientDirectory()
            candidates = _collect_candidates(rule, as_of, schedule, directory)
            requests = _requests(rule, candidates, directory)
            result = create_kpi_notifications(
                requests,
                automation_run=run,
                actor=actor,
            )
            run.status = KPIAutomationRun.STATUS_COMPLETED
            run.retry_count = attempt
            run.candidates_count = len(requests)
            run.created_count = result["created"]
            run.duplicate_count = result["duplicates"]
            run.completed_at = timezone.now()
            run.failure_type = ""
            run.failure_message = ""
            run.save(
                update_fields=(
                    "status",
                    "retry_count",
                    "candidates_count",
                    "created_count",
                    "duplicate_count",
                    "completed_at",
                    "failure_type",
                    "failure_message",
                )
            )
            _audit_run(run, "automation_run_completed")
            return run
        except Exception as exc:
            last_error = exc
            run.retry_count = attempt
            run.failure_type = exc.__class__.__name__[:120]
            run.failure_message = _safe_failure_message(exc)
            run.save(
                update_fields=(
                    "retry_count",
                    "failure_type",
                    "failure_message",
                )
            )
            if attempt < rule.retry_limit:
                continue
    run.status = KPIAutomationRun.STATUS_FAILED
    run.completed_at = timezone.now()
    run.save(
        update_fields=(
            "status",
            "completed_at",
            "retry_count",
            "failure_type",
            "failure_message",
        )
    )
    record_kpi_notification_audits(
        actor=actor,
        events=(
            {
                "event": "notification_delivery_failed",
                "record_id": run.pk,
                "record_label": run.failure_type,
            },
            {
                "event": "automation_run_failed",
                "record_id": run.pk,
                "record_label": f"{schedule} KPI automation",
            },
        ),
    )
    logger.error(
        "KPI CRM-only automation failed after bounded retries",
        extra={
            "run_id": run.pk,
            "schedule": schedule,
            "retry_count": run.retry_count,
            "failure_type": run.failure_type,
        },
    )
    return run
