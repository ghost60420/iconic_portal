from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from crm.kpi_policy_defaults import (
    DEFAULT_GREEN_MAX,
    DEFAULT_GREEN_MIN,
    DEFAULT_NOTIFICATION_ESCALATIONS,
    DEFAULT_NOTIFICATION_EVENTS,
    DEFAULT_RED_MAX,
    DEFAULT_RED_MIN,
    DEFAULT_YELLOW_MAX,
    DEFAULT_YELLOW_MIN,
    ROLE_TEMPLATE_DEFINITIONS,
)
from crm.models import CRMAuditLog
from crm.models_employee import EmployeeProfile
from crm.models_kpi import (
    KPIItemDefinition,
    KPIRoleTemplate,
    KPISettings,
    KPITemplateVersion,
)
from crm.models_kpi_assignments import EmployeeKPIRoleAssignment
from crm.models_kpi_bonus import KPIBonusRuleSet, KPIBonusWeightProfile
from crm.models_kpi_intelligence import KPIIntelligenceRuleSet
from crm.models_kpi_notifications import (
    KPIEscalationRule,
    KPINotificationRule,
)
from crm.models_kpi_release import KPIPolicyApproval
from crm.services.kpi_assignments import assign_kpi_roles, update_assignments
from crm.services.kpi_dashboard import AUDIENCE_EXECUTIVE, dashboard_audience
from crm.services.kpi_intelligence import publish_intelligence_rule_set
from crm.services.kpi_notifications import publish_notification_rule


class KPIReleaseError(Exception):
    pass


class KPIReleasePermissionError(KPIReleaseError):
    pass


@dataclass(frozen=True, slots=True)
class KPIReleaseDraftSet:
    template_versions: tuple
    kpi_settings: KPISettings
    bonus_weight_profile: KPIBonusWeightProfile
    bonus_rule_set: KPIBonusRuleSet
    intelligence_rule_set: KPIIntelligenceRuleSet
    notification_rule: KPINotificationRule
    approvals: tuple


@dataclass(frozen=True, slots=True)
class KPIAssignmentPreview:
    employee: EmployeeProfile
    start_date: date
    total_weight: Decimal
    roles: tuple


POLICY_TARGETS = (
    (
        KPITemplateVersion,
        KPIPolicyApproval.TYPE_TEMPLATE,
        "template_version",
    ),
    (
        KPISettings,
        KPIPolicyApproval.TYPE_SETTINGS,
        "kpi_settings",
    ),
    (
        KPIBonusWeightProfile,
        KPIPolicyApproval.TYPE_BONUS_WEIGHT,
        "bonus_weight_profile",
    ),
    (
        KPIBonusRuleSet,
        KPIPolicyApproval.TYPE_BONUS_RULE,
        "bonus_rule_set",
    ),
    (
        KPIIntelligenceRuleSet,
        KPIPolicyApproval.TYPE_INTELLIGENCE,
        "intelligence_rule_set",
    ),
    (
        KPINotificationRule,
        KPIPolicyApproval.TYPE_NOTIFICATION,
        "notification_rule",
    ),
)


def _require_authenticated(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise KPIReleasePermissionError("An authenticated user is required.")
    return actor


def _require_executive(actor):
    actor = _require_authenticated(actor)
    if dashboard_audience(actor) != AUDIENCE_EXECUTIVE:
        raise KPIReleasePermissionError(
            "Only CEO or Super Admin may administer KPI release policies."
        )
    return actor


def _audit(*, actor, event, approval, reason=""):
    CRMAuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        module="kpi_release",
        record_id=str(approval.pk),
        record_label=approval.target_label[:220],
        action_type=CRMAuditLog.ACTION_UPDATED,
        field_name=event[:100],
        previous_value="",
        new_value=event[:200],
        target_url="",
    )


def _target_metadata(target):
    for model, policy_type, field in POLICY_TARGETS:
        if isinstance(target, model):
            return policy_type, field
    raise KPIReleaseError("Unsupported KPI policy target.")


def _approval_for_target(target, *, lock=False):
    policy_type, field = _target_metadata(target)
    queryset = KPIPolicyApproval.service_objects.select_related(
        *KPIPolicyApproval.TARGET_FIELDS.values()
    )
    if lock:
        queryset = queryset.select_for_update()
    approval = queryset.filter(**{field: target}).first()
    if approval is None:
        raise KPIReleaseError(f"{policy_type} has no release approval record.")
    return approval


def create_policy_approval(target, *, actor):
    actor = _require_executive(actor)
    policy_type, field = _target_metadata(target)
    existing = KPIPolicyApproval.service_objects.filter(**{field: target}).first()
    if existing:
        return existing
    approval = KPIPolicyApproval(
        policy_type=policy_type,
        created_by=actor,
        **{field: target},
    )
    approval.save(service_create=True)
    _audit(
        actor=actor,
        event="policy_draft_created",
        approval=approval,
    )
    return approval


