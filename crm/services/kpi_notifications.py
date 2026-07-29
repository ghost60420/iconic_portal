import hashlib
import json
from dataclasses import dataclass
from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags

from crm.models import AutomationNotification, CRMAuditLog
from crm.models_kpi_notifications import (
    KPINotificationEvent,
    KPINotificationRule,
    KPI_NOTIFICATION_EVENT_TYPES,
    KPI_NOTIFICATION_SEVERITIES,
)
from crm.services.kpi_dashboard import AUDIENCE_EXECUTIVE, dashboard_audience


KPI_NOTIFICATION_VERSION = "kpi-notification/1.0"
KPI_NOTIFICATION_SOURCE_PREFIX = "kpi:"
KPI_NOTIFICATION_AUDIT_MODULE = "kpi_notifications"


class KPINotificationError(Exception):
    pass


class KPINotificationPermissionError(KPINotificationError):
    pass


@dataclass(frozen=True, slots=True)
class KPINotificationRequest:
    notification_rule: KPINotificationRule
    escalation_rule: object
    recipient: object
    notification_type: str
    severity: str
    title: str
    message: str
    action_link: str
    source_event: str
    source_record_type: str
    source_record_id: str
    source_period: str
    source_state: str
    due_date: date | None = None
    related_employee: object | None = None
    related_manager: object | None = None
    related_review: object | None = None
    related_department_code: str = ""
    related_department_name: str = ""


def _clean(value, limit):
    return " ".join(strip_tags(str(value or "")).split())[:limit]


def _audit_rows(*, actor, events):
    now_actor = actor if actor and getattr(actor, "is_authenticated", False) else None
    return [
        CRMAuditLog(
            actor=now_actor,
            module=KPI_NOTIFICATION_AUDIT_MODULE,
            record_id=str(event.get("record_id", "automation"))[:64],
            record_label=_clean(event.get("record_label", "KPI notification"), 220),
            action_type=CRMAuditLog.ACTION_UPDATED,
            field_name=_clean(event["event"], 100),
            previous_value="",
            new_value=_clean(event["event"], 200),
            target_url=str(event.get("target_url", ""))[:300],
        )
        for event in events
    ]


def record_kpi_notification_audits(*, actor=None, events=()):
    rows = _audit_rows(actor=actor, events=events)
    if rows:
        CRMAuditLog.objects.bulk_create(rows)
    return rows


def effective_notification_rule(as_of, *, code=None, required=True):
    queryset = KPINotificationRule.objects.filter(
        status=KPINotificationRule.STATUS_PUBLISHED,
        effective_start__lte=as_of,
    ).filter(
        Q(effective_end__isnull=True) | Q(effective_end__gte=as_of)
    )
    if code:
        queryset = queryset.filter(code=code)
    rows = list(
        queryset.prefetch_related("escalation_rules").order_by("code", "-version")[:2]
    )
    if not rows and not required:
        return None
    if len(rows) != 1:
        raise KPINotificationError(
            "Exactly one effective published KPI notification rule is required; "
            f"found {len(rows)}."
        )
    return rows[0]