def _clean_reason(reason, *, required):
    reason = " ".join(str(reason or "").split())
    if required and not reason:
        raise ValidationError({"reason": "A written reason is required."})
    return reason


@transaction.atomic
def submit_policy_for_review(approval, *, actor, reason):
    actor = _require_authenticated(actor)
    locked = _resolve_approval(approval, lock=True)
    if locked.status != KPIPolicyApproval.STATUS_DRAFT:
        raise KPIReleaseError("Only Draft policies can be submitted.")
    if actor.pk != locked.created_by_id and dashboard_audience(actor) != (
        AUDIENCE_EXECUTIVE
    ):
        raise KPIReleasePermissionError(
            "Only the creator, CEO, or Super Admin may submit this policy."
        )
    locked.status = KPIPolicyApproval.STATUS_UNDER_REVIEW
    locked.submitted_by = actor
    locked.submitted_at = timezone.now()
    locked.review_reason = _clean_reason(reason, required=True)
    locked.save(
        service_transition=True,
        update_fields=(
            "status",
            "submitted_by",
            "submitted_at",
            "review_reason",
            "updated_at",
        ),
    )
    _audit(actor=actor, event="policy_submitted", approval=locked, reason=reason)
    return locked


@transaction.atomic
def approve_policy(approval, *, actor, reason):
    actor = _require_executive(actor)
    locked = _resolve_approval(approval, lock=True)
    if locked.status != KPIPolicyApproval.STATUS_UNDER_REVIEW:
        raise KPIReleaseError("Only policies Under Review can be approved.")
    locked.status = KPIPolicyApproval.STATUS_APPROVED
    locked.approved_by = actor
    locked.approved_at = timezone.now()
    locked.approval_reason = _clean_reason(reason, required=True)
    locked.save(
        service_transition=True,
        update_fields=(
            "status",
            "approved_by",
            "approved_at",
            "approval_reason",
            "updated_at",
        ),
    )
    _audit(actor=actor, event="policy_approved", approval=locked, reason=reason)
    return locked


def _resolve_approval(approval, *, lock=False):
    approval_id = getattr(approval, "pk", approval)
    queryset = KPIPolicyApproval.service_objects.select_related(
        *KPIPolicyApproval.TARGET_FIELDS.values()
    )
    if lock:
        queryset = queryset.select_for_update()
    resolved = queryset.filter(pk=approval_id).first()
    if resolved is None:
        raise KPIReleaseError("The selected policy approval does not exist.")
    return resolved


def _publish_target(approval, actor):
    target = approval.target_object
    if approval.policy_type == KPIPolicyApproval.TYPE_TEMPLATE:
        target.status = KPITemplateVersion.STATUS_PUBLISHED
        target.published_by = actor
        target.save()
    elif approval.policy_type == KPIPolicyApproval.TYPE_SETTINGS:
        KPISettings.objects.filter(is_active=True).exclude(pk=target.pk).update(
            is_active=False
        )
        target.is_active = True
        target.save(policy_service=True, update_fields=("is_active", "updated_at"))
    elif approval.policy_type == KPIPolicyApproval.TYPE_BONUS_WEIGHT:
        target.status = KPIBonusWeightProfile.STATUS_PUBLISHED
        target.published_by = actor
        target.save()
    elif approval.policy_type == KPIPolicyApproval.TYPE_BONUS_RULE:
        weight_approval = _approval_for_target(target.weight_profile)
        if weight_approval.status != KPIPolicyApproval.STATUS_PUBLISHED:
            raise KPIReleaseError(
                "Publish the approved bonus weight profile before its rule set."
            )
        target.status = KPIBonusRuleSet.STATUS_PUBLISHED
        target.published_by = actor
        target.save()
    elif approval.policy_type == KPIPolicyApproval.TYPE_INTELLIGENCE:
        publish_intelligence_rule_set(target, actor=actor)
    elif approval.policy_type == KPIPolicyApproval.TYPE_NOTIFICATION:
        publish_notification_rule(target, actor=actor)
    else:
        raise KPIReleaseError("Unsupported KPI policy target.")


def _effective_overlap(queryset, target):
    target_start = getattr(target, "effective_start", None)
    target_end = getattr(target, "effective_end", None)
    if target_start is None:
        return queryset
    if target_end is not None:
        queryset = queryset.filter(effective_start__lte=target_end)
    return queryset.filter(
        Q(effective_end__isnull=True) | Q(effective_end__gte=target_start)
    )


def _publication_conflicts(approval):
    target = approval.target_object
    if approval.policy_type == KPIPolicyApproval.TYPE_TEMPLATE:
        queryset = KPITemplateVersion.objects.filter(
            template=target.template,
            status=KPITemplateVersion.STATUS_PUBLISHED,
        ).exclude(pk=target.pk)
        return _effective_overlap(queryset, target)
    if approval.policy_type == KPIPolicyApproval.TYPE_SETTINGS:
        return KPISettings.objects.filter(is_active=True).exclude(pk=target.pk)
    if approval.policy_type == KPIPolicyApproval.TYPE_BONUS_WEIGHT:
        queryset = KPIBonusWeightProfile.objects.filter(
            code=target.code,
            status=KPIBonusWeightProfile.STATUS_PUBLISHED,
        ).exclude(pk=target.pk)
        return _effective_overlap(queryset, target)
    if approval.policy_type == KPIPolicyApproval.TYPE_BONUS_RULE:
        queryset = KPIBonusRuleSet.objects.filter(
            status=KPIBonusRuleSet.STATUS_PUBLISHED,
        ).exclude(pk=target.pk)
        return _effective_overlap(queryset, target)
    if approval.policy_type == KPIPolicyApproval.TYPE_INTELLIGENCE:
        queryset = KPIIntelligenceRuleSet.objects.filter(
            status=KPIIntelligenceRuleSet.STATUS_PUBLISHED,
        ).exclude(pk=target.pk)
        return _effective_overlap(queryset, target)
    if approval.policy_type == KPIPolicyApproval.TYPE_NOTIFICATION:
        queryset = KPINotificationRule.objects.filter(
            status=KPINotificationRule.STATUS_PUBLISHED,
        ).exclude(pk=target.pk)
        return _effective_overlap(queryset, target)
    raise KPIReleaseError("Unsupported KPI policy target.")


@transaction.atomic
def publish_policy(approval, *, actor):
    actor = _require_executive(actor)
    locked = _resolve_approval(approval, lock=True)
    if locked.status != KPIPolicyApproval.STATUS_APPROVED:
        raise KPIReleaseError("Only Approved policies can be published.")
    if _publication_conflicts(locked).select_for_update().exists():
        raise KPIReleaseError(
            "Retire the overlapping published policy before publishing "
            "this version."
        )
    _publish_target(locked, actor)
    locked.status = KPIPolicyApproval.STATUS_PUBLISHED
    locked.published_by = actor
    locked.published_at = timezone.now()
    locked.save(
        service_transition=True,
        update_fields=(
            "status",
            "published_by",
            "published_at",
            "updated_at",
        ),
    )
    _audit(actor=actor, event="policy_published", approval=locked)
    return locked


def _retire_target(approval):
    target = approval.target_object
    if approval.policy_type == KPIPolicyApproval.TYPE_TEMPLATE:
        target.status = KPITemplateVersion.STATUS_RETIRED
        target.save(update_fields=("status", "updated_at"))
    elif approval.policy_type == KPIPolicyApproval.TYPE_SETTINGS:
        target.is_active = False
        target.save(policy_service=True, update_fields=("is_active", "updated_at"))
    elif approval.policy_type == KPIPolicyApproval.TYPE_BONUS_WEIGHT:
        if target.rule_sets.filter(status=KPIBonusRuleSet.STATUS_PUBLISHED).exists():
            raise KPIReleaseError("Retire published bonus rules before their weights.")
        target.status = KPIBonusWeightProfile.STATUS_RETIRED
        target.save(update_fields=("status", "updated_at"))
    elif approval.policy_type == KPIPolicyApproval.TYPE_BONUS_RULE:
        target.status = KPIBonusRuleSet.STATUS_RETIRED
        target.save(update_fields=("status", "updated_at"))
    elif approval.policy_type == KPIPolicyApproval.TYPE_INTELLIGENCE:
        target.status = KPIIntelligenceRuleSet.STATUS_RETIRED
        target.save(update_fields=("status", "updated_at"))
    elif approval.policy_type == KPIPolicyApproval.TYPE_NOTIFICATION:
        target.status = KPINotificationRule.STATUS_RETIRED
        target.save(update_fields=("status", "updated_at"))


@transaction.atomic
def retire_policy(approval, *, actor, reason):
    actor = _require_executive(actor)
    locked = _resolve_approval(approval, lock=True)
    if locked.status != KPIPolicyApproval.STATUS_PUBLISHED:
        raise KPIReleaseError("Only Published policies can be retired.")
    _retire_target(locked)
    locked.status = KPIPolicyApproval.STATUS_RETIRED
    locked.retired_by = actor
    locked.retired_at = timezone.now()
    locked.retirement_reason = _clean_reason(reason, required=True)
    locked.save(
        service_transition=True,
        update_fields=(
            "status",
            "retired_by",
            "retired_at",
            "retirement_reason",
            "updated_at",
        ),
    )
    _audit(actor=actor, event="policy_retired", approval=locked, reason=reason)
    return locked