@transaction.atomic
def publish_notification_rule(rule, *, actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise KPINotificationPermissionError("An authenticated publisher is required.")
    if dashboard_audience(actor) != AUDIENCE_EXECUTIVE:
        raise KPINotificationPermissionError(
            "Only CEO or Super Admin may publish KPI notification rules."
        )
    locked = (
        KPINotificationRule.objects.select_for_update()
        .prefetch_related("escalation_rules")
        .get(pk=rule.pk)
    )
    if locked.status != KPINotificationRule.STATUS_DRAFT:
        raise KPINotificationError("Only Draft notification rules can be published.")
    configured_types = {
        escalation.event_type
        for escalation in locked.escalation_rules.all()
        if escalation.is_active
    }
    missing = set(locked.enabled_event_types) - configured_types
    if missing:
        raise KPINotificationError(
            "Every enabled notification type requires an active escalation rule: "
            + ", ".join(sorted(missing))
        )
    locked.status = KPINotificationRule.STATUS_PUBLISHED
    locked.published_by = actor
    locked.published_at = timezone.now()
    locked.save(
        update_fields=("status", "published_by", "published_at", "updated_at")
    )
    record_kpi_notification_audits(
        actor=actor,
        events=(
            {
                "event": "notification_rule_changed",
                "record_id": locked.pk,
                "record_label": f"{locked.name} v{locked.version}",
            },
        ),
    )
    return locked


def _state_hash(value):
    return hashlib.sha256(_clean(value, 2000).encode("utf-8")).hexdigest()


def notification_deduplication_key(request):
    payload = {
        "type": request.notification_type,
        "recipient": request.recipient.pk,
        "source_event": request.source_event,
        "source_type": request.source_record_type,
        "source_id": str(request.source_record_id),
        "period": request.source_period,
        "severity": request.severity,
        "due_date": request.due_date.isoformat() if request.due_date else "",
        "rule": request.notification_rule.code,
        "rule_version": request.notification_rule.version,
        "stage": getattr(request.escalation_rule, "stage", 0),
        "state": _state_hash(request.source_state),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _priority(severity):
    if severity == "critical":
        return "critical"
    if severity == "high":
        return "high"
    if severity == "information":
        return "information"
    return "normal"


def _validate_request(request):
    if request.notification_type not in KPI_NOTIFICATION_EVENT_TYPES:
        raise KPINotificationError("Unsupported KPI notification type.")
    if request.severity not in KPI_NOTIFICATION_SEVERITIES:
        raise KPINotificationError("Unsupported KPI notification severity.")
    if not request.recipient or not request.recipient.is_active:
        raise KPINotificationError("KPI notification recipient must be active.")
    if request.notification_type not in request.notification_rule.enabled_event_types:
        raise KPINotificationError(
            "The effective rule does not enable this notification type."
        )
    if (
        request.escalation_rule
        and request.escalation_rule.notification_rule_id
        != request.notification_rule.pk
    ):
        raise KPINotificationError("Escalation rule does not match the policy version.")


@transaction.atomic
def create_kpi_notifications(requests, *, automation_run=None, actor=None):
    normalized = {}
    for request in requests:
        _validate_request(request)
        key = notification_deduplication_key(request)
        normalized.setdefault(key, request)
    if not normalized:
        return {"created": 0, "duplicates": 0, "events": ()}

    keys = tuple(normalized)
    existing = set(
        KPINotificationEvent.service_objects.filter(
            deduplication_key__in=keys
        ).values_list("deduplication_key", flat=True)
    )
    new_keys = [key for key in keys if key not in existing]
    duplicate_keys = [key for key in keys if key in existing]

    review_content_type = None
    if any(normalized[key].related_review for key in new_keys):
        from crm.models_kpi_reviews import KPIReview

        review_content_type = ContentType.objects.get_for_model(
            KPIReview,
            for_concrete_model=False,
        )

    notification_rows = []
    batch_size = min(
        (
            normalized[key].notification_rule.batch_size
            for key in new_keys
        ),
        default=500,
    )
    for key in new_keys:
        request = normalized[key]
        review = request.related_review
        notification_rows.append(
            AutomationNotification(
                source_key=f"{KPI_NOTIFICATION_SOURCE_PREFIX}{key}",
                rule_type="general",
                notification_type="general",
                title=_clean(request.title, 220),
                message=_clean(request.message, 1000),
                priority=_priority(request.severity),
                record_content_type=review_content_type if review else None,
                record_object_id=review.pk if review else None,
                record_label=_clean(
                    request.related_department_name
                    or getattr(request.related_employee, "public_name", "")
                    or request.source_record_type,
                    220,
                ),
                target_url=str(request.action_link or "")[:300],
                assigned_user=request.recipient,
                assigned_role="",
                due_date=request.due_date,
            )
        )
    if notification_rows:
        AutomationNotification.objects.bulk_create(
            notification_rows,
            ignore_conflicts=True,
            batch_size=batch_size,
        )

    notification_by_key = {
        row.source_key.removeprefix(KPI_NOTIFICATION_SOURCE_PREFIX): row
        for row in AutomationNotification.objects.filter(
            source_key__in=[
                f"{KPI_NOTIFICATION_SOURCE_PREFIX}{key}" for key in new_keys
            ]
        )
    }
    event_rows = []
    for key in new_keys:
        request = normalized[key]
        notification = notification_by_key.get(key)
        if notification is None:
            continue
        event_rows.append(
            KPINotificationEvent(
                notification_rule=request.notification_rule,
                escalation_rule=request.escalation_rule,
                automation_notification=notification,
                automation_run=automation_run,
                notification_type=request.notification_type,
                recipient=request.recipient,
                related_employee=request.related_employee,
                related_manager=request.related_manager,
                related_review=request.related_review,
                related_department_code=_clean(
                    request.related_department_code,
                    80,
                ),
                related_department_name=_clean(
                    request.related_department_name,
                    140,
                ),
                severity=request.severity,
                title=_clean(request.title, 220),
                message=_clean(request.message, 1000),
                action_link=str(request.action_link or "")[:300],
                due_date=request.due_date,
                notification_version=KPI_NOTIFICATION_VERSION,
                deduplication_key=key,
                source_event=_clean(request.source_event, 120),
                source_record_type=_clean(request.source_record_type, 80),
                source_record_id=_clean(request.source_record_id, 80),
                source_period=_clean(request.source_period, 80),
                source_state=_state_hash(request.source_state),
            )
        )
    if event_rows:
        KPINotificationEvent.service_objects.bulk_create(
            event_rows,
            ignore_conflicts=True,
            batch_size=batch_size,
        )

    created_events = tuple(
        KPINotificationEvent.service_objects.filter(
            deduplication_key__in=new_keys
        ).select_related("automation_notification", "escalation_rule")
    )
    created_keys = {event.deduplication_key for event in created_events}
    race_duplicates = set(new_keys) - created_keys
    duplicate_keys.extend(race_duplicates)
    for user_id in {
        normalized[key].recipient.pk
        for key in created_keys
        if key in normalized
    }:
        cache.delete(f"crm-header-unread:{user_id}")

    audit_events = [
        {
            "event": "notification_created",
            "record_id": event.pk,
            "record_label": event.notification_type,
            "target_url": event.action_link,
        }
        for event in created_events
    ]
    audit_events.extend(
        {
            "event": "notification_escalated",
            "record_id": event.pk,
            "record_label": event.notification_type,
            "target_url": event.action_link,
        }
        for event in created_events
        if event.escalation_rule_id and event.escalation_rule.stage > 0
    )
    audit_events.extend(
        {
            "event": "duplicate_notification_blocked",
            "record_id": key[:64],
            "record_label": normalized[key].notification_type,
        }
        for key in duplicate_keys
        if key in normalized
    )
    record_kpi_notification_audits(actor=actor, events=audit_events)
    return {
        "created": len(created_events),
        "duplicates": len(duplicate_keys),
        "events": created_events,
    }


def visible_kpi_notification_events(user, *, include_dismissed=False):
    queryset = KPINotificationEvent.service_objects.select_related(
        "automation_notification",
        "notification_rule",
        "escalation_rule",
        "related_employee__user",
        "related_manager",
        "related_review",
    )
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()
    queryset = queryset.filter(recipient=user)
    if not include_dismissed:
        queryset = queryset.filter(dismissed_at__isnull=True)
    return queryset


def visible_kpi_notification_history(user):
    if not user or not getattr(user, "is_authenticated", False):
        return AutomationNotification.objects.none()
    return AutomationNotification.objects.filter(kpi_event__recipient=user)


def record_kpi_notifications_viewed(user, notification_ids):
    event_ids = list(
        visible_kpi_notification_events(user, include_dismissed=True)
        .filter(automation_notification_id__in=notification_ids)
        .values_list("pk", flat=True)
    )
    record_kpi_notification_audits(
        actor=user,
        events=(
            {
                "event": "notification_viewed",
                "record_id": event_id,
                "record_label": "KPI notification",
            }
            for event_id in event_ids
        ),
    )


def record_kpi_notifications_read(user, notification_ids):
    event_ids = list(
        visible_kpi_notification_events(user, include_dismissed=True)
        .filter(automation_notification_id__in=notification_ids)
        .values_list("pk", flat=True)
    )
    record_kpi_notification_audits(
        actor=user,
        events=(
            {
                "event": "notification_marked_read",
                "record_id": event_id,
                "record_label": "KPI notification",
            }
            for event_id in event_ids
        ),
    )


@transaction.atomic
def dismiss_kpi_notification(event, *, actor):
    locked = (
        KPINotificationEvent.service_objects.select_for_update()
        .select_related("automation_notification")
        .filter(pk=getattr(event, "pk", event), recipient=actor)
        .first()
    )
    if locked is None:
        raise KPINotificationPermissionError(
            "This KPI notification is not available."
        )
    if locked.dismissed_at:
        return locked
    now = timezone.now()
    locked.dismissed_at = now
    locked.dismissed_by = actor
    locked.save(
        service_dismiss=True,
        update_fields=("dismissed_at", "dismissed_by"),
    )
    notification = locked.automation_notification
    notification.is_resolved = True
    notification.resolved_at = now
    notification.save(update_fields=("is_resolved", "resolved_at", "updated_at"))
    cache.delete(f"crm-header-unread:{actor.pk}")
    record_kpi_notification_audits(
        actor=actor,
        events=(
            {
                "event": "notification_dismissed",
                "record_id": locked.pk,
                "record_label": locked.notification_type,
            },
        ),
    )
    return locked