def _next_version(model, *, code=None, template=None):
    queryset = model.objects.all()
    if code is not None:
        queryset = queryset.filter(code=code)
    if template is not None:
        queryset = queryset.filter(template=template)
    current = queryset.order_by("-version").values_list("version", flat=True).first()
    return (current or 0) + 1


@transaction.atomic
def create_policy_successor(approval, *, effective_date, actor):
    actor = _require_executive(actor)
    if not isinstance(effective_date, date):
        raise ValidationError({"effective_date": "Enter a valid effective date."})
    locked = _resolve_approval(approval, lock=True)
    if locked.status not in {
        KPIPolicyApproval.STATUS_PUBLISHED,
        KPIPolicyApproval.STATUS_RETIRED,
    }:
        raise KPIReleaseError(
            "Only Published or Retired policies can create a successor version."
        )
    target = locked.target_object
    if getattr(target, "effective_start", None) and (
        effective_date <= target.effective_start
    ):
        raise ValidationError(
            {"effective_date": "A successor must begin after its source policy."}
        )

    if locked.policy_type == KPIPolicyApproval.TYPE_TEMPLATE:
        successor = KPITemplateVersion.objects.create(
            template=target.template,
            version=_next_version(
                KPITemplateVersion,
                template=target.template,
            ),
            effective_start=effective_date,
            notes=f"Draft successor to template version {target.version}.",
            created_by=actor,
        )
        item_fields = (
            "name",
            "description",
            "purpose",
            "measurement_method",
            "target",
            "weight",
            "review_frequency",
            "data_source",
            "green_min",
            "green_max",
            "yellow_min",
            "yellow_max",
            "red_min",
            "red_max",
            "manager_approval_required",
            "evidence_required",
            "bonus_eligible",
            "critical_failure_rule",
            "is_active",
            "sort_order",
        )
        for item in target.items.order_by("sort_order", "pk"):
            KPIItemDefinition.objects.create(
                template_version=successor,
                **{field: getattr(item, field) for field in item_fields},
            )
    elif locked.policy_type == KPIPolicyApproval.TYPE_SETTINGS:
        successor = KPISettings.objects.create(
            version=_next_version(KPISettings),
            is_active=False,
            green_min=target.green_min,
            green_max=target.green_max,
            yellow_min=target.yellow_min,
            yellow_max=target.yellow_max,
            red_min=target.red_min,
            red_max=target.red_max,
            individual_bonus_weight=target.individual_bonus_weight,
            team_bonus_weight=target.team_bonus_weight,
            company_bonus_weight=target.company_bonus_weight,
            notes=f"Draft successor to KPI settings version {target.version}.",
            created_by=actor,
        )
    elif locked.policy_type == KPIPolicyApproval.TYPE_BONUS_WEIGHT:
        successor = KPIBonusWeightProfile.objects.create(
            code=target.code,
            name=target.name,
            version=_next_version(
                KPIBonusWeightProfile,
                code=target.code,
            ),
            team_scope_code=target.team_scope_code,
            team_scope_name=target.team_scope_name,
            individual_weight=target.individual_weight,
            team_weight=target.team_weight,
            company_weight=target.company_weight,
            effective_start=effective_date,
            created_by=actor,
        )
    elif locked.policy_type == KPIPolicyApproval.TYPE_BONUS_RULE:
        successor = KPIBonusRuleSet.objects.create(
            code=target.code,
            name=target.name,
            version=_next_version(KPIBonusRuleSet, code=target.code),
            weight_profile=target.weight_profile,
            bonus_enabled=target.bonus_enabled,
            minimum_score=target.minimum_score,
            attendance_multiplier_default=target.attendance_multiplier_default,
            attendance_multiplier_min=target.attendance_multiplier_min,
            attendance_multiplier_max=target.attendance_multiplier_max,
            critical_red_behavior=target.critical_red_behavior,
            bonus_floor=target.bonus_floor,
            bonus_cap=target.bonus_cap,
            currency=target.currency,
            eligible_employee_statuses=list(target.eligible_employee_statuses),
            approval_required=target.approval_required,
            effective_start=effective_date,
            created_by=actor,
        )
    elif locked.policy_type == KPIPolicyApproval.TYPE_INTELLIGENCE:
        intelligence_fields = (
            "name",
            "minimum_score",
            "health_green_threshold",
            "critical_red_count",
            "decline_percentage",
            "trend_periods",
            "overdue_days",
            "review_completion_target",
            "improvement_threshold",
            "department_risk_threshold",
            "manager_workload_threshold",
            "overall_kpi_weight",
            "review_completion_weight",
            "bonus_readiness_weight",
            "improvement_weight",
            "risk_control_weight",
            "critical_alert_severity",
            "red_alert_severity",
            "yellow_alert_severity",
        )
        successor = KPIIntelligenceRuleSet.objects.create(
            code=target.code,
            version=_next_version(
                KPIIntelligenceRuleSet,
                code=target.code,
            ),
            effective_start=effective_date,
            created_by=actor,
            **{field: getattr(target, field) for field in intelligence_fields},
        )
    elif locked.policy_type == KPIPolicyApproval.TYPE_NOTIFICATION:
        successor = KPINotificationRule.objects.create(
            code=target.code,
            name=target.name,
            version=_next_version(KPINotificationRule, code=target.code),
            effective_start=effective_date,
            enabled_event_types=list(target.enabled_event_types),
            reminder_offsets=list(target.reminder_offsets),
            improvement_reminder_days=list(target.improvement_reminder_days),
            enabled_schedules=list(target.enabled_schedules),
            positive_recognition_enabled=target.positive_recognition_enabled,
            retry_limit=target.retry_limit,
            batch_size=target.batch_size,
            source_lookback_days=target.source_lookback_days,
            created_by=actor,
        )
        escalation_fields = (
            "event_type",
            "stage",
            "trigger_offset_days",
            "severity",
            "recipient_scopes",
            "is_active",
        )
        for escalation in target.escalation_rules.order_by("event_type", "stage"):
            values = {
                field: getattr(escalation, field)
                for field in escalation_fields
            }
            values["recipient_scopes"] = list(values["recipient_scopes"])
            KPIEscalationRule.objects.create(
                notification_rule=successor,
                **values,
            )
    else:
        raise KPIReleaseError("Unsupported KPI policy target.")
    return create_policy_approval(successor, actor=actor)


def _ensure_exact(instance, expected, fields, label):
    mismatches = [
        field
        for field in fields
        if getattr(instance, field) != expected[field]
    ]
    if mismatches:
        raise KPIReleaseError(
            f"Existing {label} differs from the approved draft definition: "
            + ", ".join(mismatches)
        )


def _ensure_draft_status(instance, draft_status, label):
    if instance.status != draft_status:
        raise KPIReleaseError(f"{label} is not a Draft record.")


def _template_item_defaults(role_name, item_name, weight, sort_order):
    return {
        "description": (
            f"Measures {item_name.lower()} against the approved {role_name} target."
        ),
        "purpose": (
            f"Keep {role_name} performance visible, consistent, and reviewable."
        ),
        "measurement_method": "manual score",
        "target": "100",
        "weight": Decimal(str(weight)),
        "review_frequency": KPIItemDefinition.FREQUENCY_MONTHLY,
        "data_source": "Approved Iconic CRM records and manager-verified evidence",
        "green_min": DEFAULT_GREEN_MIN,
        "green_max": DEFAULT_GREEN_MAX,
        "yellow_min": DEFAULT_YELLOW_MIN,
        "yellow_max": DEFAULT_YELLOW_MAX,
        "red_min": DEFAULT_RED_MIN,
        "red_max": DEFAULT_RED_MAX,
        "manager_approval_required": True,
        "evidence_required": True,
        "bonus_eligible": True,
        "critical_failure_rule": (
            "Confirmed false information, self-approval, serious safety, privacy, "
            "financial-control, or required-record breach forces Critical Red."
        ),
        "is_active": True,
        "sort_order": sort_order,
    }


@transaction.atomic
def prepare_release_drafts(*, effective_date, currency, actor):
    actor = _require_executive(actor)
    if not isinstance(effective_date, date):
        raise ValidationError({"effective_date": "Enter a valid effective date."})
    currency = str(currency or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValidationError({"currency": "Use a three-letter currency code."})

    template_versions = []
    for code, name, item_rows in ROLE_TEMPLATE_DEFINITIONS:
        template, _created = KPIRoleTemplate.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "description": f"Draft KPI role template for {name}.",
                "is_active": True,
                "created_by": actor,
            },
        )
        if template.name != name:
            raise KPIReleaseError(f"Template code {code} belongs to another role.")
        version, _created = KPITemplateVersion.objects.get_or_create(
            template=template,
            version=1,
            defaults={
                "effective_start": effective_date,
                "notes": "Stage 10 draft. Requires review and executive approval.",
                "created_by": actor,
            },
        )
        if version.status != KPITemplateVersion.STATUS_DRAFT:
            raise KPIReleaseError(f"{version} is not a Draft version.")
        if version.effective_start != effective_date:
            raise KPIReleaseError(f"{version} uses another effective date.")
        expected_names = set()
        for sort_order, (item_name, weight) in enumerate(item_rows, start=1):
            defaults = _template_item_defaults(
                name,
                item_name,
                weight,
                sort_order,
            )
            item, created = KPIItemDefinition.objects.get_or_create(
                template_version=version,
                name=item_name,
                defaults=defaults,
            )
            if not created:
                _ensure_exact(
                    item,
                    defaults,
                    tuple(defaults),
                    f"{version} item {item_name}",
                )
            expected_names.add(item_name)
        unexpected = version.items.exclude(name__in=expected_names)
        if unexpected.exists():
            raise KPIReleaseError(f"{version} contains unexpected KPI items.")
        if version.active_weight_total != Decimal("100.00"):
            raise KPIReleaseError(f"{version} weights do not total 100.00.")
        template_versions.append(version)

    settings_defaults = {
        "is_active": False,
        "green_min": DEFAULT_GREEN_MIN,
        "green_max": DEFAULT_GREEN_MAX,
        "yellow_min": DEFAULT_YELLOW_MIN,
        "yellow_max": DEFAULT_YELLOW_MAX,
        "red_min": DEFAULT_RED_MIN,
        "red_max": DEFAULT_RED_MAX,
        "individual_bonus_weight": Decimal("60.00"),
        "team_bonus_weight": Decimal("25.00"),
        "company_bonus_weight": Decimal("15.00"),
        "notes": "Stage 10 draft. Critical Red overrides displayed status.",
        "created_by": actor,
    }
    settings, settings_created = KPISettings.objects.get_or_create(
        version=1,
        defaults=settings_defaults,
    )
    if not settings_created:
        _ensure_exact(
            settings,
            settings_defaults,
            tuple(settings_defaults),
            "KPI settings version 1",
        )

    weight_defaults = {
        "name": "Iconic Default Bonus Weights",
        "team_scope_code": "configured-team",
        "team_scope_name": "Configured Team",
        "individual_weight": Decimal("60.00"),
        "team_weight": Decimal("25.00"),
        "company_weight": Decimal("15.00"),
        "effective_start": effective_date,
        "created_by": actor,
    }
    weight_profile, weight_created = KPIBonusWeightProfile.objects.get_or_create(
        code="iconic-default",
        version=1,
        defaults=weight_defaults,
    )
    _ensure_draft_status(
        weight_profile,
        KPIBonusWeightProfile.STATUS_DRAFT,
        "Bonus weight profile",
    )
    if not weight_created:
        _ensure_exact(
            weight_profile,
            weight_defaults,
            tuple(weight_defaults),
            "bonus weight profile",
        )

    bonus_defaults = {
        "name": "Iconic Default KPI Bonus Rule",
        "weight_profile": weight_profile,
        "bonus_enabled": True,
        "minimum_score": Decimal("70.00"),
        "attendance_multiplier_default": Decimal("1.0000"),
        "attendance_multiplier_min": Decimal("0.0000"),
        "attendance_multiplier_max": Decimal("1.2000"),
        "critical_red_behavior": KPIBonusRuleSet.CRITICAL_BLOCK,
        "bonus_floor": Decimal("0.00"),
        "bonus_cap": None,
        "currency": currency,
        "eligible_employee_statuses": [EmployeeProfile.STATUS_ACTIVE],
        "approval_required": True,
        "effective_start": effective_date,
        "created_by": actor,
    }
    bonus_rule, bonus_created = KPIBonusRuleSet.objects.get_or_create(
        code="iconic-default",
        version=1,
        defaults=bonus_defaults,
    )
    _ensure_draft_status(
        bonus_rule,
        KPIBonusRuleSet.STATUS_DRAFT,
        "Bonus rule set",
    )
    if not bonus_created:
        _ensure_exact(
            bonus_rule,
            bonus_defaults,
            tuple(bonus_defaults),
            "bonus rule set",
        )

    intelligence_defaults = {
        "name": "Iconic Default Intelligence Rules",
        "effective_start": effective_date,
        "minimum_score": Decimal("70.00"),
        "health_green_threshold": Decimal("85.00"),
        "critical_red_count": 1,
        "decline_percentage": Decimal("5.00"),
        "trend_periods": 3,
        "overdue_days": 7,
        "review_completion_target": Decimal("90.00"),
        "improvement_threshold": Decimal("5.00"),
        "department_risk_threshold": Decimal("70.00"),
        "manager_workload_threshold": 10,
        "overall_kpi_weight": Decimal("40.00"),
        "review_completion_weight": Decimal("20.00"),
        "bonus_readiness_weight": Decimal("15.00"),
        "improvement_weight": Decimal("10.00"),
        "risk_control_weight": Decimal("15.00"),
        "critical_alert_severity": KPIIntelligenceRuleSet.SEVERITY_CRITICAL,
        "red_alert_severity": KPIIntelligenceRuleSet.SEVERITY_HIGH,
        "yellow_alert_severity": KPIIntelligenceRuleSet.SEVERITY_MEDIUM,
        "created_by": actor,
    }
    intelligence, intelligence_created = KPIIntelligenceRuleSet.objects.get_or_create(
        code="iconic-default",
        version=1,
        defaults=intelligence_defaults,
    )
    _ensure_draft_status(
        intelligence,
        KPIIntelligenceRuleSet.STATUS_DRAFT,
        "Intelligence rule set",
    )
    if not intelligence_created:
        _ensure_exact(
            intelligence,
            intelligence_defaults,
            tuple(intelligence_defaults),
            "intelligence rule set",
        )

    notification_defaults = {
        "name": "Iconic Default CRM Notification Rules",
        "effective_start": effective_date,
        "enabled_event_types": list(DEFAULT_NOTIFICATION_EVENTS),
        "reminder_offsets": [7, 3, 1, 0, -1, -7],
        "improvement_reminder_days": [1, 7],
        "enabled_schedules": [
            "daily",
            "weekly",
            "monthly",
            "quarterly",
            "annual",
        ],
        "positive_recognition_enabled": False,
        "retry_limit": 2,
        "batch_size": 100,
        "source_lookback_days": 365,
        "created_by": actor,
    }
    notification, notification_created = KPINotificationRule.objects.get_or_create(
        code="iconic-default",
        version=1,
        defaults=notification_defaults,
    )
    _ensure_draft_status(
        notification,
        KPINotificationRule.STATUS_DRAFT,
        "Notification rule",
    )
    if not notification_created:
        _ensure_exact(
            notification,
            notification_defaults,
            tuple(notification_defaults),
            "notification rule",
        )
    for event_type, stage, offset, severity, scopes in (
        DEFAULT_NOTIFICATION_ESCALATIONS
    ):
        escalation, created = KPIEscalationRule.objects.get_or_create(
            notification_rule=notification,
            event_type=event_type,
            stage=stage,
            defaults={
                "trigger_offset_days": offset,
                "severity": severity,
                "recipient_scopes": list(scopes),
                "is_active": True,
            },
        )
        expected = {
            "trigger_offset_days": offset,
            "severity": severity,
            "recipient_scopes": list(scopes),
            "is_active": True,
        }
        if not created:
            _ensure_exact(
                escalation,
                expected,
                tuple(expected),
                f"{notification} escalation {event_type}/{stage}",
            )

    targets = (
        *template_versions,
        settings,
        weight_profile,
        bonus_rule,
        intelligence,
        notification,
    )
    approvals = tuple(
        create_policy_approval(target, actor=actor)
        for target in targets
    )
    return KPIReleaseDraftSet(
        template_versions=tuple(template_versions),
        kpi_settings=settings,
        bonus_weight_profile=weight_profile,
        bonus_rule_set=bonus_rule,
        intelligence_rule_set=intelligence,
        notification_rule=notification,
        approvals=approvals,
    )


def _resolve_employee(employee):
    employee_id = getattr(employee, "pk", employee)
    resolved = (
        EmployeeProfile.objects.select_related("user")
        .filter(pk=employee_id, is_archived=False, user__is_active=True)
        .first()
    )
    if resolved is None:
        raise ValidationError({"employee": "Select an active employee."})
    return resolved


def _resolve_manager(manager):
    if manager in (None, ""):
        return None
    user_model = EmployeeProfile._meta.get_field("user").remote_field.model
    manager_id = getattr(manager, "pk", manager)
    resolved = user_model.objects.filter(pk=manager_id, is_active=True).first()
    if resolved is None:
        raise ValidationError({"manager": "Select an active manager."})
    return resolved


def _preview_items(template):
    version = (
        template.versions.prefetch_related(
            Prefetch(
                "items",
                queryset=KPIItemDefinition.objects.filter(is_active=True).order_by(
                    "sort_order",
                    "pk",
                ),
            )
        )
        .order_by("-version")
        .first()
    )
    if version is None:
        raise ValidationError(
            {"kpi_template": f"{template.name} has no template version."}
        )
    return version, tuple(
        {
            "name": item.name,
            "weight": item.weight,
            "target": item.target,
            "measurement_method": item.measurement_method,
        }
        for item in version.items.all()
    )


def preview_assignment_plan(*, employee, assignments, start_date, actor):
    _require_executive(actor)
    employee = _resolve_employee(employee)
    if not isinstance(start_date, date):
        raise ValidationError({"start_date": "Enter a valid start date."})
    specs = list(assignments)
    if not specs:
        raise ValidationError({"assignments": "Add at least one KPI role."})
    total = sum(
        (Decimal(str(spec.get("role_weight"))) for spec in specs),
        Decimal("0.00"),
    )
    if total != Decimal("100.00"):
        raise ValidationError(
            {"role_weight": "Draft assignment weights must total exactly 100.00."}
        )
    roles = []
    template_ids = set()
    for spec in specs:
        template_id = getattr(spec.get("kpi_template"), "pk", spec.get("kpi_template"))
        template = KPIRoleTemplate.objects.filter(
            pk=template_id,
            is_active=True,
        ).first()
        if template is None or template.pk in template_ids:
            raise ValidationError(
                {"kpi_template": "Select unique active KPI templates."}
            )
        template_ids.add(template.pk)
        manager = _resolve_manager(spec.get("manager"))
        if manager and manager.pk == employee.user_id:
            raise ValidationError({"manager": "Employee cannot manage their own KPI."})
        version, items = _preview_items(template)
        roles.append(
            {
                "template": template,
                "template_version": version,
                "role_weight": Decimal(str(spec.get("role_weight"))),
                "manager": manager,
                "bonus_eligible": bool(spec.get("bonus_eligible", True)),
                "notes": str(spec.get("notes") or ""),
                "items": items,
            }
        )
    return KPIAssignmentPreview(
        employee=employee,
        start_date=start_date,
        total_weight=total,
        roles=tuple(roles),
    )


@transaction.atomic
def save_assignment_draft(*, employee, assignments, start_date, actor):
    preview = preview_assignment_plan(
        employee=employee,
        assignments=assignments,
        start_date=start_date,
        actor=actor,
    )
    rows = assign_kpi_roles(
        employee=preview.employee,
        assignments=[
            {
                "kpi_template": role["template"],
                "role_weight": role["role_weight"],
                "manager": role["manager"],
                "start_date": preview.start_date,
                "is_active": False,
                "bonus_eligible": role["bonus_eligible"],
                "notes": role["notes"],
            }
            for role in preview.roles
        ],
        actor=actor,
        reason="Stage 10 assignment draft saved",
    )
    return rows


def _published_version_exists(template_id, as_of):
    return KPITemplateVersion.objects.filter(
        template_id=template_id,
        status=KPITemplateVersion.STATUS_PUBLISHED,
        effective_start__lte=as_of,
    ).filter(
        Q(effective_end__isnull=True) | Q(effective_end__gte=as_of)
    ).exists()


@transaction.atomic
def activate_assignment_draft(*, assignment_ids, actor, reason):
    actor = _require_executive(actor)
    reason = _clean_reason(reason, required=True)
    ids = tuple(dict.fromkeys(int(value) for value in assignment_ids))
    drafts = list(
        EmployeeKPIRoleAssignment.objects.select_for_update()
        .select_related("employee", "kpi_template")
        .filter(pk__in=ids)
        .order_by("pk")
    )
    if len(drafts) != len(ids) or not drafts:
        raise ValidationError({"assignments": "Select valid assignment drafts."})
    if len({row.employee_id for row in drafts}) != 1:
        raise ValidationError({"assignments": "Drafts must belong to one employee."})
    if any(row.is_active or row.is_archived for row in drafts):
        raise ValidationError({"assignments": "Only inactive, unarchived drafts qualify."})
    if len({row.start_date for row in drafts}) != 1:
        raise ValidationError({"start_date": "Drafts must share one start date."})
    total = sum((row.role_weight for row in drafts), Decimal("0.00"))
    if total != Decimal("100.00"):
        raise ValidationError({"role_weight": "Draft weights must total 100.00."})
    start_date = drafts[0].start_date
    missing_versions = [
        row.kpi_template.name
        for row in drafts
        if not _published_version_exists(row.kpi_template_id, start_date)
    ]
    if missing_versions:
        raise ValidationError(
            {
                "kpi_template": (
                    "Publish effective template versions before activation: "
                    + ", ".join(missing_versions)
                )
            }
        )

    current = list(
        EmployeeKPIRoleAssignment.objects.select_for_update().filter(
            employee_id=drafts[0].employee_id,
            is_active=True,
            is_archived=False,
        )
    )
    updates = []
    cutoff = start_date - timedelta(days=1)
    for row in current:
        if row.end_date and row.end_date < start_date:
            continue
        changes = (
            {"end_date": cutoff}
            if row.start_date < start_date
            else {"is_active": False}
        )
        updates.append({"assignment": row, "changes": changes})
    updates.extend(
        {"assignment": row, "changes": {"is_active": True}}
        for row in drafts
    )
    return update_assignments(
        updates=updates,
        actor=actor,
        reason=reason,
    )
